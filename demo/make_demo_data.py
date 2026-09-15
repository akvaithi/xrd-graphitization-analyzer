#!/usr/bin/env python3
"""
make_demo_data.py — Synthetic but physically-consistent XRD `.xy` scans for
demoing the analyzer without shipping any real lab data.

Each pattern is built *forward* from the structure the engine is supposed to
recover. Every run is specified by the two things that physically define the
(002) complex — the graphitic d-spacing d_g and the turbostratic area fraction
f_t — from which the expected answer follows by Maire-Mering:

    d′ = (1 - f_t)·d_g + f_t·d_turbo        DG% = 100·(3.440 - d′) / 0.086

Bragg is inverted for both peak positions, the two components are laid down as
OriginLab PsdVoigt1 line shapes scaled to that *area* ratio, the
(100)/(101)/(004) carbon reflections go on a realistic background, residual
catalyst phases are added for unwashed runs, and Poisson counting noise is
applied on top.

Since the areas are set explicitly, the analyzer reads these back close to the
expected DG — the residual gap is the fit window truncating the broad
turbostratic band, which is a real property of the NETL method, not an artifact
of the generator. See `demo/README.md` for the measured values.

Run:  python3 demo/make_demo_data.py        → writes demo/data/*.xy
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

LAMBDA = 1.54187          # Å, Cu Kα (weighted) — the constant the engine uses
D_GRAPHITE = 3.354        # Å, ideal graphite d002
D_TURBO = 3.440           # Å, fully turbostratic carbon

TWO_THETA = np.arange(20.0, 60.0 + 1e-9, 0.02)
RNG = np.random.default_rng(20260825)

# Area under PsdVoigt1 of unit height and unit FWHM, as a function of mu
# (mu = 1 → pure Lorentzian, π/2; mu = 0 → pure Gaussian, ½√(π/ln2)).
_LOR_AREA = math.pi / 2.0
_GAU_AREA = 0.5 * math.sqrt(math.pi / math.log(2.0))


def _shape_area(mu: float) -> float:
    return mu * _LOR_AREA + (1.0 - mu) * _GAU_AREA


# --- structure → peak positions ----------------------------------------------

def expected_dg(d_g: float, turbo_frac: float) -> float:
    """Maire-Mering DG% for the area-weighted d′ of the two components."""
    d_prime = (1.0 - turbo_frac) * d_g + turbo_frac * D_TURBO
    return 100.0 * (D_TURBO - d_prime) / (D_TURBO - D_GRAPHITE)


def two_theta_from_d(d: float) -> float:
    """Invert Bragg for n = 1."""
    return 2.0 * math.degrees(math.asin(LAMBDA / (2.0 * d)))


# --- line shapes --------------------------------------------------------------

def psd_voigt(x: np.ndarray, amp: float, xc: float, fwhm: float, mu: float) -> np.ndarray:
    """OriginLab PsdVoigt1 convention, `amp` = peak height."""
    lor = 1.0 / (1.0 + 4.0 * ((x - xc) / fwhm) ** 2)
    gau = np.exp(-4.0 * math.log(2.0) * ((x - xc) / fwhm) ** 2)
    return amp * (mu * lor + (1.0 - mu) * gau)


def background(x: np.ndarray, level: float) -> np.ndarray:
    """Gentle air-scatter roll-off at low angle plus a flat detector floor."""
    return level * (1.0 + 1.2 * np.exp(-(x - 20.0) / 15.0)) + 0.4 * level


# --- one pattern --------------------------------------------------------------

def make_pattern(
    d_g: float,
    turbo_frac: float,
    *,
    graphitic_fwhm: float,
    turbo_fwhm: float,
    peak_counts: float = 9000.0,
    bg_level: float = 60.0,
    residual: dict[float, float] | None = None,
) -> tuple[np.ndarray, float, float]:
    """Build one intensity trace. Returns (y, graphitic 2θ, expected DG%)."""
    dg = expected_dg(d_g, turbo_frac)
    xc_g = two_theta_from_d(d_g)
    xc_t = two_theta_from_d(D_TURBO)

    mu_g, mu_t = 1.0, 0.45      # NETL convention: turbostratic peak is Lorentzian-ish

    # Heights chosen so the *areas* carry the requested fractions.
    area_g = peak_counts * graphitic_fwhm * _shape_area(mu_g)
    area_t = area_g * turbo_frac / (1.0 - turbo_frac)
    amp_t = area_t / (turbo_fwhm * _shape_area(mu_t))

    y = background(TWO_THETA, bg_level)
    y += psd_voigt(TWO_THETA, peak_counts, xc_g, graphitic_fwhm, mu_g)
    y += psd_voigt(TWO_THETA, amp_t, xc_t, turbo_fwhm, mu_t)

    # Other carbon reflections — sharpen and grow with ordering
    order = dg / 100.0
    tall = peak_counts * (1.0 - turbo_frac)
    y += psd_voigt(TWO_THETA, tall * 0.060 * order, 42.4, 1.9 - 0.9 * order, 0.7)      # (100)
    y += psd_voigt(TWO_THETA, tall * 0.032 * order, 44.6, 1.5 - 0.7 * order, 0.7)      # (101)
    y += psd_voigt(TWO_THETA, tall * 0.050 * order ** 2, 54.6, 1.2 - 0.5 * order, 0.8)  # (004)

    # Residual crystalline phases (unwashed samples): {2theta: height / peak_counts}
    for pos, rel in (residual or {}).items():
        y += psd_voigt(TWO_THETA, peak_counts * rel, pos, 0.42, 0.5)

    y = RNG.poisson(np.clip(y, 1.0, None)).astype(float)
    return y, xc_g, dg


def write_xy(path: Path, y: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for x, v in zip(TWO_THETA, y):
            fh.write(f"{x:.2f}\t{v:.4f}\n")


# --- the demo set -------------------------------------------------------------
# Filenames follow the lab's loose convention that run_parser.py understands:
# carbon source, Fe ratio, temperature, hold time, wash state. These are
# catalyst-only (Fe) runs — the published baseline chemistry.
#
# Residual-phase line positions are taken from the SAME lattice constants the
# engine's internal-standard calibrator uses (`_phase_lines`), so a demo scan
# that is deliberately mis-mounted can actually be recovered by it. All of these
# sit outside the 24-28.5° fit window, so they flag an incomplete wash without
# moving DG. Heights are fractions of the (002) peak height.
_FE3C = {                     # cementite Fe3C, ICDD PDF 00-035-0772
    37.672: 0.06, 42.924: 0.07, 43.790: 0.11,
    45.913: 0.05, 49.167: 0.06, 51.877: 0.04,
}
_ALPHA_FE = {44.712: 0.14}    # metallic alpha-Fe (110), ICDD PDF 00-006-0696

# `counts` is the graphitic peak height. It rises with ordering as well as
# sharpening: scattering power that was spread across a broad amorphous band in
# the feedstock concentrates into a narrow reflection in the product.
RUNS = [
    # name,                                  d_g (Å),  f_t,   g_fwhm, t_fwhm, counts, residual
    # Negative control: the disordered feedstock. Essentially all turbostratic,
    # d002 well outside the graphitic bound — the two-peak NETL fit has no
    # meaningful answer here and DG comes back nonsensical. That is the point:
    # DG% measures *ordering*, not *how much* graphite there is, which is what
    # the crystallinity index exists to cover.
    ("GPC-raw-calcined.xy",                   3.4200, 0.900, 4.20, 5.00,  400, {}),
    ("GPC-6Fe-1000C-5h-washed.xy",            3.3780, 0.240, 1.55, 3.90, 3500, {}),
    ("GPC-6Fe-1100C-5h-washed.xy",            3.3670, 0.150, 1.05, 3.60, 5000, {}),
    ("GPC-6Fe-1200C-1h-washed.xy",            3.3635, 0.110, 0.95, 3.40, 6000, {}),
    ("GPC-6Fe-1200C-3h-washed.xy",            3.3600, 0.075, 0.81, 3.30, 6600, {}),
    ("GPC-6Fe-1200C-5h-washed.xy",            3.3590, 0.075, 0.72, 3.40, 7000, {}),
    ("GPC-6Fe-1300C-5h-washed.xy",            3.3570, 0.040, 0.55, 3.20, 8000, {}),
    ("GPC-6Fe-1400C-5h-washed.xy",            3.3560, 0.025, 0.46, 3.00, 8600, {}),
    ("GPC-6Fe-1600C-24h-washed.xy",           3.3552, 0.014, 0.38, 2.80, 9000, {}),
    # Same run as the 1300 °C above, but scanned before the acid wash — the
    # data-quality flag should fire while DG stays put.
    ("GPC-6Fe-1300C-5h-unwashed.xy",          3.3570, 0.040, 0.55, 3.20, 8000, {**_FE3C, **_ALPHA_FE}),
    # Deliberately misaligned mount: every line shifted, so DG reads
    # unphysically high until the internal standard puts it back.
    ("GPC-6Fe-1400C-5h-unwashed-offaxis.xy",  3.3560, 0.025, 0.46, 3.00, 8600, {**_FE3C, **_ALPHA_FE}),
]

OFFSET_DEG = 0.09   # specimen displacement, applied to the off-axis run only


def main() -> None:
    out = Path(__file__).resolve().parent / "data"
    print(f"{'file':42s} {'d_g (Å)':>9s} {'f_turbo':>8s} {'2θ(002)':>9s} {'DG%':>7s}")
    for name, d_g, f_t, g_fwhm, t_fwhm, counts, residual in RUNS:
        y, xc_g, dg = make_pattern(
            d_g, f_t, graphitic_fwhm=g_fwhm, turbo_fwhm=t_fwhm,
            peak_counts=counts, residual=residual
        )
        if "offaxis" in name:
            # Specimen displacement shifts the whole pattern by a near-constant Δ2θ
            y = np.interp(TWO_THETA - OFFSET_DEG, TWO_THETA, y)
        write_xy(out / name, y)
        print(f"{name:42s} {d_g:9.4f} {f_t:8.3f} {xc_g:9.3f} {dg:7.1f}")
    print(f"\n{len(RUNS)} files → {out}")


if __name__ == "__main__":
    main()
