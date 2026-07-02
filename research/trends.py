"""
research/trends.py — batch EDA: how do the XRD observables move with the
synthesis parameters (temperature, time, Fe%, CaCO₃, grade)?

For every ``.xy`` in a directory it pulls the synthesis recipe straight out of
the filename ([run_parser.py]) and computes, per scan:

    DG_percent          ordering quality (engine fit_netl, 2-peak)
    crystalline_fraction *amount* of crystalline graphite (amorphous.decompose)
    disordered_fraction  amorphous + turbostratic halo
    norm_002_area        sharp-(002) area ÷ total scatter  ← the comparable one
    graphitic_xc / fwhm / Lc   position, width, crystallite height
    impurity_verdict     residual Fe/Ca/S flag (engine scan_impurities)

THE HEADLINE LESSON it is built to expose: **raw** peak height/area is NOT
comparable across these scans (they are auto-scaled — Imax ranges ~150–600 for
chemically similar material), so "taller peak ⇒ more graphite" is a trap. Only
the *normalized* ``norm_002_area`` and the dimensionless fractions are valid
cross-sample measures — and they, not DG%, are what reveal residual amorphous
carbon. The CSV reports both ``raw_002_height`` and ``norm_002_area`` so the gap
is visible.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

from _shared import (DATA_DIR, FitError, XRDPattern, expand_xy_inputs, fit_netl,
                     integrate, scan_impurities, total_scatter)
from amorphous import decompose

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from run_parser import parse_run_filename  # noqa: E402

# CSV schema. Categorical recipe fields, then numeric factors, then metrics.
COLUMNS = [
    "filename", "display_name", "carbon_type", "form", "wash",
    "temperature_C", "time_h", "fe_ratio", "caco3_ratio", "carbon_ratio",
    "DG_percent", "crystalline_fraction", "disordered_fraction",
    "norm_002_area", "raw_002_height", "graphitic_xc", "graphitic_fwhm",
    "Lc_angstrom", "decomp_r2", "impurity_verdict", "worst_impurity_pct", "error",
]
# Numeric synthesis factors to use as plot X-axes.
FACTORS = [("temperature_C", "Temperature (°C)"), ("time_h", "Time (h)"),
           ("fe_ratio", "Fe (recipe token)"), ("caco3_ratio", "CaCO₃ (recipe token)")]
# Metrics to plot against each factor.
PLOT_METRICS = [("DG_percent", "DG% (ordering)"),
                ("crystalline_fraction", "Crystalline fraction (amount)"),
                ("norm_002_area", "Normalized 002 area"),
                ("raw_002_height", "RAW 002 height (NOT comparable)")]


def analyze_one(path: str) -> dict:
    """Compute all metrics for one scan; recipe from the filename. Errors are
    captured per-row so one bad file never aborts the batch."""
    name = Path(path).name
    row = {c: None for c in COLUMNS}
    row["filename"] = name
    params = parse_run_filename(name)
    for k in ("display_name", "carbon_type", "form", "wash", "temperature_C",
              "time_h", "fe_ratio", "caco3_ratio", "carbon_ratio"):
        row[k] = params.get(k)
    try:
        p = XRDPattern.from_file(path)
        tt, inten = p.two_theta, p.intensity
        fit = fit_netl(tt, inten, peak_count=2)
        row["DG_percent"] = fit["DG_percent"]
        row["graphitic_xc"] = fit["graphitic"]["xc"]
        row["graphitic_fwhm"] = fit["graphitic"]["w"]
        row["Lc_angstrom"] = fit["crystallite_height_Lc_angstrom"]
        dec = decompose(tt, inten)
        row["crystalline_fraction"] = dec["crystalline_fraction"]
        row["disordered_fraction"] = dec["disordered_fraction"]
        row["decomp_r2"] = dec["fit_r2"]
        # normalized vs raw 002 — the comparability lesson, side by side
        sharp = dec["areas"]["graphitic_sharp"]
        tot = total_scatter(tt, inten)
        row["norm_002_area"] = round(sharp / tot, 6) if tot > 0 else None
        x, y = np.asarray(tt, float), np.asarray(inten, float)
        m = (x >= 24.0) & (x <= 28.5)
        row["raw_002_height"] = round(float(y[m].max()), 3) if m.any() else None
        imp = scan_impurities(tt, inten)
        row["impurity_verdict"] = imp["verdict"]
        row["worst_impurity_pct"] = imp.get("worst_pct")
    except (FitError, ValueError, FileNotFoundError) as exc:
        row["error"] = str(exc)
    return row


def analyze_dir(target: str) -> list[dict]:
    files = expand_xy_inputs([target])
    return [analyze_one(f) for f in sorted(files)]


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _short_label(r: dict) -> str:
    """Compact sample tag for point annotation, e.g. '1200°C·5h·6Fe'. Falls back
    to the parsed display name when the numeric factors are missing."""
    bits = []
    if r.get("temperature_C") is not None:
        bits.append(f"{int(r['temperature_C'])}°C")
    if r.get("time_h") is not None:
        bits.append(f"{r['time_h']:g}h")
    if r.get("fe_ratio") is not None:
        bits.append(f"{r['fe_ratio']:g}Fe")
    return "·".join(bits) if bits else str(r.get("display_name", r["filename"]))[:18]


def make_plots(rows: list[dict], out_dir: str) -> list[str]:
    """One figure per synthesis factor; each shows every metric vs that factor,
    colored by carbon grade. Saved as PNGs. Returns the written paths."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ok = [r for r in rows if not r["error"]]
    grades = sorted({r["carbon_type"] for r in ok if r["carbon_type"]})
    cmap = {g: c for g, c in zip(grades, plt.cm.tab10.colors)}
    written = []

    for factor, flabel in FACTORS:
        pts = [r for r in ok if r.get(factor) is not None]
        if len(pts) < 2:
            continue
        fig, axes = plt.subplots(2, 2, figsize=(11, 8))
        for ax, (metric, mlabel) in zip(axes.ravel(), PLOT_METRICS):
            for g in grades:
                gp = [r for r in pts if r["carbon_type"] == g and r.get(metric) is not None]
                if gp:
                    ax.scatter([r[factor] for r in gp], [r[metric] for r in gp],
                               label=g, color=cmap[g], s=42, edgecolor="k", linewidth=0.3)
            ax.set_xlabel(flabel)
            ax.set_ylabel(mlabel)
            ax.grid(alpha=0.3)
        axes.ravel()[0].legend(title="grade", fontsize=8)
        fig.suptitle(f"XRD metrics vs {flabel}", fontweight="bold")
        fig.tight_layout()
        fp = out / f"trends_vs_{factor}.png"
        fig.savefig(fp, dpi=130)
        plt.close(fig)
        written.append(str(fp))

    # DG% vs crystalline_fraction — the divergence, with every point LABELLED so
    # you can compare/contrast specific runs (the whole point of this figure).
    fig, ax = plt.subplots(figsize=(10, 7.5))
    labelled = [r for r in ok if r.get("DG_percent") is not None
                and r.get("crystalline_fraction") is not None]
    for g in grades:
        gp = [r for r in labelled if r["carbon_type"] == g]
        if gp:
            ax.scatter([r["crystalline_fraction"] for r in gp], [r["DG_percent"] for r in gp],
                       label=g, color=cmap[g], s=60, edgecolor="k", linewidth=0.4, zorder=3)
    for r in labelled:
        ax.annotate(_short_label(r), (r["crystalline_fraction"], r["DG_percent"]),
                    xytext=(4, 4), textcoords="offset points", fontsize=7,
                    color="0.25", zorder=4)
    ax.set_xlabel("Crystalline fraction  (AMOUNT of crystalline graphite, 0–1)", fontweight="bold")
    ax.set_ylabel("DG%  (ORDERING quality, Maire–Mering)", fontweight="bold")
    ax.set_title("DG% saturates high while crystalline fraction varies\n"
                 "→ a high DG% can still hide amorphous carbon (points spread left)",
                 fontsize=11)
    ax.grid(alpha=0.3)
    ax.legend(title="PC grade", fontsize=9, loc="lower right")
    fig.tight_layout()
    fp = out / "dg_vs_crystalline.png"
    fig.savefig(fp, dpi=140)
    plt.close(fig)
    written.append(str(fp))
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch XRD metric trends vs synthesis parameters.")
    ap.add_argument("target", nargs="?", default=str(DATA_DIR),
                    help="directory of .xy scans (default: the repo DATA/xrd scans).")
    ap.add_argument("--csv", default=None, help="write the per-scan metric table here.")
    ap.add_argument("--plots", default=None, metavar="DIR", help="write trend PNGs to this dir.")
    args = ap.parse_args()

    rows = analyze_dir(args.target)
    ok = sum(1 for r in rows if not r["error"])
    if args.csv:
        write_csv(rows, args.csv)
        print(f"wrote {ok}/{len(rows)} scans → {args.csv}")
    if args.plots:
        for p in make_plots(rows, args.plots):
            print(f"  plot → {p}")
    if not args.csv and not args.plots:  # console summary
        print(f"{'grade':6}{'T':>6}{'t':>5}{'Fe':>5}{'DG%':>8}{'cryst':>8}{'normA':>9}  impurity")
        for r in rows:
            if r["error"]:
                print(f"{r['filename'][:40]:40}  ERROR: {r['error'][:40]}")
                continue
            print(f"{str(r['carbon_type']):6}{str(r['temperature_C']):>6}"
                  f"{str(r['time_h']):>5}{str(r['fe_ratio']):>5}"
                  f"{r['DG_percent']:>8}{r['crystalline_fraction']:>8}"
                  f"{r['norm_002_area']:>9}  {r['impurity_verdict'][:28]}")
        print(f"\n{ok}/{len(rows)} scans analyzed.")


if __name__ == "__main__":
    main()
