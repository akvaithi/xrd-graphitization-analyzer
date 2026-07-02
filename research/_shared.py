"""
research/_shared.py — bridge to the validated DG engine + normalization helpers.

The ``research/`` package is intentionally separate from the shipping engine
([xrd_analyzer.py]) so this exploratory work never touches the validated DG
method or the Swift parity. This module does two jobs:

1. Put the repo root on ``sys.path`` so ``research/`` scripts can ``import`` the
   engine whether run as ``python research/foo.py`` or ``python -m research.foo``.
2. Provide the cross-sample **normalization** primitives that every metric here
   depends on. Raw XRD intensities are meaningless across samples (these scans
   are auto-scaled — Imax ranges ~150–600 for chemically similar material), so a
   "peak is taller therefore more graphite" comparison is invalid until the
   pattern is put on a common footing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.integrate import trapezoid

# --- 1. make the engine importable -----------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Re-export the engine pieces the research code reuses, so callers can do
# ``from _shared import XRDPattern, fit_netl`` without a second path dance.
from xrd_analyzer import (  # noqa: E402
    DEFAULT_WAVELENGTH,
    D_GRAPHITE,
    D_TURBOSTRATIC,
    FitError,
    XRDPattern,
    expand_xy_inputs,
    fit_netl,
    pseudo_voigt,
    scan_impurities,
)

DATA_DIR = _REPO_ROOT / "DATA" / "xrd scans"

# Total-scatter normalization window. Wide enough to capture the amorphous band,
# the (002), and the higher-angle graphite/residue lines, so the integral tracks
# the total diffracting material in the beam (a per-sample intensity yardstick).
TOTAL_SCATTER_WINDOW = (10.0, 90.0)


def sort_pattern(two_theta, intensity):
    """Return (x, y) sorted by ascending 2θ — fits/integrals assume monotone x."""
    x = np.asarray(two_theta, float)
    y = np.asarray(intensity, float)
    o = np.argsort(x)
    return x[o], y[o]


def integrate(two_theta, intensity, lo: float, hi: float,
              baseline: bool = True) -> float:
    """Trapezoidal area under [lo, hi]. With ``baseline``, subtract the straight
    line between the window edges first (removes the constant air/Compton floor
    so the integral reflects the diffracted signal, not the detector pedestal)."""
    x, y = sort_pattern(two_theta, intensity)
    m = (x >= lo) & (x <= hi)
    if m.sum() < 2:
        return 0.0
    xx, yy = x[m], y[m]
    if baseline:
        yl, yr = float(yy[0]), float(yy[-1])
        base = yl + (yr - yl) * (xx - xx[0]) / (xx[-1] - xx[0] or 1.0)
        yy = np.clip(yy - base, 0.0, None)
    return float(trapezoid(yy, xx))


def total_scatter(two_theta, intensity,
                  window: tuple[float, float] = TOTAL_SCATTER_WINDOW) -> float:
    """Integrated scatter over the wide window — the per-sample normalizer.

    Use it to put intensity-derived metrics on a common scale: a *normalized*
    (002) area (002-area ÷ total-scatter) is comparable across samples that the
    raw area is not, because it divides out specimen mass / packing / auto-scale.
    """
    return integrate(two_theta, intensity, window[0], window[1], baseline=False)
