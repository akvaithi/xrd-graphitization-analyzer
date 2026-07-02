"""
research/amorphous.py — amorphous-aware decomposition of the (002) region.

WHY THIS EXISTS
---------------
DG% (Maire–Mering, the shipping engine) is computed from peak *position* only —
the area-weighted d-spacing between the graphitic and turbostratic peaks. It
answers "how well-ordered is the stacked carbon?" It does **not** answer "how
much of the carbon is stacked at all?" Truly amorphous carbon scatters as a
broad diffuse band with no sharp peak, so it never enters the DG calculation —
it is "drowned out" beneath the graphitic + turbostratic peaks. A sample can
read DG ≈ 97% while a large mass fraction is still amorphous.

WHAT THIS DOES
--------------
PRIMARY (robust, 2 components + background): split the (002) scattering into a
**sharp crystalline graphitic peak** and a single **broad disordered band**
(the amorphous + turbostratic halo, which cannot be cleanly separated from each
other in a single pattern — see below):

    crystalline_fraction = A_sharp / (A_sharp + A_broad)
    disordered_fraction  = A_broad / (A_sharp + A_broad)

This is the classic XRD crystallinity index (sharp peak ÷ sharp + halo). It is
robust because there is no broad-vs-broad degeneracy: one narrow peak, one wide
band, well-separated FWHM ranges.

DIAGNOSTIC (``subdivide=True``, 3 components): additionally try to split the
broad band into a turbostratic and an amorphous sub-band. This is reported only
as a *diagnostic* and flagged ``broad_split_reliable``: from one lab pattern the
amorphous↔turbostratic split is genuinely non-unique (both are broad and
overlapping), so do not trust the individual numbers without Raman/TGA support.

IMPORTANT — every fraction here is a model-dependent **index**, NOT an absolute
weight percent. Diffuse amorphous scatter has a different mass-normalized
scattering power than crystalline graphite, so to convert any of these indices
to true crystalline-graphite wt% you must anchor to physical standards — that is
exactly what ``research/calibration.py`` is for.

SOURCES & ASSUMPTIONS
---------------------
The *method class* (deconvolve the (002) into a sharp crystalline peak + a broad
disordered band; take the area ratio as a crystallinity index) is standard
carbon-XRD practice. The *specific* window, bounds, and the one-broad-one-sharp
parameterization below are engineering choices, validated here only by internal
consistency (monotonic with treatment temperature on our own series, and an
unstable amorphous↔turbostratic sub-split — hence the diagnostic-only flag).

Literature the approach rests on:
  - Warren, B. E. "X-ray diffraction in random layer lattices." Phys. Rev. 59
    (1941) 693 — turbostratic (random-layer) carbon scattering.
  - Franklin, R. E. "Crystallite growth in graphitizing and non-graphitizing
    carbons." Proc. R. Soc. A 209 (1951) 196 — Lc / d002 crystallite parameters.
  - Maire, J. & Méring, J. "Graphitization of soft carbons." Chem. Phys. Carbon 6
    (1970) — the degree-of-graphitization / d-spacing relation (the engine's DG%).
  - Iwashita, N. et al. "Specification for a standard procedure of X-ray
    diffraction measurements on carbon materials." Carbon 42 (2004) 701–714 —
    windowing, background, and profile fitting of the carbon (002).
  - Lu, L., Sahajwalla, V., Kong, C. & Harris, D. "Quantitative X-ray diffraction
    analysis and its application to various coals." Carbon 39 (2001) 1821–1833 —
    deconvolving (002) into crystalline + amorphous bands and taking area ratios
    (the closest published analogue to this module's index).
  - Ruland, W. & Smarsly, B. "X-ray scattering of non-graphitic carbon."
    J. Appl. Cryst. 35 (2002) 624 — why a bare peak-area ratio is only an *index*:
    rigorous fractions require the total coherent (incl. diffuse) scattering.

Key assumption flagged by Ruland & Smarsly: this index treats the area ratio as
a stand-in for composition, which ignores that amorphous and crystalline carbon
do not scatter equally per gram. That bias is constant-ish for a fixed feedstock,
so the index *ranks* samples reliably but is not a mass fraction until calibrated.
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit

from _shared import D_GRAPHITE, D_TURBOSTRATIC, DEFAULT_WAVELENGTH, pseudo_voigt, sort_pattern

# Decomposition window: low enough to seat the broad disordered band, high enough
# to clear the (002), but short of the (100)/(101) graphite + Fe lines (>~40°).
# The very-low-angle air/beamstop upturn (<~15°) is excluded.
AMORPHOUS_WINDOW: tuple[float, float] = (16.0, 31.0)


def _edge_baseline_subtract(x, y):
    """Subtract a straight line through the window-edge means, clip ≥ 0 — the
    exact operation of ``XRDPattern.baseline_subtracted`` / Swift
    ``baselineSubtracted``. Pre-subtracting a deterministic background (instead of
    fitting a free one) removes the broad-band-vs-background degeneracy that would
    otherwise let two optimizers land on different area splits — which is what
    keeps the Python and Swift crystallinity numbers in lockstep."""
    n_edge = max(3, len(x) // 20)
    xl, yl = x[:n_edge].mean(), y[:n_edge].mean()
    xr, yr = x[-n_edge:].mean(), y[-n_edge:].mean()
    slope = (yr - yl) / (xr - xl) if xr != xl else 0.0
    return np.clip(y - (yl + slope * (x - xl)), 0.0, None)


def _model2(x, A_d, xc_d, w_d, mu_d, A_g, xc_g, w_g, mu_g):
    """One broad disordered band + one sharp graphitic peak (on a pre-subtracted
    baseline — no free background term)."""
    return (pseudo_voigt(x, A_d, xc_d, w_d, mu_d)
            + pseudo_voigt(x, A_g, xc_g, w_g, mu_g))


def _model3(x, A_am, xc_am, w_am, A_t, xc_t, w_t, A_g, xc_g, w_g, mu_g):
    """Amorphous (very broad, Gaussian) + turbostratic (medium, Lorentzian) +
    sharp graphitic, on a pre-subtracted baseline. The broad components are given
    *non-overlapping* FWHM ranges by the caller's bounds, which is the only thing
    that keeps the amorphous/turbostratic split from being fully degenerate (it
    still is *partly* degenerate — hence the diagnostic-only flag)."""
    return (pseudo_voigt(x, A_am, xc_am, w_am, 0.0)
            + pseudo_voigt(x, A_t, xc_t, w_t, 1.0)
            + pseudo_voigt(x, A_g, xc_g, w_g, mu_g))


def _bragg_d(tt_deg: float, wavelength: float) -> float:
    return wavelength / (2.0 * np.sin(np.deg2rad(tt_deg / 2.0)))


def _r2(y, yfit) -> float:
    ss_res = float(np.sum((y - yfit) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def _fit(model, x, y, p0, bounds, label):
    with warnings.catch_warnings():
        warnings.simplefilter("error", OptimizeWarning)
        try:
            popt, _ = curve_fit(model, x, y, p0=p0, bounds=bounds, maxfev=40000)
        except (OptimizeWarning, RuntimeError, ValueError) as exc:
            raise ValueError(f"{label} did not converge — {exc}")
    return popt


def decompose(two_theta, intensity, *, wavelength: float = DEFAULT_WAVELENGTH,
              window: tuple[float, float] = AMORPHOUS_WINDOW,
              subdivide: bool = False) -> dict:
    """Fit the (002) region; return component areas + the crystallinity index.

    Areas are the integrated pseudo-Voigt ``A`` parameters (the line shape is
    area-normalized, so ``A`` *is* the integrated intensity of that component).
    Set ``subdivide=True`` to also attempt the (uncertain) amorphous↔turbostratic
    split of the broad band.
    """
    x, y = sort_pattern(two_theta, intensity)
    m = (x >= window[0]) & (x <= window[1])
    x, y = x[m], y[m]
    if len(x) < 12:
        raise ValueError(f"only {len(x)} point(s) in {window}° — too few to decompose.")

    # deterministic edge-baseline subtraction (shared with the Swift port) so the
    # peak areas — not a free background — carry the disordered/crystalline split
    y = _edge_baseline_subtract(x, y)
    ph = float(y.max())

    # --- primary 2-component fit (robust) ---------------------------------
    #     A_d            xc_d  w_d  mu_d  A_g            xc_g  w_g   mu_g
    p0 = [ph * 0.4 * 3.0, 25.5, 3.0, 0.5, ph * 0.8 * 0.3, 26.5, 0.3, 0.6]
    lo = [0.0, 22.0, 1.5, 0.0, 0.0, 26.2, 0.05, 0.0]
    hi = [np.inf, 26.3, 12.0, 1.0, np.inf, 26.9, 0.8, 1.0]
    A_d, xc_d, w_d, mu_d, A_g, xc_g, w_g, mu_g = _fit(
        _model2, x, y, p0, (lo, hi), "2-component decomposition")
    r2 = _r2(y, _model2(x, A_d, xc_d, w_d, mu_d, A_g, xc_g, w_g, mu_g))

    A_d, A_g = float(A_d), float(A_g)
    total = A_d + A_g or 1.0
    out = {
        "window_deg": list(window),
        "fit_r2": round(r2, 5),
        "areas": {"disordered_broad": round(A_d, 4), "graphitic_sharp": round(A_g, 4)},
        "graphitic_center_2theta": round(float(xc_g), 3),
        "graphitic_fwhm_deg": round(float(w_g), 3),
        # headline numbers
        "crystalline_fraction": round(A_g / total, 4),   # sharp peak ÷ total
        "disordered_fraction": round(A_d / total, 4),     # amorphous + turbostratic halo
        "crystallinity_index": round(A_g / total, 4),     # alias
        "d_graphitic_angstrom": round(_bragg_d(float(xc_g), wavelength), 6),
        "is_absolute_wt_pct": False,
        "note": ("model-dependent XRD index (sharp crystalline ÷ total 002 "
                 "scattering); anchor to physical standards via "
                 "research/calibration.py for absolute crystalline wt%"),
    }

    # --- optional 3-component subdivision (diagnostic only) ----------------
    if subdivide:
        # amorphous very broad (w 4–12), turbostratic medium (w 0.6–2.5), graphitic sharp.
        p3 = [ph * 0.3 * 6.0, 24.0, 6.0, ph * 0.3 * 1.2, 25.9, 1.2,
              ph * 0.8 * 0.3, 26.5, 0.3, 0.6]
        l3 = [0.0, 19.0, 4.0, 0.0, 25.2, 0.6, 0.0, 26.2, 0.05, 0.0]
        h3 = [np.inf, 26.0, 12.0, np.inf, 26.2, 2.5, np.inf, 26.9, 0.8, 1.0]
        try:
            p = _fit(_model3, x, y, p3, (l3, h3), "3-component subdivision")
            A_am, A_t, A_g3 = float(p[0]), float(p[3]), float(p[6])
            tot3 = A_am + A_t + A_g3 or 1.0
            r2_3 = _r2(y, _model3(x, *p))
            out["broad_subdivision"] = {
                "amorphous_fraction": round(A_am / tot3, 4),
                "turbostratic_fraction": round(A_t / tot3, 4),
                "graphitic_fraction": round(A_g3 / tot3, 4),
                "fit_r2": round(r2_3, 5),
                # only marginally better R² than the 2-component fit ⇒ the extra
                # broad band isn't justified by the data, so the split is unreliable
                "broad_split_reliable": bool(r2_3 - r2 > 0.002),
                "note": "amorphous↔turbostratic split is non-unique from one pattern; "
                        "confirm with Raman (I_D/I_G) or TGA burn-off.",
            }
        except ValueError:
            out["broad_subdivision"] = {"error": "3-component fit did not converge"}

    return out


def crystallinity_index(two_theta, intensity, **kw) -> float:
    """Convenience: the crystalline (sharp graphitic) fraction, 0–1."""
    return decompose(two_theta, intensity, **kw)["crystallinity_index"]


# Sanity references used by tests / callers.
_D_GRAPHITE = D_GRAPHITE
_D_TURBOSTRATIC = D_TURBOSTRATIC


if __name__ == "__main__":  # quick manual check on one file
    import json
    import sys

    from _shared import XRDPattern

    if len(sys.argv) < 2:
        print("usage: python research/amorphous.py <file.xy>")
        raise SystemExit(2)
    p = XRDPattern.from_file(sys.argv[1])
    print(json.dumps(decompose(p.two_theta, p.intensity, subdivide=True), indent=2))
