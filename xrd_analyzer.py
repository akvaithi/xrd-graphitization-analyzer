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

# Strict peak-centre bounds (Method B; also reused to keep Method A labelled)
TURBO_XC_BOUNDS: tuple[float, float] = (25.1, 26.3)
GRAPH_XC_BOUNDS: tuple[float, float] = (26.3, 26.8)

# NETL "fit one peak first" rule: if the single-peak fit's R² clears this
# threshold, the single-peak DG is recommended (well-graphitized sample);
# otherwise a two-peak fit is needed. Env-overridable.
SINGLE_FIT_R2_THRESHOLD: float = float(os.environ.get("XRD_SINGLE_FIT_R2", "0.997"))

METHODS = ("A", "B")


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


def _single_model(x, A, xc, w, mu):
    """Single PsdVoigt1 peak (baseline already removed)."""
    return pseudo_voigt(x, A, xc, w, mu)


def _dual_model(x, Ag, xcg, wg, mug, At, xct, wt, mut):
    """Sum of graphitic + turbostratic PsdVoigt1 peaks (baseline removed)."""
    return pseudo_voigt(x, Ag, xcg, wg, mug) + pseudo_voigt(x, At, xct, wt, mut)


def _dual_model_baseline(x, Ag, xcg, wg, mug, At, xct, wt, mut, y0):
    """Method A model: two peaks on a fitted flat baseline y0."""
    return y0 + pseudo_voigt(x, Ag, xcg, wg, mug) + pseudo_voigt(x, At, xct, wt, mut)


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
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            try:
                popt, _ = curve_fit(model, x, y, p0=p0, bounds=bounds, maxfev=20000)
            except (OptimizeWarning, RuntimeError, ValueError) as exc:
                raise FitError(
                    f"{label}: fit did not converge "
                    f"(possible high amorphous content) — {exc}"
                )
        return popt

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

    # -- dispatch ------------------------------------------------------------

    def run(self, method: str) -> dict:
        if method == "A":
            return self._run_a()
        if method == "B":
            return self._run_b()
        if method == "both":
            out: dict = {"method": "both", "wavelength_angstrom": round(self.wavelength, 6)}
            for key, fn in (("A", self._run_a), ("B", self._run_b)):
                try:
                    out[key] = fn()
                except (FitError, ValueError) as exc:
                    out[key] = {"method": key, "error": str(exc)}
            return out
        raise ValueError(f"unknown method '{method}'; choose from {METHODS} or 'both'.")

    # -- Method A: NETL paper standard --------------------------------------

    def _run_a(self) -> dict:
        x, y = self.pattern.window(*ANALYSIS_WINDOW)
        if len(x) < 10:
            raise FitError(
                f"Only {len(x)} point(s) in the {ANALYSIS_WINDOW} window — too few to fit."
            )
        ph, base0 = float(y.max()), float(y.min())

        # [Ag, xcg, wg, mug,  At, xct, wt, mut,  y0]   peak1=graphitic, peak2=turbostratic
        p0 = [ph * 0.3, 26.5, 0.30, 0.5,  ph * 0.8, 25.8, 1.00, 0.8,  base0]
        lower = [0.0, GRAPH_XC_BOUNDS[0], 0.02, 0.0,
                 0.0, 24.5, 0.05, 0.0,  0.0]
        upper = [np.inf, GRAPH_XC_BOUNDS[1], 3.0, 1.0,
                 np.inf, GRAPH_XC_BOUNDS[0], 3.0, 1.0,  ph]
        popt = self._fit(_dual_model_baseline, x, y, p0, (lower, upper),
                         "Method A bimodal")
        Ag, xcg, wg, mug, At, xct, wt, mut, y0 = popt

        dg, dt = self.bragg_d(xcg), self.bragg_d(xct)
        total = Ag + At
        Xg, Xt = Ag / total, At / total
        d_prime = Xg * dg + Xt * dt
        DG = self.maire_mering(d_prime)

        return {
            "method": "A",
            "method_name": "NETL paper standard — bimodal Pseudo-Voigt",
            "wavelength_angstrom": round(self.wavelength, 6),
            "baseline_y0": round(float(y0), 6),
            "graphitic": self._peak(Ag, xcg, wg, mug, dg),
            "turbostratic": self._peak(At, xct, wt, mut, dt),
            "area_fraction_graphitic": round(Xg, 6),
            "area_fraction_turbostratic": round(Xt, 6),
            "d_spacing_weighted_angstrom": round(d_prime, 6),
            "DG_percent": round(DG, 2),
        }

    # -- Method B: OriginLab PsdVoigt1 (single vs dual) ---------------------

    def _run_b(self) -> dict:
        x, y, _baseline = self.pattern.baseline_subtracted(*ANALYSIS_WINDOW)
        if len(x) < 10:
            raise FitError(
                f"Only {len(x)} point(s) in the {ANALYSIS_WINDOW} window — too few to fit."
            )
        ph = float(y.max())

        # --- Single-peak fit (NETL "one peak first"): one graphitic peak ---
        p0_s = [ph * 1.0, 26.5, 0.5, 0.5]
        lo_s = [0.0, GRAPH_XC_BOUNDS[0], 0.02, 0.0]
        hi_s = [np.inf, GRAPH_XC_BOUNDS[1], 3.0, 1.0]
        popt_s = self._fit(_single_model, x, y, p0_s, (lo_s, hi_s),
                           "Method B single-peak")
        As, xcs, ws, mus = popt_s
        r2_single = self._r2(y, _single_model(x, *popt_s))
        d_single = self.bragg_d(xcs)
        DG_single = self.maire_mering(d_single)

        # --- Dual-peak fit (NETL two-peak) with strict bounds --------------
        p0_d = [ph * 0.5, 26.5, 0.30, 0.5,  ph * 0.5, 25.8, 1.00, 0.8]
        lo_d = [0.0, GRAPH_XC_BOUNDS[0], 0.02, 0.0,
                0.0, TURBO_XC_BOUNDS[0], 0.05, 0.0]
        hi_d = [np.inf, GRAPH_XC_BOUNDS[1], 3.0, 1.0,
                np.inf, TURBO_XC_BOUNDS[1], 3.0, 1.0]
        popt_d = self._fit(_dual_model, x, y, p0_d, (lo_d, hi_d),
                           "Method B dual-peak")
        Ag, xcg, wg, mug, At, xct, wt, mut = popt_d
        r2_dual = self._r2(y, _dual_model(x, *popt_d))

        dg, dt = self.bragg_d(xcg), self.bragg_d(xct)
        total = Ag + At
        Xg, Xt = Ag / total, At / total
        d_prime = Xg * dg + Xt * dt
        DG_dual = self.maire_mering(d_prime)
        Lc = self.scherrer_lc(xcg, wg)

        # --- NETL "one-peak-first" recommendation --------------------------
        if r2_single >= SINGLE_FIT_R2_THRESHOLD:
            fit_rec, DG_rec = "single-peak", DG_single
        else:
            fit_rec, DG_rec = "dual-peak", DG_dual

        return {
            "method": "B",
            "method_name": "OriginLab PsdVoigt1 — single (legacy) vs dual (NETL)",
            "wavelength_angstrom": round(self.wavelength, 6),
            "baseline_region_deg": list(ANALYSIS_WINDOW),
            # dual / NETL (primary)
            "graphitic": self._peak(Ag, xcg, wg, mug, dg),
            "turbostratic": self._peak(At, xct, wt, mut, dt),
            "area_fraction_graphitic": round(Xg, 6),
            "area_fraction_turbostratic": round(Xt, 6),
            "d_spacing_weighted_angstrom": round(d_prime, 6),
            "DG_percent": round(DG_dual, 2),
            # single / legacy
            "single_peak": self._peak(As, xcs, ws, mus, d_single),
            "DG_single_percent": round(DG_single, 2),
            "dg_overestimation_percent": round(DG_single - DG_dual, 2),
            # microstructure
            "crystallite_height_Lc_angstrom": round(float(Lc), 2),
            # NETL one-peak-first procedure
            "single_peak_r2": round(r2_single, 5),
            "dual_peak_r2": round(r2_dual, 5),
            "fit_recommendation": fit_rec,
            "DG_recommended_percent": round(DG_rec, 2),
        }


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------

