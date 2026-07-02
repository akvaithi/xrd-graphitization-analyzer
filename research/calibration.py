"""
research/calibration.py — turn the XRD crystallinity *index* into absolute
crystalline-graphite **wt%** by anchoring it to physical standards.

THE PROBLEM IT SOLVES
---------------------
``amorphous.crystallinity_index`` gives a model-dependent number in [0, 1]. It
is *not* a weight fraction: amorphous carbon and crystalline graphite do not
diffract equally per gram, and raw peak intensities are not comparable across
samples (these scans are auto-scaled — see ``_shared.total_scatter``). The only
rigorous way to read "X% of this carbon is crystalline graphite" off an XRD
pattern is a calibration against samples of *known* composition.

TWO CALIBRATION MODES
---------------------
1. ``mixture`` — scan physical blends of one pure amorphous end-member (raw PC)
   and one pure crystalline end-member (commercial synthetic graphite) at known
   mass fractions (e.g. 0/25/50/75/100 wt%). Regress a normalized XRD metric vs
   the known graphite wt% → a calibration line. RECOMMENDED first step: cheapest,
   uses materials already on hand. Assumes the product's crystalline/amorphous
   phases resemble the end-members.

2. ``internal_standard`` — spike each sample with a known wt% of a crystalline
   standard (Si or corundum α-Al₂O₃) whose strongest line is clear of carbon.
   The graphite-002 / standard-line intensity ratio, scaled by a reference
   intensity ratio (RIR), gives absolute crystalline-graphite wt% directly, and
   the amorphous content follows "by difference" (100 − Σcrystalline − impurity).
   More work per sample, but free of the mixture-similarity assumption — use it
   to validate the mixture curve.

A manifest CSV drives it:  ``file, graphite_wt_pct[, standard_wt_pct]``.

Run ``python research/calibration.py --selftest`` to validate the whole pipeline
on synthetic mixtures *before any real standards exist* — it builds known blends,
fits the calibration, and checks that it recovers the known wt%.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from _shared import XRDPattern, total_scatter
from amorphous import AMORPHOUS_WINDOW, decompose

# ---------------------------------------------------------------------------
# Metrics — every one is normalized so it is comparable across samples.
# ---------------------------------------------------------------------------

def norm_002_area(two_theta, intensity) -> float:
    """Sharp graphitic-(002) area ÷ total integrated scatter (10–90°).

    The numerator is the *amount* of crystalline graphite (integrated peak area);
    the denominator divides out specimen mass / packing / instrument auto-scale,
    so this is the right cross-sample intensity yardstick. This is the default
    calibration metric for the ``mixture`` mode.
    """
    d = decompose(two_theta, intensity)
    sharp = d["areas"]["graphitic_sharp"]
    tot = total_scatter(two_theta, intensity)
    return float(sharp / tot) if tot > 0 else 0.0


def crystallinity_metric(two_theta, intensity) -> float:
    """The dimensionless crystallinity index (sharp ÷ total 002 scattering)."""
    return decompose(two_theta, intensity)["crystallinity_index"]


METRICS = {"norm_002_area": norm_002_area, "crystallinity": crystallinity_metric}


# ---------------------------------------------------------------------------
# Linear calibration (metric ↔ known wt%) with prediction uncertainty.
# ---------------------------------------------------------------------------

def _poly_fit_stats(x, y, degree: int) -> dict:
    coeffs = np.polyfit(x, y, degree)
    yhat = np.polyval(coeffs, x)
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    n, p = x.size, degree
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / (n - p - 1) if n - p - 1 > 0 else r2
    sigma = float(np.sqrt(ss_res / max(n - p - 1, 1)))
    return {"coeffs": [float(c) for c in coeffs], "r2": r2, "adj_r2": adj_r2,
            "predict_sigma_wt_pct": sigma}


def fit_calibration(metric_values, known_wt_pct, degree: int | str = "auto") -> dict:
    """Fit ``wt% = poly(metric)`` and report fit quality + a 1σ prediction error.

    ``degree="auto"`` compares a line and a quadratic by *adjusted* R² and keeps
    the quadratic only if it genuinely improves the fit (≥0.01). A mild curvature
    is expected for the ``norm_002_area`` metric, because its total-scatter
    denominator also varies with composition — so the metric↔wt% map is a smooth
    rational curve, not a straight line. The fit stores wt% as a function of the
    metric, so applying it is a direct polynomial evaluation (no inversion)."""
    x = np.asarray(metric_values, float)
    y = np.asarray(known_wt_pct, float)
    if x.size < 2:
        raise ValueError("need ≥ 2 calibration points.")
    if degree == "auto":
        lin = _poly_fit_stats(x, y, 1)
        if x.size >= 4:
            quad = _poly_fit_stats(x, y, 2)
            chosen, deg = ((quad, 2) if quad["adj_r2"] - lin["adj_r2"] >= 0.01
                           else (lin, 1))
        else:
            chosen, deg = lin, 1
    else:
        deg = int(degree)
        chosen = _poly_fit_stats(x, y, deg)
    return {"mode": "polynomial", "degree": deg, "coeffs": chosen["coeffs"],
            "r2": round(chosen["r2"], 5),
            "predict_sigma_wt_pct": round(chosen["predict_sigma_wt_pct"], 3),
            "n_points": int(x.size),
            "metric_range": [round(float(x.min()), 5), round(float(x.max()), 5)]}


def apply_calibration(cal: dict, metric_value: float) -> dict:
    """Read crystalline-graphite wt% (± 1σ) off a fitted calibration."""
    wt = float(np.polyval(cal["coeffs"], float(metric_value)))
    wt = max(0.0, min(100.0, wt))
    extrap = not (cal["metric_range"][0] <= metric_value <= cal["metric_range"][1])
    return {"crystalline_graphite_wt_pct": round(wt, 2),
            "sigma_wt_pct": cal.get("predict_sigma_wt_pct"),
            "extrapolated": bool(extrap)}


# ---------------------------------------------------------------------------
# Internal-standard (RIR) mode — absolute wt%, amorphous by difference.
# ---------------------------------------------------------------------------

# Reference-intensity-ratio of graphite vs the standard (I/Ic). Placeholder
# defaults; replace with a measured RIR once a spiked standard is run. Graphite
# RIR ≈ 3–4 in databases; corundum is the RIR reference (1.0 by definition).
DEFAULT_RIR = {"corundum": 3.4, "Si": 4.7}


def internal_standard_wt_pct(sample_graphite_area: float, standard_area: float,
                             standard_wt_pct: float, rir: float) -> dict:
    """Absolute crystalline-graphite wt% from a spiked internal standard.

        W_graphite = (I_graphite / I_standard) · (W_standard / RIR)

    Amorphous-by-difference is then ``100 − W_graphite − W_standard`` (minus any
    quantified crystalline impurity), and is left to the caller who knows the
    impurity load. Returns the graphite wt% on the *as-spiked* basis.
    """
    if standard_area <= 0 or rir <= 0:
        raise ValueError("standard_area and rir must be positive.")
    w = (sample_graphite_area / standard_area) * (standard_wt_pct / rir)
    return {"crystalline_graphite_wt_pct_as_spiked": round(float(w), 3),
            "rir": rir, "standard_wt_pct": standard_wt_pct}


# ---------------------------------------------------------------------------
# Manifest-driven build / apply
# ---------------------------------------------------------------------------

def _load_manifest(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    return rows


def build_from_manifest(manifest_csv: str, *, metric: str = "norm_002_area",
                        base_dir: str | None = None) -> dict:
    """Build a ``mixture`` calibration from a CSV of known-wt% standard scans."""
    fn = METRICS[metric]
    base = Path(base_dir) if base_dir else Path(manifest_csv).resolve().parent
    mvals, wts, used = [], [], []
    for row in _load_manifest(manifest_csv):
        fp = (base / row["file"]) if not Path(row["file"]).is_absolute() else Path(row["file"])
        p = XRDPattern.from_file(fp)
        mvals.append(fn(p.two_theta, p.intensity))
        wts.append(float(row["graphite_wt_pct"]))
        used.append({"file": row["file"], "metric": round(mvals[-1], 6),
                     "graphite_wt_pct": wts[-1]})
    cal = fit_calibration(mvals, wts)
    cal.update({"metric": metric, "window_deg": list(AMORPHOUS_WINDOW), "points": used})
    return cal


# ---------------------------------------------------------------------------
# Synthetic self-test (no real standards required)
# ---------------------------------------------------------------------------

def _synthetic_pattern(graphite_frac: float, *, seed: int = 0, noise: float = 0.01):
    """A fake 10–90° pattern = graphite_frac·(crystalline end-member) +
    (1−graphite_frac)·(amorphous end-member). The crystalline end-member has a
    sharp 002 + higher-angle lines; the amorphous one is a broad hump. Used only
    to prove the calibration math recovers a known composition."""
    from _shared import pseudo_voigt
    x = np.arange(10.0, 90.0, 0.04)
    rng = np.random.default_rng(seed)
    # crystalline graphite end-member (unit "amount")
    cryst = (pseudo_voigt(x, 100.0, 26.54, 0.18, 0.6)   # sharp 002
             + pseudo_voigt(x, 8.0, 42.4, 0.4, 0.5)     # (100)
             + pseudo_voigt(x, 10.0, 44.6, 0.4, 0.5)    # (101)
             + pseudo_voigt(x, 6.0, 54.7, 0.5, 0.5))    # (004)
    # amorphous end-member: broad hump, same total scattering power
    amorph = pseudo_voigt(x, 100.0, 24.0, 7.0, 0.2) + pseudo_voigt(x, 30.0, 43.0, 12.0, 0.3)
    y = graphite_frac * cryst + (1.0 - graphite_frac) * amorph
    y = y + 0.5 + noise * float(y.max()) * rng.standard_normal(x.size)
    return x, np.clip(y, 0.0, None)


def selftest(verbose: bool = True) -> dict:
    """Build a calibration on synthetic blends, then predict held-out blends."""
    train_w = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    test_w = [0.1, 0.5, 0.9]
    mvals = [norm_002_area(*_synthetic_pattern(w, seed=i)) for i, w in enumerate(train_w)]
    cal = fit_calibration(mvals, [w * 100 for w in train_w])
    errs = []
    rows = []
    for j, w in enumerate(test_w):
        x, y = _synthetic_pattern(w, seed=100 + j)
        pred = apply_calibration(cal, norm_002_area(x, y))
        err = abs(pred["crystalline_graphite_wt_pct"] - w * 100)
        errs.append(err)
        rows.append((w * 100, pred["crystalline_graphite_wt_pct"], err))
    mae = float(np.mean(errs))
    result = {"calibration": cal, "test_mae_wt_pct": round(mae, 2),
              "passed": bool(cal["r2"] > 0.98 and mae < 5.0)}
    if verbose:
        print(f"calibration: degree={cal['degree']} coeffs={[round(c,3) for c in cal['coeffs']]} "
              f"R²={cal['r2']:.4f} σ={cal['predict_sigma_wt_pct']:.2f} wt%")
        for known, pred, err in rows:
            print(f"  known {known:5.1f} → predicted {pred:6.2f}  (err {err:4.2f})")
        print(f"held-out MAE = {mae:.2f} wt%   ->  {'PASS' if result['passed'] else 'FAIL'}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="XRD crystalline-graphite wt% calibration.")
    ap.add_argument("--selftest", action="store_true",
                    help="validate the pipeline on synthetic mixtures (no data needed).")
    ap.add_argument("--manifest", metavar="CSV",
                    help="build a mixture calibration from file,graphite_wt_pct rows.")
    ap.add_argument("--metric", default="norm_002_area", choices=list(METRICS),
                    help="calibration metric (default: norm_002_area).")
    ap.add_argument("--out", metavar="JSON", help="write the fitted calibration to JSON.")
    args = ap.parse_args()

    if args.selftest:
        r = selftest()
        raise SystemExit(0 if r["passed"] else 1)
    if args.manifest:
        cal = build_from_manifest(args.manifest, metric=args.metric)
        text = json.dumps(cal, indent=2)
        if args.out:
            Path(args.out).write_text(text)
            print(f"wrote calibration → {args.out}  (R²={cal['r2']:.4f})")
        else:
            print(text)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
