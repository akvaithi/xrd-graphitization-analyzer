"""
xrd_analyzer.py — XRD Degree of Graphitization Calculator (NETL Standard)

Parses a two-column .xy diffraction file, fits a bimodal *Doublet* Pseudo-Voigt
to the carbon (002) peak region (22°–30° 2θ), and computes DG% via the
Maire-Mering equation.

Each modelled phase is a Cu Kα1/Kα2 *doublet*: a primary Kα1 peak plus a
Kα2 "shadow" whose position follows Bragg's Law and whose amplitude is scaled
by the instrument's Kα2/Kα1 intensity ratio. Hardware wavelengths can be read
from a Bruker .brml archive; otherwise standard Cu defaults are used.

All core functions are importable; CLI/output logic is isolated at the bottom.

Usage:
    python xrd_analyzer.py sample.xy
    python xrd_analyzer.py sample.xy --json
    python xrd_analyzer.py sample.xy --brml sample.brml --json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
from scipy.optimize import curve_fit

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

LAMBDA_CU_KA: float = 1.5406   # Å  — Cu Kα1 wavelength (primary)
D_GRAPHITE: float = 3.354       # Å  — ideal crystalline graphite d₀₀₂
D_TURBOSTRATIC: float = 3.440   # Å  — fully disordered turbostratic carbon

# Default hardware metadata (used when no .brml is supplied)
DEFAULT_ALPHA1: float = 1.5406   # Å  — Cu Kα1
DEFAULT_ALPHA2: float = 1.54439  # Å  — Cu Kα2
DEFAULT_RATIO:  float = 0.5      # Kα2/Kα1 intensity ratio

# 2θ window that brackets the carbon (002) reflection
WINDOW_LOW: float = 22.0        # degrees
WINDOW_HIGH: float = 30.0       # degrees

# Bruker .brml tag names whose `Value` attribute we extract
_BRML_WANTED = ("WaveLengthAlpha1", "WaveLengthAlpha2", "WaveLengthRatio")


# ---------------------------------------------------------------------------
# Core mathematical functions  (importable)
# ---------------------------------------------------------------------------

def pseudo_voigt(
    x: np.ndarray,
    amplitude: float,
    center: float,
    sigma: float,
    eta: float,
) -> np.ndarray:
    """
    Single Pseudo-Voigt profile: η·Lorentzian + (1−η)·Gaussian.

    Parameters
    ----------
    x         : independent variable (2θ in degrees)
    amplitude : peak height
    center    : peak position (2θ)
    sigma     : half-width parameter (shared between both components)
    eta       : mixing fraction ∈ [0, 1]  (0 = pure Gaussian, 1 = pure Lorentzian)
    """
    dx = x - center
    gaussian = np.exp(-(dx ** 2) / (2.0 * sigma ** 2))
    lorentzian = 1.0 / (1.0 + (dx / sigma) ** 2)
    return amplitude * (eta * lorentzian + (1.0 - eta) * gaussian)


def bimodal_pseudo_voigt(
    x: np.ndarray,
    amp1: float, cen1: float, sig1: float, eta1: float,
    amp2: float, cen2: float, sig2: float, eta2: float,
) -> np.ndarray:
    """
    Sum of two *single* Pseudo-Voigt profiles (Kα1 primaries only).

    Retained for visualising the deconvolved Kα1 components. The fit itself
    uses the doublet model below.
    """
    return (pseudo_voigt(x, amp1, cen1, sig1, eta1) +
            pseudo_voigt(x, amp2, cen2, sig2, eta2))


def kalpha2_shadow_center(center1_2theta: float, lam1: float, lam2: float) -> float:
    """
    Dynamic Kα2 shadow position from Bragg's Law.

    The recorded centre is 2θ; Bragg operates on θ. With
        sin θ₂ = (λ₂ / λ₁) · sin θ₁
    the shadow's 2θ position is 2·θ₂.

    Parameters
    ----------
    center1_2theta : primary (Kα1) peak position in degrees (2θ)
    lam1, lam2     : Kα1 and Kα2 wavelengths (Å)
    """
    theta1 = np.deg2rad(center1_2theta / 2.0)
    sin_theta2 = (lam2 / lam1) * np.sin(theta1)
    # Guard against floating-point excursions just past ±1
    sin_theta2 = np.clip(sin_theta2, -1.0, 1.0)
    theta2 = np.arcsin(sin_theta2)
    return 2.0 * np.rad2deg(theta2)


def doublet_pseudo_voigt(
    x: np.ndarray,
    amplitude: float,
    center: float,
    sigma: float,
    eta: float,
    lam1: float,
    lam2: float,
    ratio: float,
) -> np.ndarray:
    """
    Kα1/Kα2 doublet: a primary Pseudo-Voigt plus its Kα2 shadow.

    • Primary peak  : at ``center`` (2θ), amplitude ``amplitude``.
    • Kα2 shadow    : centre from :func:`kalpha2_shadow_center`, amplitude
                      scaled by ``ratio``; shares the primary's σ and η.

    All parameters except the wavelengths/ratio are the fit's free variables;
    the doublet relationship is enforced analytically (not fitted), so the
    shadow does not add free parameters.
    """
    primary = pseudo_voigt(x, amplitude, center, sigma, eta)
    center2 = kalpha2_shadow_center(center, lam1, lam2)
    shadow = pseudo_voigt(x, amplitude * ratio, center2, sigma, eta)
    return primary + shadow


def make_bimodal_doublet(meta: dict):
    """
    Build the bimodal-doublet model used by ``curve_fit``.

    Returns a function ``model(x, amp1, cen1, sig1, eta1, amp2, cen2, sig2,
    eta2)`` that sums a graphitic doublet and a turbostratic doublet. The
    wavelengths and Kα2/Kα1 ratio from ``meta`` are baked in via closure so
    the optimiser still sees only the eight physical shape parameters.
    """
    lam1 = meta["wavelength_alpha1"]
    lam2 = meta["wavelength_alpha2"]
    ratio = meta["wavelength_ratio"]

    def model(
        x: np.ndarray,
        amp1: float, cen1: float, sig1: float, eta1: float,
        amp2: float, cen2: float, sig2: float, eta2: float,
    ) -> np.ndarray:
        return (
            doublet_pseudo_voigt(x, amp1, cen1, sig1, eta1, lam1, lam2, ratio) +
            doublet_pseudo_voigt(x, amp2, cen2, sig2, eta2, lam1, lam2, ratio)
        )

    return model


# ---------------------------------------------------------------------------
# Origin PsdVoigt model  (NETL-faithful, area-normalised, with baseline)
# ---------------------------------------------------------------------------

def origin_pseudo_voigt(
    x: np.ndarray,
    area: float,
    center: float,
    fwhm: float,
    mu: float,
) -> np.ndarray:
    """
    OriginLab **PsdVoigt1** profile (without the y0 baseline term).

    Exactly the equation NETL uses in Origin:

        A·( μ·(2/π)·(w / (4·(x−xc)² + w²))
            + (1−μ)·(√(4·ln2) / (√π·w))·exp(−(4·ln2/w²)·(x−xc)²) )

    Both components are **unit-area-normalised**, so ``area`` is the integrated
    peak area directly and ``w`` is a true FWHM shared by both components.

    Parameters
    ----------
    area   : integrated peak area  (Origin's A)
    center : peak position 2θ       (Origin's xc)
    fwhm   : full width at half max  (Origin's w), shared Gaussian/Lorentzian
    mu     : Lorentzian fraction ∈ [0, 1]  (Origin's mu)
    """
    dx = x - center
    ln2 = np.log(2.0)
    lorentzian = (2.0 / np.pi) * (fwhm / (4.0 * dx ** 2 + fwhm ** 2))
    gaussian = (np.sqrt(4.0 * ln2) / (np.sqrt(np.pi) * fwhm)) * \
        np.exp(-(4.0 * ln2 / fwhm ** 2) * dx ** 2)
    return area * (mu * lorentzian + (1.0 - mu) * gaussian)


def make_bimodal_origin():
    """
    Build the NETL Origin **PsdVoigt2** model: two area-normalised Pseudo-Voigt
    peaks sharing a single ``y0`` baseline.

    Returns ``model(x, A1, xc1, w1, mu1, A2, xc2, w2, mu2, y0)`` (nine free
    parameters). This is a plain pseudo-Voigt sum with **no Kα2 doublet**, to
    faithfully reproduce NETL's Origin fitting procedure (which carries no
    wavelength terms in the line shape).
    """
    def model(
        x: np.ndarray,
        amp1: float, cen1: float, w1: float, mu1: float,
        amp2: float, cen2: float, w2: float, mu2: float,
        y0: float,
    ) -> np.ndarray:
        return (
            y0
            + origin_pseudo_voigt(x, amp1, cen1, w1, mu1)
            + origin_pseudo_voigt(x, amp2, cen2, w2, mu2)
        )

    return model


# Names of the supported fitting models (first entry is the CLI/GUI default)
MODELS = ("legacy", "origin")


def integrated_area(amplitude: float, sigma: float, eta: float) -> float:
    """
    Closed-form integrated area of a Pseudo-Voigt peak.

    Derived from individual component integrals over (−∞, +∞):
      Gaussian   integral = amplitude · σ · √(2π)
      Lorentzian integral = amplitude · σ · π
    Combined:
      A = amplitude · σ · [(1−η)·√(2π) + η·π]
    """
    return amplitude * sigma * ((1.0 - eta) * np.sqrt(2.0 * np.pi) + eta * np.pi)


def bragg_d_spacing(two_theta_deg: float, wavelength: float = LAMBDA_CU_KA) -> float:
    """
    Bragg's Law: d = λ / (2·sin θ)

    Parameters
    ----------
    two_theta_deg : peak position in degrees (2θ, as recorded by diffractometer)
    wavelength    : X-ray wavelength in Å (defaults to Cu Kα1). d-spacings are
                    always computed from the Kα1 line only.

    Returns
    -------
    d-spacing in Ångströms.
    """
    theta_rad = np.deg2rad(two_theta_deg / 2.0)
    return wavelength / (2.0 * np.sin(theta_rad))


# ---------------------------------------------------------------------------
# Hardware metadata  (importable)
# ---------------------------------------------------------------------------

def default_metadata() -> dict:
    """Return the default Cu Kα hardware metadata used when no .brml is given."""
    return {
        "wavelength_alpha1": DEFAULT_ALPHA1,
        "wavelength_alpha2": DEFAULT_ALPHA2,
        "wavelength_ratio":  DEFAULT_RATIO,
        "metadata_source":   "default",
    }


def parse_brml_metadata(brml_path: str | Path) -> dict:
    """
    Extract Kα1/Kα2 wavelengths and ratio from a Bruker .brml archive.

    A .brml file is a ZIP container. We open it with :mod:`zipfile`, locate
    ``MeasurementContainer.xml`` in memory, parse it with ElementTree, and read
    the ``Value`` attribute of the ``WaveLengthAlpha1``, ``WaveLengthAlpha2``
    and ``WaveLengthRatio`` elements. XML namespaces are ignored by matching on
    each element's local tag name. Any field absent from the file falls back to
    its Cu default.

    Returns
    -------
    dict with wavelength_alpha1/alpha2, wavelength_ratio, metadata_source="brml".

    Raises
    ------
    FileNotFoundError : if the .brml path does not exist
    ValueError        : if it is not a valid zip or lacks MeasurementContainer.xml
    """
    if not Path(brml_path).exists():
        # 3-arg form populates exc.filename so the CLI reports the right path
        raise FileNotFoundError(2, "No such file or directory", str(brml_path))

    with open(brml_path, "rb") as fh:
        return parse_brml_bytes(fh.read(), label=str(brml_path))


def parse_brml_bytes(data: bytes, label: str = "uploaded .brml") -> dict:
    """
    Same as :func:`parse_brml_metadata` but operates on the raw bytes of a
    .brml archive — used by the web GUI, which receives the file as an upload
    rather than a path on disk.

    Parameters
    ----------
    data  : the complete bytes of the .brml (ZIP) archive
    label : name used in error messages

    Raises
    ------
    ValueError : if it is not a valid zip or lacks MeasurementContainer.xml
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"'{label}' is not a valid .brml (zip) archive.") from exc

    with archive:
        # Locate MeasurementContainer.xml anywhere in the archive tree
        target = next(
            (n for n in archive.namelist()
             if n.rsplit("/", 1)[-1].lower() == "measurementcontainer.xml"),
            None,
        )
        if target is None:
            raise ValueError(
                f"'{label}' does not contain a MeasurementContainer.xml entry."
            )
        with archive.open(target) as fh:
            root = ET.parse(fh).getroot()

    # Start from defaults; override with any values found in the XML
    found: dict[str, float] = {}
    for elem in root.iter():
        local = elem.tag.rsplit("}", 1)[-1]   # strip any {namespace}
        if local in _BRML_WANTED and local not in found:
            raw = elem.get("Value")
            if raw is not None:
                try:
                    found[local] = float(raw)
                except ValueError:
                    pass

    return {
        "wavelength_alpha1": found.get("WaveLengthAlpha1", DEFAULT_ALPHA1),
        "wavelength_alpha2": found.get("WaveLengthAlpha2", DEFAULT_ALPHA2),
        "wavelength_ratio":  found.get("WaveLengthRatio",  DEFAULT_RATIO),
        "metadata_source":   "brml",
    }


# ---------------------------------------------------------------------------
# I/O helpers  (importable)
# ---------------------------------------------------------------------------

def load_xy_file(filepath: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """
    Read a standard .xy file (two whitespace-separated columns: 2θ, Intensity).

    Lines that are blank, start with #/!/'/letter, or cannot be parsed as two
    floats are silently skipped — this tolerates common header formats.

    Returns
    -------
    two_theta  : 1-D array of 2θ values (degrees)
    intensity  : 1-D array of intensity counts
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    try:
        return parse_xy_text(text)
    except ValueError:
        # Re-raise with the filename for a friendlier message
        raise ValueError(f"No numeric data found in '{filepath}'.")


def parse_xy_text(text: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Parse the contents of a .xy file (as a string) into two arrays.

    Identical tolerant rules as :func:`load_xy_file`, but operates on an
    in-memory string — used by the web GUI for uploaded file content so the
    parsing logic lives in exactly one place.
    """
    two_theta: list[float] = []
    intensity: list[float] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip comment / header tokens
        if stripped[0] in ("#", "!", "'") or stripped[0].isalpha():
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        try:
            two_theta.append(float(parts[0]))
            intensity.append(float(parts[1]))
        except ValueError:
            continue

    if not two_theta:
        raise ValueError("No numeric data found in input.")

    return np.array(two_theta, dtype=float), np.array(intensity, dtype=float)


def filter_window(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    low: float = WINDOW_LOW,
    high: float = WINDOW_HIGH,
) -> tuple[np.ndarray, np.ndarray]:
    """Return only the data points within [low, high] degrees 2θ."""
    mask = (two_theta >= low) & (two_theta <= high)
    return two_theta[mask], intensity[mask]


# ---------------------------------------------------------------------------
# Fitting & calculation pipeline  (importable)
# ---------------------------------------------------------------------------

def fit_bimodal(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    meta: dict | None = None,
    model: str = "legacy",
) -> tuple[np.ndarray, np.ndarray]:
    """
    Fit a bimodal Pseudo-Voigt to the filtered (002) window.

    Two models are available (selected by ``model``):

    ``"legacy"``  — our Kα1/Kα2 *doublet* Pseudo-Voigt (height-parametrised, no
        baseline). Eight free parameters:
        ``[amp1, cen1, sig1, eta1, amp2, cen2, sig2, eta2]``. Each phase carries
        an analytic Kα2 shadow from the wavelengths in ``meta``.

    ``"origin"`` — NETL's OriginLab **PsdVoigt2** (area-normalised, shared FWHM,
        shared ``y0`` baseline, no Kα2). Nine free parameters:
        ``[A1, xc1, w1, mu1, A2, xc2, w2, mu2, y0]``.

    In both models peak 1 is the narrow *graphitic* phase and peak 2 the broad
    *turbostratic* phase. Initial guesses and bounds keep the optimiser in
    physically meaningful space.

    Parameters
    ----------
    meta  : hardware metadata dict (see :func:`default_metadata`). Falls back to
            Cu defaults when ``None``. Used by the legacy doublet only.
    model : ``"legacy"`` or ``"origin"``.

    Returns
    -------
    popt : best-fit parameter vector (8 for legacy, 9 for origin)
    pcov : estimated covariance matrix
    """
    if meta is None:
        meta = default_metadata()
    if model not in MODELS:
        raise ValueError(f"unknown model '{model}'; choose from {MODELS}.")

    peak_height = float(np.max(intensity))

    if model == "legacy":
        fn = make_bimodal_doublet(meta)
        # [amp1, cen1, sig1, eta1,   amp2, cen2, sig2, eta2]
        p0 = [
            peak_height * 0.70, 26.5, 0.30, 0.5,   # graphitic (narrow)
            peak_height * 0.40, 26.0, 0.80, 0.5,   # turbostratic (broad)
        ]
        lower = [0.0, 24.0, 0.05, 0.0,   0.0, 23.0, 0.10, 0.0]
        upper = [np.inf, 28.0, 2.00, 1.0,   np.inf, 27.5, 3.00, 1.0]
    else:  # origin (PsdVoigt2)
        fn = make_bimodal_origin()
        baseline = float(np.min(intensity))
        # Area ≈ height · FWHM (rough; bounds are generous). Layout:
        # [A1, xc1, w1, mu1,   A2, xc2, w2, mu2,   y0]
        p0 = [
            peak_height * 0.30, 26.5, 0.30, 0.5,   # graphitic (narrow)
            peak_height * 0.80, 26.0, 0.80, 0.8,   # turbostratic (broad)
            baseline,
        ]
        lower = [0.0, 24.0, 0.02, 0.0,   0.0, 23.0, 0.05, 0.0,   0.0]
        upper = [np.inf, 28.0, 2.00, 1.0,   np.inf, 27.5, 3.00, 1.0,   peak_height]

    popt, pcov = curve_fit(
        fn,
        two_theta,
        intensity,
        p0=p0,
        bounds=(lower, upper),
        maxfev=20_000,
    )
    return popt, pcov


def calculate_dg(
    popt: np.ndarray,
    meta: dict | None = None,
    model: str = "legacy",
) -> dict:
    """
    Derive all intermediate values and the final DG% from fitted parameters.

    Works for both fitting models (see :func:`fit_bimodal`). The downstream
    NETL pipeline is identical regardless of model:

    1. Obtain the integrated area of each phase's graphitic/turbostratic peak.
       • legacy: analytically integrate the Kα1 Pseudo-Voigt via
         :func:`integrated_area`.
       • origin: the fitted ``A`` *is* the area (area-normalised PsdVoigt).
    2. Area fractions  X_g = A_g/(A_g+A_t),  X_t = 1−X_g.
    3. d-spacings from the peak centres via Bragg's Law, Kα1 wavelength only.
    4. Weighted d-spacing  d′ = X_g·d_g + X_t·d_t.
    5. Maire-Mering  DG% = (3.440 − d′) / (3.440 − 3.354) × 100.

    Parameters
    ----------
    meta  : hardware metadata dict; d-spacings use ``meta['wavelength_alpha1']``.
            Falls back to Cu defaults when ``None``.
    model : ``"legacy"`` or ``"origin"`` — must match the model that produced
            ``popt``.

    Returns
    -------
    Flat dict with the applied metadata, the chosen model, all intermediate
    values and the final DG%. Shape-parameter keys are model-specific.
    """
    if meta is None:
        meta = default_metadata()
    lam1 = meta["wavelength_alpha1"]

    # --- Step 1: per-model peak areas, centres and shape parameters ---
    model_fields: dict = {}
    if model == "legacy":
        amp1, cen1, sig1, eta1, amp2, cen2, sig2, eta2 = popt
        A_g = integrated_area(amp1, sig1, eta1)
        A_t = integrated_area(amp2, sig2, eta2)
        model_fields = {
            "graphitic_sigma_deg":                 round(sig1, 4),
            "turbostratic_sigma_deg":              round(sig2, 4),
            "graphitic_eta":                       round(eta1, 4),
            "turbostratic_eta":                    round(eta2, 4),
            "graphitic_kalpha2_center_2theta_deg":
                round(kalpha2_shadow_center(cen1, lam1, meta["wavelength_alpha2"]), 4),
            "turbostratic_kalpha2_center_2theta_deg":
                round(kalpha2_shadow_center(cen2, lam1, meta["wavelength_alpha2"]), 4),
        }
    else:  # origin (PsdVoigt2): A is the area directly
        A_g, cen1, w1, mu1, A_t, cen2, w2, mu2, y0 = popt
        model_fields = {
            "graphitic_fwhm_deg":     round(w1, 4),
            "turbostratic_fwhm_deg":  round(w2, 4),
            "graphitic_mu":           round(mu1, 4),
            "turbostratic_mu":        round(mu2, 4),
            "baseline_y0":            round(float(y0), 6),
        }

    A_total = A_g + A_t

    # --- Steps 2-5: shared NETL pipeline ---
    X_g = A_g / A_total
    X_t = A_t / A_total
    d_g = bragg_d_spacing(cen1, lam1)
    d_t = bragg_d_spacing(cen2, lam1)
    d_prime = X_g * d_g + X_t * d_t
    DG_percent = ((D_TURBOSTRATIC - d_prime) / (D_TURBOSTRATIC - D_GRAPHITE)) * 100.0

    return {
        # --- model + parsed/applied hardware metadata ---
        "model":                               model,
        "wavelength_alpha1":                   round(meta["wavelength_alpha1"], 6),
        "wavelength_alpha2":                   round(meta["wavelength_alpha2"], 6),
        "wavelength_ratio":                    round(meta["wavelength_ratio"], 6),
        "metadata_source":                     meta.get("metadata_source", "default"),
        # --- fitted peak centres ---
        "graphitic_peak_center_2theta_deg":    round(cen1, 4),
        "turbostratic_peak_center_2theta_deg": round(cen2, 4),
        # --- model-specific shape parameters ---
        **model_fields,
        # --- areas & fractions ---
        "graphitic_integrated_area":           round(A_g, 4),
        "turbostratic_integrated_area":        round(A_t, 4),
        "graphitic_area_fraction":             round(X_g, 6),
        "turbostratic_area_fraction":          round(X_t, 6),
        # --- d-spacings & final DG% ---
        "d_spacing_graphitic_angstrom":        round(d_g, 6),
        "d_spacing_turbostratic_angstrom":     round(d_t, 6),
        "d_spacing_weighted_angstrom":         round(d_prime, 6),
        "DG_percent":                          round(DG_percent, 2),
    }


def analyze_xrd_file(
    filepath: str | Path,
    brml_path: str | Path | None = None,
    model: str = "legacy",
) -> dict:
    """
    Full analysis pipeline: load → (read .brml) → window-filter → fit → DG%.

    This is the primary public API for programmatic use.

    Parameters
    ----------
    filepath  : path to the two-column .xy data file
    brml_path : optional Bruker .brml archive supplying Kα1/Kα2 wavelengths and
                the Kα2/Kα1 ratio. When ``None``, Cu defaults are used.
    model     : ``"legacy"`` (doublet Pseudo-Voigt) or ``"origin"`` (NETL
                PsdVoigt2). See :func:`fit_bimodal`.

    Returns
    -------
    Results dict from :func:`calculate_dg` (includes the applied metadata).

    Raises
    ------
    FileNotFoundError : if the .xy or .brml file does not exist
    ValueError        : bad .brml, unknown model, or too few points in the window
    RuntimeError      : if scipy curve_fit fails to converge
    """
    meta = parse_brml_metadata(brml_path) if brml_path else default_metadata()
    two_theta, intensity = load_xy_file(filepath)
    results, _popt, _tw, _iw = fit_and_report(two_theta, intensity, meta, model)
    return results


def fit_and_report(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    meta: dict | None = None,
    model: str = "legacy",
) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    """
    Window → fit → calculate, returning everything a caller needs to plot.

    Shared by the CLI and the web GUI so the pipeline lives in one place.

    Parameters
    ----------
    meta  : hardware metadata dict (see :func:`default_metadata`). Falls back to
            Cu defaults when ``None``.
    model : ``"legacy"`` or ``"origin"`` (see :func:`fit_bimodal`).

    Returns
    -------
    results : dict from :func:`calculate_dg`
    popt    : fitted parameter vector (8 for legacy, 9 for origin)
    tw, iw  : the windowed 2θ and intensity arrays actually fitted

    Raises
    ------
    ValueError   : if the windowed data has too few points to fit
    RuntimeError : if scipy curve_fit fails to converge
    """
    if meta is None:
        meta = default_metadata()

    tw, iw = filter_window(two_theta, intensity)

    if len(tw) < 8:
        raise ValueError(
            f"Only {len(tw)} data point(s) found in the [{WINDOW_LOW}°,"
            f" {WINDOW_HIGH}°] window. Verify the file covers the (002) peak region."
        )

    popt, _ = fit_bimodal(tw, iw, meta, model)
    results = calculate_dg(popt, meta, model)
    return results, popt, tw, iw


# ---------------------------------------------------------------------------
# Batch processing  (importable)
# ---------------------------------------------------------------------------

def expand_xy_inputs(paths: list[str]) -> list[str]:
    """
    Expand a list of CLI path arguments into a flat list of .xy file paths.

    Each entry may be a file (kept as-is) or a directory (expanded to its
    ``*.xy`` files, sorted). Shell globs are already expanded by the shell, so
    they arrive here as individual files.
    """
    files: list[str] = []
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            files.extend(str(x) for x in sorted(pth.glob("*.xy")))
        else:
            files.append(str(p))
    return files


def analyze_batch(
    filepaths: list[str],
    brml_path: str | Path | None = None,
    model: str = "legacy",
) -> list[dict]:
    """
    Run :func:`analyze_xrd_file` over many .xy files, one shared .brml/model.

    A failure on one file does not abort the batch — its entry carries an
    ``"error"`` key instead of results. Every entry begins with ``"file"``.

    Returns
    -------
    list of dicts, one per input file, in input order.
    """
    results: list[dict] = []
    for fp in filepaths:
        entry: dict = {"file": str(fp)}
        try:
            entry.update(analyze_xrd_file(fp, brml_path, model))
        except FileNotFoundError as exc:
            entry["error"] = f"file not found — '{exc.filename or fp}'"
        except ValueError as exc:
            entry["error"] = str(exc)
        except RuntimeError as exc:
            entry["error"] = f"curve fitting did not converge — {exc}"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"unexpected error — {exc}"
        results.append(entry)
    return results


# Model-agnostic columns written by the CSV exporter
_CSV_COLUMNS = (
    "file", "model", "metadata_source", "wavelength_alpha1",
    "graphitic_peak_center_2theta_deg", "turbostratic_peak_center_2theta_deg",
    "graphitic_area_fraction", "turbostratic_area_fraction",
    "d_spacing_graphitic_angstrom", "d_spacing_turbostratic_angstrom",
    "d_spacing_weighted_angstrom", "DG_percent", "error",
)


def write_batch_csv(results: list[dict], fh) -> None:
    """Write batch results as CSV (core, model-agnostic columns) to ``fh``."""
    writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in results:
        writer.writerow(row)


# ---------------------------------------------------------------------------
# CLI — output & argument parsing  (not imported by downstream tools)
# ---------------------------------------------------------------------------

def _print_human_readable(results: dict, filepath: str | Path) -> None:
    """Render a formatted, human-readable report to stdout."""
    SEP = "=" * 56
    print(SEP)
    print("  XRD Degree of Graphitization Analysis  (NETL Standard)")
    print(f"  File : {filepath}")
    print(SEP)

    model = results.get("model", "legacy")
    model_label = ("legacy (Kα1/Kα2 doublet Pseudo-Voigt)" if model == "legacy"
                   else "origin (NETL PsdVoigt2, area-normalised + baseline)")
    print(f"\n  Fitting Model : {model_label}")

    src = results["metadata_source"]
    src_label = "Bruker .brml" if src == "brml" else "Cu default"
    print(f"\n  Hardware Metadata  ({src_label})")
    print(f"    λ Kα1               : {results['wavelength_alpha1']:>10.6f} Å")
    print(f"    λ Kα2               : {results['wavelength_alpha2']:>10.6f} Å")
    print(f"    Kα2/Kα1 ratio       : {results['wavelength_ratio']:>10.6f}")

    print("\n  Peak Positions (2θ)")
    if model == "legacy":
        print(f"    Graphitic  (narrow) : {results['graphitic_peak_center_2theta_deg']:>8.4f} °  "
              f"(Kα2 shadow {results['graphitic_kalpha2_center_2theta_deg']:.4f} °)")
        print(f"    Turbostratic (broad): {results['turbostratic_peak_center_2theta_deg']:>8.4f} °  "
              f"(Kα2 shadow {results['turbostratic_kalpha2_center_2theta_deg']:.4f} °)")
        print("\n  Peak Shape Parameters")
        print(f"    σ_graphitic         : {results['graphitic_sigma_deg']:>8.4f} °  "
              f"(η = {results['graphitic_eta']:.4f})")
        print(f"    σ_turbostratic      : {results['turbostratic_sigma_deg']:>8.4f} °  "
              f"(η = {results['turbostratic_eta']:.4f})")
    else:  # origin
        print(f"    Graphitic  (narrow) : {results['graphitic_peak_center_2theta_deg']:>8.4f} °")
        print(f"    Turbostratic (broad): {results['turbostratic_peak_center_2theta_deg']:>8.4f} °")
        print("\n  Peak Shape Parameters  (Origin PsdVoigt)")
        print(f"    w_graphitic (FWHM)  : {results['graphitic_fwhm_deg']:>8.4f} °  "
              f"(μ = {results['graphitic_mu']:.4f})")
        print(f"    w_turbostratic(FWHM): {results['turbostratic_fwhm_deg']:>8.4f} °  "
              f"(μ = {results['turbostratic_mu']:.4f})")
        print(f"    baseline y0         : {results['baseline_y0']:>10.6f}")

    print(f"\n  d-Spacings via Bragg's Law  (Kα1 λ = {results['wavelength_alpha1']:.5f} Å)")
    print(f"    d_graphitic         : {results['d_spacing_graphitic_angstrom']:>10.6f} Å")
    print(f"    d_turbostratic      : {results['d_spacing_turbostratic_angstrom']:>10.6f} Å")
    print(f"    d_weighted  (d′)    : {results['d_spacing_weighted_angstrom']:>10.6f} Å")

    print("\n  Integrated Areas")
    print(f"    A_graphitic         : {results['graphitic_integrated_area']:>14.4f}  "
          f"(X_g = {results['graphitic_area_fraction']:.2%})")
    print(f"    A_turbostratic      : {results['turbostratic_integrated_area']:>14.4f}  "
          f"(X_t = {results['turbostratic_area_fraction']:.2%})")

    print()
    print(SEP)
    print(f"  Degree of Graphitization  DG% = {results['DG_percent']:>7.2f} %")
    print(SEP)


def _print_batch_table(results: list[dict]) -> None:
    """Render a compact one-row-per-file summary table to stdout."""
    SEP = "=" * 72
    print(SEP)
    print("  XRD Degree of Graphitization — Batch Summary  (NETL Standard)")
    print(SEP)
    print(f"  {'File':<40}{'Model':<9}{'Src':<8}{'DG%':>8}")
    print("  " + "-" * 68)

    ok = 0
    for r in results:
        name = Path(r["file"]).name
        if len(name) > 38:
            name = name[:35] + "..."
        if "error" in r:
            print(f"  {name:<40}{'—':<9}{'—':<8}{'ERROR':>8}")
            print(f"      ↳ {r['error']}")
        else:
            ok += 1
            print(f"  {name:<40}{r['model']:<9}"
                  f"{r['metadata_source']:<8}{r['DG_percent']:>8.2f}")

    print("  " + "-" * 68)
    print(f"  {ok}/{len(results)} file(s) analyzed successfully.")
    print(SEP)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xrd_analyzer",
        description="Calculate DG% from an XRD .xy file (NETL standard, bimodal Pseudo-Voigt).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python xrd_analyzer.py sample.xy\n"
            "  python xrd_analyzer.py sample.xy --json\n"
            "  python xrd_analyzer.py *.xy --csv results.csv\n"
            "  python xrd_analyzer.py data_dir/ --model origin --json\n"
        ),
    )
    parser.add_argument(
        "filepaths",
        nargs="+",
        metavar="FILE_OR_DIR",
        help=(
            "One or more two-column .xy files, and/or directories (each "
            "directory is expanded to its *.xy files). Multiple inputs trigger "
            "batch mode."
        ),
    )
    parser.add_argument(
        "--brml",
        dest="brml_path",
        default=None,
        metavar="FILE.brml",
        help=(
            "Optional Bruker .brml archive. Reads WaveLengthAlpha1/Alpha2 and "
            "WaveLengthRatio from its MeasurementContainer.xml to drive the "
            "Kα1/Kα2 doublet fit. Defaults: 1.5406, 1.54439, 0.5."
        ),
    )
    parser.add_argument(
        "--model",
        choices=MODELS,
        default="legacy",
        help=(
            "Curve-fitting model. 'legacy' (default): our Kα1/Kα2 doublet "
            "Pseudo-Voigt. 'origin': NETL's OriginLab PsdVoigt2 "
            "(area-normalised, shared FWHM and y0 baseline, no Kα2)."
        ),
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        default=None,
        metavar="FILE",
        help=(
            "Write results as CSV to FILE (use '-' for stdout). Works for one "
            "or many files; one row per input."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit JSON instead of human-readable text: a single object for one "
            "file, or an array of per-file objects in batch mode."
        ),
    )
    return parser


def _emit_csv(results: list[dict], csv_path: str) -> None:
    """Write CSV to a file path, or to stdout when path is '-'."""
    if csv_path == "-":
        write_batch_csv(results, sys.stdout)
    else:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            write_batch_csv(results, fh)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    files = expand_xy_inputs(args.filepaths)
    if not files:
        msg = "no .xy files found in the given path(s)."
        if args.json_output:
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    # ----- single-file mode: keep the original rich output -----
    if len(files) == 1 and not args.csv_path:
        filepath = files[0]

        def _emit_error(msg: str) -> None:
            if args.json_output:
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)

        try:
            results = analyze_xrd_file(filepath, args.brml_path, args.model)
        except FileNotFoundError as exc:
            _emit_error(f"file not found — '{exc.filename or filepath}'")
            sys.exit(1)
        except ValueError as exc:
            _emit_error(str(exc))
            sys.exit(1)
        except RuntimeError as exc:
            _emit_error(f"curve fitting did not converge — {exc}")
            sys.exit(1)
        except Exception as exc:  # noqa: BLE001
            _emit_error(f"unexpected error — {exc}")
            sys.exit(1)

        if args.json_output:
            print(json.dumps(results, indent=2))
        else:
            _print_human_readable(results, filepath)
        return

    # ----- batch mode (multiple files and/or --csv) -----
    results = analyze_batch(files, args.brml_path, args.model)

    if args.csv_path:
        _emit_csv(results, args.csv_path)
    if args.json_output:
        print(json.dumps(results, indent=2))
    elif not args.csv_path:
        _print_batch_table(results)
    elif args.csv_path != "-":
        # CSV written to a file with no other stdout output — confirm on stderr
        succeeded = sum(1 for r in results if "error" not in r)
        print(f"Wrote {succeeded}/{len(results)} result(s) to {args.csv_path}",
              file=sys.stderr)

    # Exit non-zero only if nothing succeeded
    if all("error" in r for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
