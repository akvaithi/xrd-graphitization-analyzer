"""
xrd_analyzer.py — Object-oriented XRD Degree of Graphitization analyzer.

Analyses XRD ``.xy`` patterns of synthetic graphite and computes the Degree of
Graphitization (DG%) of the carbon (002) reflection by two selectable methods:

  Method A — NETL paper standard
      Bimodal Pseudo-Voigt deconvolution (graphitic + turbostratic), Bragg
      d-spacings, area fractions, weighted d′, and the Maire-Mering equation.

  Method B — OriginLab PsdVoigt1 (XRD ppt) + NETL
      Linear baseline subtraction (24°–27.5°), then BOTH a single-peak (legacy)
      and a dual-peak (NETL) fit using the exact OriginLab PsdVoigt1 line shape
      with strict bounds. Reports the DG% overestimation of the legacy
      single-peak fit and the crystallite stacking height Lc (Scherrer).

Dependencies: numpy, scipy.  CLI at the bottom; everything above is importable.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import warnings
from pathlib import Path

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit

# ---------------------------------------------------------------------------
# Configuration / physical constants
# ---------------------------------------------------------------------------

DEFAULT_WAVELENGTH: float = 1.54187   # Å — NETL standard Cu Kα (weighted)
D_GRAPHITE: float = 3.354             # Å — ideal graphite d₀₀₂
D_TURBOSTRATIC: float = 3.440         # Å — fully turbostratic carbon
SCHERRER_K: float = 0.89              # Scherrer shape factor for Lc

# Analysis / baseline window for the (002) reflection
ANALYSIS_WINDOW: tuple[float, float] = (24.0, 27.5)

# Peak-centre bounds for the standard pipeline.
#   graphitic : the sharp (002) peak, ~26.3–26.8°
#   turbostratic : the broad disordered band, kept ≤ 26.1° (NETL places it in
#                  the low-angle shoulder; capping here keeps it off the
#                  graphitic peak and matches the reference fits).
GRAPH_XC_BOUNDS: tuple[float, float] = (26.3, 26.8)
TURBO_XC_BOUNDS: tuple[float, float] = (25.1, 26.1)


class FitError(Exception):
    """Raised when a curve fit fails (e.g. high amorphous content)."""


# ---------------------------------------------------------------------------
# Line-shape model — OriginLab PsdVoigt1 (single peak, no baseline)
# ---------------------------------------------------------------------------

def pseudo_voigt(x: np.ndarray, A: float, xc: float, w: float, mu: float) -> np.ndarray:
    """
    OriginLab **PsdVoigt1** profile (area-normalised, without the y0 term):

        A·( μ·(2/π)·(w / (4·(x−xc)² + w²))
            + (1−μ)·(√(4·ln2)/(√π·w))·exp(−(4·ln2/w²)·(x−xc)²) )

    ``A`` is the integrated peak area, ``w`` the shared FWHM, ``mu`` the
    Lorentzian fraction ∈ [0, 1], ``xc`` the centre (2θ).
    """
    ln2 = np.log(2.0)
    dx = x - xc
    lorentzian = (2.0 / np.pi) * (w / (4.0 * dx ** 2 + w ** 2))
    gaussian = (np.sqrt(4.0 * ln2) / (np.sqrt(np.pi) * w)) * \
        np.exp(-(4.0 * ln2 / w ** 2) * dx ** 2)
    return A * (mu * lorentzian + (1.0 - mu) * gaussian)


def _standard_model(x, Ag, xcg, wg, mug, At, xct, wt):
    """
    Standard pipeline model: a free graphitic Pseudo-Voigt plus a *pure
    Lorentzian* turbostratic peak (mu = 1), per the NETL convention seen in
    every reference fit. Seven free parameters (the turbostratic mu is fixed).
    """
    return pseudo_voigt(x, Ag, xcg, wg, mug) + pseudo_voigt(x, At, xct, wt, 1.0)


# ---------------------------------------------------------------------------
# Pattern container — parsing, windowing, baseline subtraction
# ---------------------------------------------------------------------------

class XRDPattern:
    """Holds a two-column XRD pattern and provides windowing/baseline helpers."""

    def __init__(self, two_theta: np.ndarray, intensity: np.ndarray) -> None:
        self.two_theta = np.asarray(two_theta, dtype=float)
        self.intensity = np.asarray(intensity, dtype=float)

    # -- construction --------------------------------------------------------

    @classmethod
    def from_text(cls, text: str) -> "XRDPattern":
        """Parse the contents of a .xy file (tolerant of headers/comments)."""
        tt: list[float] = []
        inten: list[float] = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s[0] in ("#", "!", "'") or s[0].isalpha():
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            try:
                tt.append(float(parts[0]))
                inten.append(float(parts[1]))
            except ValueError:
                continue
        if not tt:
            raise ValueError("No numeric (2θ, intensity) data found in input.")
        return cls(np.array(tt), np.array(inten))

    @classmethod
    def from_file(cls, path: str | Path) -> "XRDPattern":
        if not Path(path).exists():
            raise FileNotFoundError(2, "No such file or directory", str(path))
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return cls.from_text(fh.read())

    # -- views ---------------------------------------------------------------

    def window(self, low: float, high: float) -> tuple[np.ndarray, np.ndarray]:
        """Return (2θ, intensity) restricted to [low, high]."""
        mask = (self.two_theta >= low) & (self.two_theta <= high)
        return self.two_theta[mask], self.intensity[mask]

    def baseline_subtracted(
        self, low: float, high: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Linear background subtraction over [low, high].

        A straight baseline is drawn between the mean intensity of the left and
        right edges of the window; it is subtracted and the result clipped at 0.

        Returns (x, y_corrected, baseline).
        """
        x, y = self.window(low, high)
        if len(x) < 4:
            raise FitError(
                f"Only {len(x)} point(s) in the [{low}°, {high}°] window — "
                "cannot establish a baseline."
            )
        n_edge = max(3, len(x) // 20)
        xl, yl = x[:n_edge].mean(), y[:n_edge].mean()
        xr, yr = x[-n_edge:].mean(), y[-n_edge:].mean()
        slope = (yr - yl) / (xr - xl) if xr != xl else 0.0
        baseline = yl + slope * (x - xl)
        y_corr = np.clip(y - baseline, 0.0, None)
        return x, y_corr, baseline


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class GraphitizationAnalyzer:
    """Computes DG% from an :class:`XRDPattern` by Method A or Method B."""

    def __init__(self, pattern: XRDPattern, wavelength: float = DEFAULT_WAVELENGTH) -> None:
        self.pattern = pattern
        self.wavelength = float(wavelength)

    # -- physics -------------------------------------------------------------

    def bragg_d(self, two_theta_deg: float) -> float:
        """Bragg's Law: d = λ / (2·sin θ), with θ = (2θ)/2."""
        theta = np.deg2rad(two_theta_deg / 2.0)
        return self.wavelength / (2.0 * np.sin(theta))

    def maire_mering(self, d_prime: float) -> float:
        """DG% = (3.440 − d′) / (3.440 − 3.354) × 100."""
        return (D_TURBOSTRATIC - d_prime) / (D_TURBOSTRATIC - D_GRAPHITE) * 100.0

    def scherrer_lc(self, xc: float, w: float) -> float:
        """
        Crystallite stacking height: Lc = 0.89·λ / (B·cos(θ/2)),
        with B = w (FWHM) in radians and θ = peak centre (2θ).
        """
        B = np.deg2rad(w)
        theta_over_2 = np.deg2rad(xc / 2.0)
        return SCHERRER_K * self.wavelength / (B * np.cos(theta_over_2))

    # -- fitting -------------------------------------------------------------

    @staticmethod
    def _fit(model, x, y, p0, bounds, label):
        """Run curve_fit, converting OptimizeWarning/failures into FitError."""
        return GraphitizationAnalyzer._fit_cov(model, x, y, p0, bounds, label)[0]

    @staticmethod
    def _fit_cov(model, x, y, p0, bounds, label):
        """Like ``_fit`` but also returns the parameter covariance (for DG σ)."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            try:
                popt, pcov = curve_fit(model, x, y, p0=p0, bounds=bounds, maxfev=20000)
            except (OptimizeWarning, RuntimeError, ValueError) as exc:
                raise FitError(
                    f"{label}: fit did not converge "
                    f"(possible high amorphous content) — {exc}"
                )
        return popt, pcov

    @staticmethod
    def _r2(y, yfit):
        """Coefficient of determination of a fit."""
        ss_res = float(np.sum((y - yfit) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    @staticmethod
    def _peak(A, xc, w, mu, d):
        return {
            "A": round(float(A), 4),
            "xc": round(float(xc), 4),
            "w": round(float(w), 4),
            "mu": round(float(mu), 4),
            "d_spacing_angstrom": round(float(d), 6),
        }

    # -- the one standard automatic pipeline --------------------------------

    def run(self) -> dict:
        """
        Standard automatic DG% pipeline (single method, no options):

        1. Linear baseline subtraction over the (002) window (24°–27.5°).
        2. Fit a graphitic Pseudo-Voigt + a **pure-Lorentzian** turbostratic
           peak (mu = 1, the NETL convention), turbostratic centre ≤ 26.1°.
        3. Area-weighted d′ and the Maire-Mering DG%; Scherrer Lc from the
           graphitic peak.
        """
        x, y, _baseline = self.pattern.baseline_subtracted(*ANALYSIS_WINDOW)
        if len(x) < 10:
            raise FitError(
                f"Only {len(x)} point(s) in the {ANALYSIS_WINDOW} window — too few to fit."
            )
        ph = float(y.max())

        # params: [Ag, xcg, wg, mug,  At, xct, wt]   (turbostratic mu fixed = 1)
        p0 = [ph * 0.6, 26.55, 0.15, 0.5,  ph * 0.4, 26.0, 0.9]
        lo = [0.0, GRAPH_XC_BOUNDS[0], 0.02, 0.0,  0.0, TURBO_XC_BOUNDS[0], 0.05]
        hi = [np.inf, GRAPH_XC_BOUNDS[1], 3.0, 1.0,  np.inf, TURBO_XC_BOUNDS[1], 3.0]
        popt = self._fit(_standard_model, x, y, p0, (lo, hi), "standard fit")
        Ag, xcg, wg, mug, At, xct, wt = popt
        r2 = self._r2(y, _standard_model(x, *popt))

        dg, dt = self.bragg_d(xcg), self.bragg_d(xct)
        total = Ag + At
        Xg, Xt = Ag / total, At / total
        d_prime = Xg * dg + Xt * dt
        DG = self.maire_mering(d_prime)
        Lc = self.scherrer_lc(xcg, wg)

        return {
            "method_name": "Standard (graphitic + pure-Lorentzian turbostratic)",
            "wavelength_angstrom": round(self.wavelength, 6),
            "baseline_region_deg": list(ANALYSIS_WINDOW),
            "graphitic": self._peak(Ag, xcg, wg, mug, dg),
            "turbostratic": self._peak(At, xct, wt, 1.0, dt),
            "area_fraction_graphitic": round(Xg, 6),
            "area_fraction_turbostratic": round(Xt, 6),
            "d_spacing_weighted_angstrom": round(d_prime, 6),
            "crystallite_height_Lc_angstrom": round(float(Lc), 2),
            "fit_r2": round(r2, 5),
            "DG_percent": round(DG, 2),
        }


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------

def analyze_text(text: str) -> dict:
    return GraphitizationAnalyzer(XRDPattern.from_text(text)).run()


def analyze_file(path: str | Path) -> dict:
    return GraphitizationAnalyzer(XRDPattern.from_file(path)).run()


def dg_from_peaks(peaks: list[dict], wavelength: float = DEFAULT_WAVELENGTH) -> dict:
    """
    Compute DG% directly from manually-entered Origin fit peaks — the exact
    operation of NETL's "prompt excel sheet" (input peak centres + areas).

    Parameters
    ----------
    peaks : list of 1 or 2 dicts, each with ``xc`` (2θ, degrees) and ``area``
            (the OriginLab PsdVoigt1 ``A``). With one peak, DG comes from that
            peak's d-spacing; with two, from the area-weighted d′. The graphitic
            peak is taken as the higher-2θ peak.

    Returns
    -------
    Flat results dict (mode, per-peak d-spacings, fractions, d′, DG%).
    """
    if not (1 <= len(peaks) <= 2):
        raise ValueError("provide 1 or 2 peaks (xc + area each).")
    try:
        pk = [(float(p["xc"]), float(p["area"])) for p in peaks]
    except (KeyError, TypeError, ValueError):
        raise ValueError("each peak needs numeric 'xc' and 'area'.")
    for xc, area in pk:
        if not (1.0 < xc < 179.0):
            raise ValueError(f"peak centre {xc}° is out of range.")
        if area <= 0:
            raise ValueError("peak area must be positive.")

    def bragg(tt):
        return wavelength / (2.0 * np.sin(np.deg2rad(tt / 2.0)))

    def mm(dp):
        return (D_TURBOSTRATIC - dp) / (D_TURBOSTRATIC - D_GRAPHITE) * 100.0

    out: dict = {
        "method": "manual",
        "method_name": "Manual peak entry (NETL excel-sheet calculation)",
        "wavelength_angstrom": round(wavelength, 6),
        "n_peaks": len(pk),
    }
    if len(pk) == 1:
        xc, area = pk[0]
        d = bragg(xc)
        DG = mm(d)
        out.update({
            "graphitic": {"xc": round(xc, 4), "A": round(area, 4),
                          "d_spacing_angstrom": round(d, 6)},
            "d_spacing_weighted_angstrom": round(d, 6),
            "DG_percent": round(DG, 2),
        })
    else:
        (xg, ag), (xt, at) = sorted(pk, key=lambda p: -p[0])  # graphitic = higher 2θ
        dg, dt = bragg(xg), bragg(xt)
        Xg, Xt = ag / (ag + at), at / (ag + at)
        d_prime = Xg * dg + Xt * dt
        DG = mm(d_prime)
        out.update({
            "graphitic": {"xc": round(xg, 4), "A": round(ag, 4),
                          "d_spacing_angstrom": round(dg, 6)},
            "turbostratic": {"xc": round(xt, 4), "A": round(at, 4),
                             "d_spacing_angstrom": round(dt, 6)},
            "area_fraction_graphitic": round(Xg, 6),
            "area_fraction_turbostratic": round(Xt, 6),
            "d_spacing_weighted_angstrom": round(d_prime, 6),
            "DG_percent": round(DG, 2),
        })
    return out


# ---------------------------------------------------------------------------
# NETL-faithful fit (free y0, 1/2 peaks, lockable turbostratic, optional bg)
# Mirrors the Swift engine; used for the AI-assisted / interactive deconvolution.
# ---------------------------------------------------------------------------

# (002) deconvolution window. Right edge trimmed to 28.5° so the calcite CaCO3
# (104) reflection (~29.4-29.7°), which can survive in carbonate-heavy/unwashed
# samples, can never intrude on the fit — that region is pure baseline for the
# (002) anyway, so DG is unchanged (verified < 0.15% shift vs 24-30°).
NETL_WINDOW: tuple[float, float] = (24.0, 28.5)


# Reference 2theta lines (Cu Kα) for a QC phase scan. Graphite lines are the
# expected signal; the rest are catalyst/carbonate residue that acid washing is
# meant to remove. (002) is excluded — it's the analyte, handled by the fit.
_GRAPHITE_LINES = {                 # 2theta: label  — expected, NOT impurities
    42.4: "graphite (100)", 44.6: "graphite (101)", 50.6: "graphite (102)",
    54.7: "graphite (004)", 77.5: "graphite (110)", 83.6: "graphite (112)",
}
_IMPURITY_LINES = {                 # 2theta: (phase, what it means)
    29.4: ("calcite CaCO3 (104)", "carbonate residue"),
    30.9: ("calcite/dolomite", "carbonate residue"),
    36.0: ("iron oxide (Fe3O4/Fe2O3)", "oxidised catalyst"),
    37.4: ("CaO (200)", "lime from CaCO3 decomposition"),
    43.8: ("Fe3C cementite", "unreacted iron carbide"),
    45.0: ("Fe3C / Fe", "iron carbide / metallic iron"),
    49.1: ("Fe3C cementite", "unreacted iron carbide"),
    53.9: ("CaO (220)", "lime from CaCO3 decomposition"),
    64.9: ("metallic Fe (200)", "unreacted iron catalyst"),
}


def scan_impurities(two_theta, intensity, *, tol: float = 0.45,
                    trace_frac: float = 0.012) -> dict:
    """Full-pattern QC scan: flag catalyst/carbonate residue peaks.

    Detects local-prominence peaks across the whole scan and assigns each to a
    known graphite or impurity line. DG is computed only from the (002) window,
    so this never changes the result — it's a data-quality flag indicating
    whether acid washing was complete. Severity is relative to the (002) height.
    """
    tt = np.asarray(two_theta, float)
    inten = np.asarray(intensity, float)
    if tt.size < 50:
        return {"verdict": "insufficient range", "impurities": [], "clean": True}
    o = np.argsort(tt)
    x, y = tt[o], inten[o]

    # (002) height for relative severity
    win = (x >= 24.0) & (x <= 28.5)
    i002 = float(y[win].max()) if win.any() else float(y.max())
    base002 = float(np.median(np.r_[y[win][:8], y[win][-8:]])) if win.sum() > 16 else float(np.min(y))
    h002 = max(i002 - base002, 1e-9)

    # local-prominence peak detection vs a wide rolling minimum (baseline)
    half = 30
    pad = np.pad(y, half, mode="edge")
    rollmin = np.array([pad[i:i + 2 * half + 1].min() for i in range(len(y))])
    prom = y - rollmin
    noise = float(np.std(np.r_[y[:20], y[-20:]]))
    thresh = max(5.0 * noise, trace_frac * h002, 4.0)

    peaks = []
    w = 12
    for i in range(w, len(y) - w):
        seg = y[i - w:i + w + 1]
        if y[i] == seg.max() and prom[i] >= thresh:
            if not peaks or x[i] - peaks[-1][0] > 0.7:
                peaks.append([x[i], prom[i]])
            elif prom[i] > peaks[-1][1]:
                peaks[-1] = [x[i], prom[i]]

    impurities = []
    for px, pr in peaks:
        if abs(px - 26.5) < 1.0:
            continue  # the (002) analyte itself
        g = min(_GRAPHITE_LINES, key=lambda r: abs(r - px))
        m = min(_IMPURITY_LINES, key=lambda r: abs(r - px))
        dg_, dm_ = abs(g - px), abs(m - px)
        if dg_ < tol and dg_ <= dm_:
            continue  # an expected graphite reflection
        if dm_ < tol:
            phase, meaning = _IMPURITY_LINES[m]
            rel = round(100.0 * float(pr) / h002, 1)
            impurities.append({"two_theta": round(float(px), 2), "phase": phase,
                               "meaning": meaning, "rel_pct": rel,
                               "level": ("significant" if rel >= 10 else
                                         "minor" if rel >= 2 else "trace")})

    impurities.sort(key=lambda d: -d["rel_pct"])
    worst = float(max((d["rel_pct"] for d in impurities), default=0.0))
    if not impurities:
        verdict = "Clean — graphite reflections only."
    elif worst >= 10:
        verdict = "Residual catalyst/carbonate detected — washing likely incomplete."
    elif worst >= 2:
        verdict = "Minor residual catalyst/carbonate present."
    else:
        verdict = "Trace impurities only — essentially clean."
    return {"verdict": verdict, "impurities": impurities,
            "clean": bool(worst < 2), "worst_pct": round(worst, 1)}


# --------------------------------------------------------------------------
# Internal-standard 2theta calibration
# --------------------------------------------------------------------------
# Residual catalyst/carbonate phases have lattice-fixed d-spacings (independent
# of graphitization), so their reflections are a built-in 2theta reference. We
# index a chosen phase, match its lines to observed peaks, and if they agree on
# one offset (tight spread) we use that to correct specimen-displacement / zero
# error -- far more rigorous than anchoring the (002) to a guessed angle.
# Lattice constants are room-temperature reference values (Å). Sources:
#   Fe3C     orthorhombic Pnma  — ICDD PDF 00-035-0772, after Fasiska & Jeffrey,
#            Acta Cryst. 19 (1965) 463–471 (a,b,c here are the Pnma cell).
#   alpha-Fe cubic Im-3m (bcc)  — ICDD PDF 00-006-0696, a=2.8664 Å at 20 °C.
#   CaO      cubic Fm-3m (lime) — ICDD PDF 00-037-1497, a=4.8105 Å.
_CALIB_PHASES = {
    "Fe3C":     {"system": "ortho", "abc": (5.0896, 6.7443, 4.5248), "label": "cementite Fe₃C"},
    "alpha-Fe": {"system": "cubic", "a": 2.8664, "label": "metallic α-Fe"},
    "CaO":      {"system": "cubic", "a": 4.8105, "label": "lime CaO"},
}
# graphite's own reflections — never use these as the foreign-phase reference
_GRAPHITE_LINES_DEG = (26.55, 42.4, 44.6, 50.6, 54.7, 77.5, 83.6)


def _phase_lines(phase: str, wavelength: float, lo: float = 32.0, hi: float = 90.0) -> list[float]:
    spec = _CALIB_PHASES[phase]
    out = []
    for h in range(4):
        for k in range(4):
            for l in range(4):
                if h == k == l == 0:
                    continue
                if spec["system"] == "cubic":
                    a = spec["a"]; inv = (h * h + k * k + l * l) / (a * a)
                else:
                    a, b, c = spec["abc"]; inv = h * h / (a * a) + k * k / (b * b) + l * l / (c * c)
                s = wavelength / (2.0 / math.sqrt(inv))
                if s > 1.0:
                    continue
                t = 2.0 * math.degrees(math.asin(s))
                if lo <= t <= hi:
                    out.append(t)
    out = sorted(set(round(t, 3) for t in out))
    dedup = []
    for t in out:
        if not dedup or t - dedup[-1] > 0.15:
            dedup.append(t)
    return dedup


# Half-width (deg) of the intensity-weighted centroid window. Defined in 2θ
# (not index count) so Python and Swift select identical points → identical centre.
_CENTROID_HALF_DEG = 0.12


def _local_peaks(x, y, lo, hi, min_prom_frac=0.02):
    """Local-prominence peaks with an intensity-weighted centre in [lo, hi]."""
    o = np.argsort(x); x, y = x[o], y[o]
    half = 40
    pad = np.pad(y, half, mode="edge")
    rollmin = np.array([pad[i:i + 2 * half + 1].min() for i in range(len(y))])
    prom = y - rollmin
    ymax = float(y.max())
    out = []
    for i in range(6, len(y) - 6):
        if not (lo <= x[i] <= hi):
            continue
        if y[i] == y[i - 6:i + 7].max() and prom[i] > min_prom_frac * ymax:
            sel = (x >= x[i] - _CENTROID_HALF_DEG) & (x <= x[i] + _CENTROID_HALF_DEG)
            wts = np.clip(y[sel] - rollmin[sel], 0.0, None)
            cen = float((x[sel] * wts).sum() / wts.sum()) if wts.sum() > 0 else float(x[i])
            if not out or cen - out[-1][0] > 0.4:
                out.append([float(cen), float(prom[i])])
    return out


def calibrate_internal_standard(two_theta, intensity, phase: str = "auto", *,
                                wavelength: float = DEFAULT_WAVELENGTH,
                                tol: float = 0.40, min_lines: int = 3,
                                max_spread: float = 0.06, max_offset: float = 0.8,
                                min_significant: float = 0.05) -> dict:
    """Estimate the 2theta offset from a residual-phase internal standard.

    ``phase`` is one of ``Fe3C`` / ``alpha-Fe`` / ``CaO`` or ``"auto"`` (try all,
    keep the best-supported reliable match). Returns the median offset across the
    matched lines (observed - reference), its spread, and a ``reliable`` flag
    (enough lines that agree). Apply ``-offset`` via ``fit_netl(two_theta_offset=)``.
    """
    tt = np.asarray(two_theta, float)
    inten = np.asarray(intensity, float)
    peaks = _local_peaks(tt, inten, 32.0, 90.0)
    phases = [phase] if phase in _CALIB_PHASES else list(_CALIB_PHASES)
    best = None
    for ph in phases:
        matches = []
        for L in _phase_lines(ph, wavelength):
            if any(abs(L - g) < 0.5 for g in _GRAPHITE_LINES_DEG):
                continue  # avoid graphite-overlapped reference lines
            cand = [p for p in peaks if abs(p[0] - L) < tol]
            if cand:
                pk = min(cand, key=lambda p: abs(p[0] - L))
                matches.append((round(L, 3), round(pk[0], 3), round(pk[0] - L, 4)))
        if len(matches) < min_lines:
            continue
        d = np.array([m[2] for m in matches])
        off = float(np.median(d))
        keep = [m for m in matches if abs(m[2] - off) <= 0.10]  # drop outlier mis-assignments
        if len(keep) < min_lines:
            continue
        d = np.array([m[2] for m in keep])
        off = float(np.median(d)); spread = float(np.std(d))
        reliable = spread <= max_spread and abs(off) <= max_offset
        # below the reference-lattice uncertainty floor (~0.05deg) the offset is
        # within method noise — report it but don't treat it as a real shift.
        significant = reliable and abs(off) >= min_significant
        res = {"phase": ph, "phase_label": _CALIB_PHASES[ph]["label"],
               "offset": round(off, 4), "spread": round(spread, 4),
               "n_lines": len(keep), "matches": keep,
               "reliable": bool(reliable), "significant": bool(significant)}
        rank = (reliable, len(keep), -spread)
        if best is None or rank > (best["reliable"], best["n_lines"], -best["spread"]):
            best = res
    if best is None:
        return {"phase": None, "phase_label": None, "offset": 0.0, "spread": None,
                "n_lines": 0, "matches": [], "reliable": False, "significant": False}
    return best


def _bragg_d(tt_deg, wavelength):
    return wavelength / (2.0 * np.sin(np.deg2rad(tt_deg / 2.0)))


def _dg_single(xcg, wavelength):
    return (D_TURBOSTRATIC - _bragg_d(xcg, wavelength)) / (D_TURBOSTRATIC - D_GRAPHITE) * 100.0


def _dg_two(Ag, xcg, At, xct, wavelength):
    if xct > xcg:                       # graphitic = higher 2θ
        xcg, xct, Ag, At = xct, xcg, At, Ag
    total = Ag + At
    Xg = Ag / total if total > 0 else 1.0
    dprime = Xg * _bragg_d(xcg, wavelength) + (1 - Xg) * _bragg_d(xct, wavelength)
    return (D_TURBOSTRATIC - dprime) / (D_TURBOSTRATIC - D_GRAPHITE) * 100.0


def _mc_dg_sigma(popt, pcov, dg_fn, n=400):
    """Monte-Carlo DG std from the fit parameter covariance (None if ill-posed)."""
    if pcov is None:
        return None
    pcov = np.asarray(pcov, float)
    if pcov.shape != (len(popt), len(popt)) or not np.all(np.isfinite(pcov)):
        return None
    try:
        samples = np.random.default_rng(12345).multivariate_normal(popt, pcov, n)
    except Exception:  # noqa: BLE001 — singular / non-PSD covariance
        return None
    vals = []
    for s in samples:
        try:
            v = dg_fn(s)
            if np.isfinite(v) and -50.0 < v < 150.0:
                vals.append(v)
        except Exception:  # noqa: BLE001
            pass
    return round(float(np.std(vals)), 2) if len(vals) >= 20 else None


def _y0_pv(x, y0, A, xc, w, mu):
    return y0 + pseudo_voigt(x, A, xc, w, mu)


def anchor_offset(two_theta, intensity, target_2theta: float,
                  wavelength: float = DEFAULT_WAVELENGTH,
                  window: tuple[float, float] = NETL_WINDOW) -> float:
    """Constant 2θ offset that puts the measured graphitic (002) at ``target_2theta``.

    Corrects a Bragg-Brentano specimen-displacement error: a quick single-peak fit
    locates the measured (002) centre, and the offset is ``target − measured``.
    Over the narrow (002) window the cosθ dependence of true displacement is ~flat,
    so a constant shift is exact enough for DG. (Anchoring assumes the graphitic
    phase is well ordered; an internal standard is the rigorous alternative.)
    """
    r = fit_netl(two_theta, intensity, peak_count=1,
                 wavelength=wavelength, window=window)
    return float(target_2theta) - r["graphitic"]["xc"]


def fit_netl(two_theta, intensity, *, peak_count: int = 2,
             turbostratic_center: float | None = None, lock_turbostratic: bool = False,
             subtract_background: bool = False,
             two_theta_offset: float = 0.0, anchor_002: float | None = None,
             wavelength: float = DEFAULT_WAVELENGTH,
             window: tuple[float, float] = NETL_WINDOW) -> dict:
    """
    NETL PsdVoigt1 deconvolution with a free shared ``y0``: graphitic (free μ) +
    optional pure-Lorentzian turbostratic (μ=1). One or two peaks; the
    turbostratic centre may be supplied (the AI/human's choice). Returns DG% via
    area-weighted Maire-Mering. Identical method to the Swift core.

    Specimen-displacement calibration (optional): pass ``anchor_002`` to shift the
    whole pattern so the measured (002) lands at that 2θ (e.g. 26.54°), or a raw
    ``two_theta_offset``. The applied shift is reported in ``two_theta_offset``.
    """
    tt = np.asarray(two_theta, float)
    inten = np.asarray(intensity, float)
    if anchor_002 is not None:
        two_theta_offset = anchor_offset(tt, inten, anchor_002, wavelength, window)
    if two_theta_offset:
        tt = tt + two_theta_offset
    mask = (tt >= window[0]) & (tt <= window[1])
    x, y = tt[mask], inten[mask]
    if len(x) < 10:
        raise FitError(f"only {len(x)} point(s) in the {window} window — too few to fit.")
    if subtract_background:
        n_edge = max(3, len(x) // 20)
        xl, yl = x[:n_edge].mean(), y[:n_edge].mean()
        xr, yr = x[-n_edge:].mean(), y[-n_edge:].mean()
        slope = (yr - yl) / (xr - xl) if xr != xl else 0.0
        y = np.clip(y - (yl + slope * (x - xl)), 0.0, None)

    ph, ymin = float(y.max()), float(y.min())

    def bragg(tt_deg):
        return wavelength / (2.0 * np.sin(np.deg2rad(tt_deg / 2.0)))

    if peak_count <= 1:
        p0 = [ymin, ph * 0.9, 26.55, 0.2, 0.5]
        lo = [-np.inf, 0, 26.3, 0.02, 0]; hi = [np.inf, np.inf, 26.8, 3, 1]
        popt, pcov = GraphitizationAnalyzer._fit_cov(_y0_pv, x, y, p0, (lo, hi), "AI single-peak")
        y0, Ag, xcg, wg, mug = popt
        At = xct = wt = None
        dg_fn = lambda p: _dg_single(p[2], wavelength)
    elif lock_turbostratic and turbostratic_center is not None:
        T = float(turbostratic_center)
        model = lambda x, y0, Ag, xcg, wg, mug, At, wt: \
            y0 + pseudo_voigt(x, Ag, xcg, wg, mug) + pseudo_voigt(x, At, T, wt, 1.0)
        p0 = [ymin, ph * 0.6, 26.55, 0.15, 0.5, ph * 0.3, 0.5]
        lo = [-np.inf, 0, 26.3, 0.02, 0, 0, 0.05]; hi = [np.inf, np.inf, 26.8, 3, 1, np.inf, 3]
        popt, pcov = GraphitizationAnalyzer._fit_cov(model, x, y, p0, (lo, hi), "AI two-peak (locked)")
        y0, Ag, xcg, wg, mug, At, wt = popt; xct = T
        dg_fn = lambda p: _dg_two(p[1], p[2], p[5], T, wavelength)
    else:
        tseed = float(turbostratic_center) if turbostratic_center is not None else 26.2
        model = lambda x, y0, Ag, xcg, wg, mug, At, xct, wt: \
            y0 + pseudo_voigt(x, Ag, xcg, wg, mug) + pseudo_voigt(x, At, xct, wt, 1.0)
        p0 = [ymin, ph * 0.6, 26.55, 0.15, 0.5, ph * 0.3, tseed, 0.6]
        lo = [-np.inf, 0, 26.3, 0.02, 0, 0, 25.1, 0.05]
        hi = [np.inf, np.inf, 26.8, 3, 1, np.inf, 26.45, 3]
        popt, pcov = GraphitizationAnalyzer._fit_cov(model, x, y, p0, (lo, hi), "AI two-peak")
        y0, Ag, xcg, wg, mug, At, xct, wt = popt
        dg_fn = lambda p: _dg_two(p[1], p[2], p[5], p[6], wavelength)

    dg_sigma = _mc_dg_sigma(popt, pcov, dg_fn)

    dg = bragg(xcg)
    if At is None:
        Xg, Xt, d_prime = 1.0, 0.0, dg
        turbo = None
    else:
        # graphitic = higher 2θ
        if xct > xcg:
            xcg, xct, Ag, At, wg, wt, mug = xct, xcg, At, Ag, wt, wg, 1.0
        dt = bragg(xct)
        total = Ag + At
        Xg, Xt = (Ag / total, At / total) if total > 0 else (1.0, 0.0)
        d_prime = Xg * dg + Xt * dt
        turbo = {"xc": round(float(xct), 4), "w": round(float(wt), 4), "mu": 1.0,
                 "A": round(float(At), 4), "d_spacing_angstrom": round(float(dt), 6)}

    DG = (D_TURBOSTRATIC - d_prime) / (D_TURBOSTRATIC - D_GRAPHITE) * 100.0
    B = np.deg2rad(wg)
    Lc = SCHERRER_K * wavelength / (B * np.cos(np.deg2rad(xcg / 2.0)))
    return {
        "method_name": "NETL faithful (free y0)" + ("" if At is None else " · 2-peak"),
        "wavelength_angstrom": round(wavelength, 6),
        "two_theta_offset": round(float(two_theta_offset), 4),
        "y0": round(float(y0), 4),
        "peak_count": 1 if At is None else 2,
        "background_subtracted": bool(subtract_background),
        "graphitic": {"xc": round(float(xcg), 4), "w": round(float(wg), 4),
                      "mu": round(float(mug), 4), "A": round(float(Ag), 4),
                      "d_spacing_angstrom": round(float(dg), 6)},
        "turbostratic": turbo,
        "area_fraction_graphitic": round(float(Xg), 6),
        "area_fraction_turbostratic": round(float(Xt), 6),
        "d_spacing_weighted_angstrom": round(float(d_prime), 6),
        "crystallite_height_Lc_angstrom": round(float(Lc), 2),
        "DG_percent": round(float(DG), 2),
        "DG_sigma": dg_sigma,   # statistical (fit-covariance) 1σ; None if ill-posed
        "points_x": [round(float(v), 4) for v in x],
        "points_y": [round(float(v), 4) for v in y],
    }


def dg_range(two_theta, intensity, *, turbostratic_low: float = 26.10,
             subtract_background: bool = False, anchor_002: float | None = None,
             two_theta_offset: float = 0.0, wavelength: float = DEFAULT_WAVELENGTH,
             window: tuple[float, float] = NETL_WINDOW) -> dict | None:
    """DG across the defensible deconvolution choices → primary + [low, high].

    The dominant DG uncertainty is the *deconvolution choice* (1 vs 2 peaks, where
    the turbostratic peak sits), not the fit covariance. This runs the three
    defensible setups — 2-peak free (primary), 1-peak, and 2-peak with a broad
    low turbostratic — and reports the spread, so the non-uniqueness is explicit.
    """
    kw = dict(subtract_background=subtract_background, anchor_002=anchor_002,
              two_theta_offset=two_theta_offset, wavelength=wavelength, window=window)
    methods: dict[str, float] = {}
    for name, extra in (("2-peak", dict(peak_count=2)),
                        ("1-peak", dict(peak_count=1)),
                        ("2-peak low turbostratic",
                         dict(peak_count=2, turbostratic_center=turbostratic_low,
                              lock_turbostratic=True))):
        try:
            methods[name] = round(fit_netl(two_theta, intensity, **extra, **kw)["DG_percent"], 2)
        except (FitError, ValueError):
            pass
    if not methods:
        return None
    vals = list(methods.values())
    return {"primary": methods.get("2-peak", vals[0]),
            "low": round(min(vals), 2), "high": round(max(vals), 2),
            "by_method": methods}


# ---------------------------------------------------------------------------
# Crystallinity — amorphous-aware (002) decomposition (AMOUNT of crystalline C)
# ---------------------------------------------------------------------------
# DG% (above) measures ordering *quality* (d-spacing); it is blind to how much
# carbon is amorphous, because amorphous carbon scatters diffusely with no sharp
# peak. This decomposes the (002) region into a sharp crystalline peak + a broad
# disordered band (amorphous + turbostratic halo) and reports the integrated-area
# crystallinity index. It is the canonical engine implementation mirrored by the
# Swift core (XRDCore/Crystallinity.swift) and verified by a parity test; the
# research/ scripts and the web GUI both read it, so all front-ends show one value.
#
# IMPORTANT: a model-dependent *index* (sharp ÷ total 002 scattering), NOT a weight
# percent — amorphous and crystalline carbon do not scatter equally per gram, so
# convert to absolute wt% only via a physical-standard calibration. Sources:
# Warren (1941); Franklin (1951); Iwashita Carbon 42 (2004); Lu Carbon 39 (2001);
# Ruland & Smarsly J. Appl. Cryst. 35 (2002).
CRYSTALLINITY_WINDOW: tuple[float, float] = (16.0, 31.0)


def crystallinity(two_theta, intensity, *, wavelength: float = DEFAULT_WAVELENGTH,
                  window: tuple[float, float] = CRYSTALLINITY_WINDOW) -> dict:
    """Fit a broad disordered band + a sharp graphitic (002) peak on a
    deterministically baseline-subtracted window; return the crystalline fraction.

    The deterministic edge-baseline subtraction (not a free background) is what
    keeps this numerically in lockstep with the Swift port — a free background
    would let two optimizers split the area differently.
    """
    tt = np.asarray(two_theta, float)
    inten = np.asarray(intensity, float)
    o = np.argsort(tt)
    tt, inten = tt[o], inten[o]
    mask = (tt >= window[0]) & (tt <= window[1])
    x, y = tt[mask], inten[mask]
    if len(x) < 12:
        raise FitError(f"only {len(x)} point(s) in {window}° — too few to decompose.")

    # edge-baseline subtraction (matches XRDPattern.baseline_subtracted / Swift)
    n_edge = max(3, len(x) // 20)
    xl, yl = x[:n_edge].mean(), y[:n_edge].mean()
    xr, yr = x[-n_edge:].mean(), y[-n_edge:].mean()
    slope = (yr - yl) / (xr - xl) if xr != xl else 0.0
    y = np.clip(y - (yl + slope * (x - xl)), 0.0, None)
    ph = float(y.max())

    # model: broad disordered PV + sharp graphitic PV (no free background)
    def model(xx, A_d, xc_d, w_d, mu_d, A_g, xc_g, w_g, mu_g):
        return pseudo_voigt(xx, A_d, xc_d, w_d, mu_d) + pseudo_voigt(xx, A_g, xc_g, w_g, mu_g)

    p0 = [ph * 0.4 * 3.0, 25.5, 3.0, 0.5, ph * 0.8 * 0.3, 26.5, 0.3, 0.6]
    lo = [0.0, 22.0, 1.5, 0.0, 0.0, 26.2, 0.05, 0.0]
    hi = [np.inf, 26.3, 12.0, 1.0, np.inf, 26.9, 0.8, 1.0]
    popt = GraphitizationAnalyzer._fit(model, x, y, p0, (lo, hi), "crystallinity decomposition")
    A_d, xc_d, w_d, mu_d, A_g, xc_g, w_g, mu_g = popt

    yfit = model(x, *popt)
    ss_res = float(np.sum((y - yfit) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    A_d, A_g = float(A_d), float(A_g)
    total = (A_d + A_g) or 1.0
    d_g = wavelength / (2.0 * np.sin(np.deg2rad(float(xc_g) / 2.0)))
    return {
        "crystalline_fraction": round(A_g / total, 4),   # sharp ÷ total 002 scattering
        "disordered_fraction": round(A_d / total, 4),     # amorphous + turbostratic
        "sharp_area": round(A_g, 4),
        "broad_area": round(A_d, 4),
        "graphitic_xc": round(float(xc_g), 4),
        "graphitic_w": round(float(w_g), 4),
        "d_graphitic_angstrom": round(float(d_g), 6),
        "fit_r2": round(r2, 5),
        "window_deg": list(window),
        "is_absolute_wt_pct": False,
    }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def expand_xy_inputs(paths: list[str]) -> list[str]:
    """Expand file/dir arguments into a flat list of .xy paths (dirs → *.xy)."""
    files: list[str] = []
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            files.extend(str(x) for x in sorted(pth.glob("*.xy")))
        else:
            files.append(str(p))
    return files


def analyze_batch(filepaths: list[str]) -> list[dict]:
    """Analyse many files; each entry is results+``file`` or ``{file, error}``."""
    results: list[dict] = []
    for fp in filepaths:
        entry: dict = {"file": str(fp)}
        try:
            entry.update(analyze_file(fp))
        except FileNotFoundError as exc:
            entry["error"] = f"file not found — '{exc.filename or fp}'"
        except (FitError, ValueError) as exc:
            entry["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"unexpected error — {exc}"
        results.append(entry)
    return results


_CSV_COLUMNS = (
    "file", "wavelength_angstrom", "DG_percent",
    "d_spacing_weighted_angstrom", "graphitic_xc", "graphitic_w",
    "turbostratic_xc", "area_fraction_turbostratic",
    "crystallite_height_Lc_angstrom", "fit_r2", "error",
)


def _csv_row(entry: dict) -> dict:
    """Flatten a (possibly nested) result entry into CSV columns."""
    row = {"file": entry.get("file", "")}
    if "error" in entry:
        row["error"] = entry["error"]
        return row
    row["wavelength_angstrom"] = entry.get("wavelength_angstrom")
    row["DG_percent"] = entry.get("DG_percent")
    row["d_spacing_weighted_angstrom"] = entry.get("d_spacing_weighted_angstrom")
    g = entry.get("graphitic", {})
    t = entry.get("turbostratic", {})
    row["graphitic_xc"] = g.get("xc")
    row["graphitic_w"] = g.get("w")
    row["turbostratic_xc"] = t.get("xc")
    row["area_fraction_turbostratic"] = entry.get("area_fraction_turbostratic")
    row["crystallite_height_Lc_angstrom"] = entry.get("crystallite_height_Lc_angstrom")
    row["fit_r2"] = entry.get("fit_r2")
    return row


def write_batch_csv(results: list[dict], fh) -> None:
    writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in results:
        writer.writerow(_csv_row(r))


# ---------------------------------------------------------------------------
# CLI — human-readable rendering & argument parsing
# ---------------------------------------------------------------------------

def _print_peak(label: str, p: dict) -> None:
    print(f"    {label:<20}: xc={p['xc']:.4f}°  w={p['w']:.4f}°  "
          f"μ={p['mu']:.4f}  A={p['A']:.4f}  d={p['d_spacing_angstrom']:.6f} Å")


def _print_human(result: dict, filepath: str | Path) -> None:
    SEP = "=" * 64
    print(SEP)
    print(f"  XRD Degree of Graphitization — {result['method_name']}")
    print(f"  File : {filepath}")
    print(f"  λ    : {result['wavelength_angstrom']:.5f} Å")
    print(SEP)

    if "n_peaks" in result:   # manual peak-entry result
        _mp = lambda lbl, p: print(f"    {lbl:<16}: xc={p['xc']:.4f}°  A={p['A']:.4f}  "
                                   f"d={p['d_spacing_angstrom']:.6f} Å")
        print("\n  Manual peak entry (NETL excel-sheet calculation)")
        _mp("Graphitic", result["graphitic"])
        if result["n_peaks"] == 2:
            _mp("Turbostratic", result["turbostratic"])
            print(f"    X_g = {result['area_fraction_graphitic']:.4f}   "
                  f"X_t = {result['area_fraction_turbostratic']:.4f}   "
                  f"d′ = {result['d_spacing_weighted_angstrom']:.6f} Å")
    else:                      # standard auto-fit result
        print("\n  Fitted peaks  (graphitic + pure-Lorentzian turbostratic)")
        _print_peak("Graphitic", result["graphitic"])
        _print_peak("Turbostratic", result["turbostratic"])
        print(f"\n  X_g = {result['area_fraction_graphitic']:.4f}   "
              f"X_t = {result['area_fraction_turbostratic']:.4f}   "
              f"d′ = {result['d_spacing_weighted_angstrom']:.6f} Å")
        print(f"  Crystallite height Lc : {result['crystallite_height_Lc_angstrom']:.2f} Å"
              f"   (fit R² = {result['fit_r2']:.5f})")

    print()
    print(SEP)
    print(f"  Degree of Graphitization  DG% = {result['DG_percent']:>7.2f} %")
    print(SEP)


def _print_batch_table(results: list[dict]) -> None:
    SEP = "=" * 72
    print(SEP)
    print("  XRD Degree of Graphitization — Batch Summary")
    print(SEP)
    print(f"  {'File':<50}{'DG%':>9}")
    print("  " + "-" * 68)
    ok = 0
    for r in results:
        name = Path(r["file"]).name
        if len(name) > 48:
            name = name[:45] + "..."
        if "error" in r:
            print(f"  {name:<50}{'ERROR':>9}")
            print(f"      ↳ {r['error']}")
        else:
            ok += 1
            print(f"  {name:<50}{r['DG_percent']:>9.2f}")
    print("  " + "-" * 68)
    print(f"  {ok}/{len(results)} file(s) analyzed successfully.")
    print(SEP)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xrd_analyzer",
        description="Analyse XRD .xy files for synthetic graphite (Degree of Graphitization).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python xrd_analyzer.py sample.xy\n"
            "  python xrd_analyzer.py sample.xy --json\n"
            "  python xrd_analyzer.py *.xy --csv results.csv\n"
            "  python xrd_analyzer.py --peaks 26.51:20.571,26.181:8.062\n"
        ),
    )
    parser.add_argument("filepaths", nargs="*", metavar="FILE_OR_DIR",
                        help="One or more .xy files and/or directories (expanded to *.xy).")
    parser.add_argument("--peaks", default=None, metavar="xc:area,...",
                        help="Manual peak entry (no file/fit): 1 or 2 comma-separated "
                             "'xc:area' pairs from an Origin fit, e.g. "
                             "26.51:20.571,26.181:8.062. Computes DG like the NETL excel sheet.")
    parser.add_argument("--csv", dest="csv_path", default=None, metavar="FILE",
                        help="Write results as CSV to FILE ('-' for stdout).")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON only (object for one file, array in batch); "
                             "suppresses all other stdout.")
    return parser


def _parse_peaks_arg(spec: str) -> list[dict]:
    """Parse '26.51:20.571,26.181:8.062' into [{xc, area}, ...]."""
    peaks = []
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" not in tok:
            raise ValueError(f"bad peak '{tok}' — expected xc:area.")
        xc, area = tok.split(":", 1)
        peaks.append({"xc": float(xc), "area": float(area)})
    return peaks


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # ---- manual peak-entry mode (no file/fit) ----
    if args.peaks:
        try:
            result = dg_from_peaks(_parse_peaks_arg(args.peaks))
        except ValueError as exc:
            _fail(str(exc), args.json_output)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            _print_human(result, "(manual peak entry)")
        return

    files = expand_xy_inputs(args.filepaths)
    if not files:
        msg = "no .xy files found in the given path(s)."
        print(json.dumps({"error": msg}) if args.json_output
              else f"Error: {msg}", file=(None if args.json_output else sys.stderr))
        sys.exit(1)

    # ---- single-file mode: rich output ----
    if len(files) == 1 and not args.csv_path:
        fp = files[0]
        try:
            result = analyze_file(fp)
        except FileNotFoundError as exc:
            _fail(f"file not found — '{exc.filename or fp}'", args.json_output)
        except (FitError, ValueError) as exc:
            _fail(str(exc), args.json_output)
        except Exception as exc:  # noqa: BLE001
            _fail(f"unexpected error — {exc}", args.json_output)

        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            _print_human(result, fp)
        return

    # ---- batch mode ----
    results = analyze_batch(files)
    if args.csv_path:
        if args.csv_path == "-":
            write_batch_csv(results, sys.stdout)
        else:
            with open(args.csv_path, "w", newline="", encoding="utf-8") as fh:
                write_batch_csv(results, fh)
    if args.json_output:
        print(json.dumps(results, indent=2))
    elif not args.csv_path:
        _print_batch_table(results)
    elif args.csv_path != "-":
        ok = sum(1 for r in results if "error" not in r)
        print(f"Wrote {ok}/{len(results)} result(s) to {args.csv_path}", file=sys.stderr)

    if all("error" in r for r in results):
        sys.exit(1)


def _fail(msg: str, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