def analyze_text(text: str, method: str = "A",
                 wavelength: float = DEFAULT_WAVELENGTH) -> dict:
    analyzer = GraphitizationAnalyzer(XRDPattern.from_text(text), wavelength)
    return analyzer.run(method)


def analyze_file(path: str | Path, method: str = "A",
                 wavelength: float = DEFAULT_WAVELENGTH) -> dict:
    analyzer = GraphitizationAnalyzer(XRDPattern.from_file(path), wavelength)
    return analyzer.run(method)


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


def analyze_batch(filepaths: list[str], method: str = "A",
                  wavelength: float = DEFAULT_WAVELENGTH) -> list[dict]:
    """Analyse many files; each entry is results+``file`` or ``{file, error}``."""
    results: list[dict] = []
    for fp in filepaths:
        entry: dict = {"file": str(fp)}
        try:
            entry.update(analyze_file(fp, method, wavelength))
        except FileNotFoundError as exc:
            entry["error"] = f"file not found — '{exc.filename or fp}'"
        except (FitError, ValueError) as exc:
            entry["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"unexpected error — {exc}"
        results.append(entry)
    return results


_CSV_COLUMNS = (
    "file", "method", "wavelength_angstrom", "DG_percent",
    "fit_recommendation", "DG_recommended_percent",
    "d_spacing_weighted_angstrom", "graphitic_xc", "graphitic_w",
    "turbostratic_xc", "DG_single_percent", "single_peak_r2",
    "dg_overestimation_percent", "crystallite_height_Lc_angstrom", "error",
)


def _csv_row(entry: dict) -> dict:
    """Flatten a (possibly nested) result entry into CSV columns."""
    row = {"file": entry.get("file", ""), "method": entry.get("method", "")}
    if "error" in entry:
        row["error"] = entry["error"]
        return row
    row["wavelength_angstrom"] = entry.get("wavelength_angstrom")
    row["DG_percent"] = entry.get("DG_percent")
    row["fit_recommendation"] = entry.get("fit_recommendation")
    row["DG_recommended_percent"] = entry.get("DG_recommended_percent")
    row["d_spacing_weighted_angstrom"] = entry.get("d_spacing_weighted_angstrom")
    g = entry.get("graphitic", {})
    t = entry.get("turbostratic", {})
    row["graphitic_xc"] = g.get("xc")
    row["graphitic_w"] = g.get("w")
    row["turbostratic_xc"] = t.get("xc")
    row["DG_single_percent"] = entry.get("DG_single_percent")
    row["single_peak_r2"] = entry.get("single_peak_r2")
    row["dg_overestimation_percent"] = entry.get("dg_overestimation_percent")
    row["crystallite_height_Lc_angstrom"] = entry.get("crystallite_height_Lc_angstrom")
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

    if result["method"] == "manual":
        print("\n  Manual peak entry (NETL excel-sheet calculation)")
        _mp = lambda lbl, p: print(f"    {lbl:<16}: xc={p['xc']:.4f}°  A={p['A']:.4f}  "
                                   f"d={p['d_spacing_angstrom']:.6f} Å")
        _mp("Graphitic", result["graphitic"])
        if result["n_peaks"] == 2:
            _mp("Turbostratic", result["turbostratic"])
            print(f"    X_g = {result['area_fraction_graphitic']:.4f}   "
                  f"X_t = {result['area_fraction_turbostratic']:.4f}   "
                  f"d′ = {result['d_spacing_weighted_angstrom']:.6f} Å")
    elif result["method"] == "A":
        print("\n  Fitted Peaks (Pseudo-Voigt)")
        _print_peak("Graphitic", result["graphitic"])
        _print_peak("Turbostratic", result["turbostratic"])
        print(f"    baseline y0         : {result['baseline_y0']:.6f}")
        print("\n  Area Fractions / d-spacing")
        print(f"    X_g = {result['area_fraction_graphitic']:.4f}   "
              f"X_t = {result['area_fraction_turbostratic']:.4f}")
        print(f"    d′ (weighted)       : {result['d_spacing_weighted_angstrom']:.6f} Å")
    else:  # B
        rec = result["fit_recommendation"]
        print(f"\n  NETL one-peak-first: single R²={result['single_peak_r2']:.5f}  "
              f"dual R²={result['dual_peak_r2']:.5f}")
        print(f"  → recommended fit: {rec.upper()}  "
              f"(DG = {result['DG_recommended_percent']:.2f} %)")
        print("\n  Dual-Peak fit (NETL)")
        _print_peak("Graphitic", result["graphitic"])
        _print_peak("Turbostratic", result["turbostratic"])
        print(f"    X_g = {result['area_fraction_graphitic']:.4f}   "
              f"X_t = {result['area_fraction_turbostratic']:.4f}   "
              f"d′ = {result['d_spacing_weighted_angstrom']:.6f} Å")
        print("\n  Single-Peak fit (Legacy)")
        _print_peak("Graphitic", result["single_peak"])
        print(f"\n  DG% single = {result['DG_single_percent']:.2f} %   "
              f"DG% dual = {result['DG_percent']:.2f} %   "
              f"(overestimation {result['dg_overestimation_percent']:.2f})")
        print(f"  Crystallite height Lc : {result['crystallite_height_Lc_angstrom']:.2f} Å")

    print()
    print(SEP)
    if result["method"] == "B":
        print(f"  DG% (NETL recommended, {result['fit_recommendation']}) = "
              f"{result['DG_recommended_percent']:>7.2f} %")
    else:
        print(f"  Degree of Graphitization  DG% = {result['DG_percent']:>7.2f} %")
    print(SEP)


def _print_batch_table(results: list[dict]) -> None:
    SEP = "=" * 72
    print(SEP)
    print("  XRD Degree of Graphitization — Batch Summary")
    print(SEP)
    print(f"  {'File':<44}{'Method':<8}{'DG%':>9}")
    print("  " + "-" * 68)
    ok = 0
    for r in results:
        name = Path(r["file"]).name
        if len(name) > 42:
            name = name[:39] + "..."
        if "error" in r:
            print(f"  {name:<44}{'—':<8}{'ERROR':>9}")
            print(f"      ↳ {r['error']}")
        else:
            ok += 1
            print(f"  {name:<44}{r['method']:<8}{r['DG_percent']:>9.2f}")
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
            "  python xrd_analyzer.py sample.xy --method A\n"
            "  python xrd_analyzer.py sample.xy --method B --json\n"
            "  python xrd_analyzer.py *.xy --method B --csv results.csv\n"
            "  python xrd_analyzer.py --peaks 26.51:20.571,26.181:8.062\n"
        ),
    )
    parser.add_argument("filepaths", nargs="*", metavar="FILE_OR_DIR",
                        help="One or more .xy files and/or directories (expanded to *.xy).")
    parser.add_argument("--method", choices=METHODS, default="A",
                        help="A = NETL paper standard (default); "
                             "B = OriginLab PsdVoigt1 single-vs-dual + Lc.")
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
            result = analyze_file(fp, args.method)
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
    results = analyze_batch(files, args.method)
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
