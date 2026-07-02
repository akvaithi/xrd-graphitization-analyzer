"""
research/yield_calc.py — carbon / graphite mass-YIELD from weighed run masses.

(The file is ``yield_calc.py`` and not ``yield.py`` because ``yield`` is a Python
keyword — ``import yield`` is a syntax error.)

This ports the group's "Yield Calculations" spreadsheet and makes it run on any
run from its three weighed masses (pellet, post-furnace, post-acid), then extends
it in three ways the sheet doesn't cover:

  1. **Boudouard reconciliation** — the sheet's chemistry (below) predicts a
     furnace mass-loss; we compare that to the *measured* pellet→post-furnace
     drop and report the unaccounted loss (volatiles / extra etching / S gas),
     so the theoretical graphite is understood as an upper bound.
  2. **Mass-reconciled carbon** — the carbon actually surviving the furnace is
     ``post_furnace − Fe − CaO − CaS`` (algebraically identical to the sheet's
     ``post_acid − trapped_metal``), i.e. it uses the measured post-furnace mass
     rather than assuming the chemistry is complete.
  3. **Crystalline-graphite yield** — mass yield × the XRD crystallinity index
     (``xrd_analyzer.crystallinity``): the sheet assumes *all* remaining C is
     graphite, but only the crystalline fraction is battery-grade. This ties the
     yield work to the amorphous work.

THE CHEMISTRY (one reaction per step, exactly as the sheet):
    1. CaCO₃ → CaO + CO₂                          (thermal decomposition)
    2. C + CO₂ → 2 CO                             (Boudouard etching — C is LOST)
    3. CaO + S → CaS                              (sulfur trapping)
    4. remaining C → graphite                     (assumed complete)
    5. acid wash removes Fe + CaO + CaS; the shortfall vs the measured wash mass
       is "trapped metal" (incompletely-washed Fe/Ca left in the product).

YIELD = (carbon surviving the furnace) / (carbon predicted to survive Boudouard).
It is < 1 because of carbon lost beyond Boudouard (volatiles, fines, extra
etching). Validated by internal self-consistency + Python↔Swift parity.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import _shared  # noqa: F401  (puts the repo root on sys.path)
from xrd_analyzer import FitError, XRDPattern
from xrd_analyzer import crystallinity as engine_crystallinity
from run_parser import parse_run_filename

# Molar masses (g/mol) — match the spreadsheet's constants exactly.
M_C, M_Fe, M_CaCO3, M_CaO, M_S, M_CaS = 12.011, 55.845, 100.086, 56.077, 32.06, 72.138
M_CO2, M_CO = 44.009, 28.01

# Placeholder feed compositions (carbon, sulfur mass fractions) by PC grade —
# literature-typical values. **REPLACE with your own measured proximate/ultimate
# analysis** (ideally fixed carbon, not total) before quoting an absolute yield.
DEFAULT_COMPOSITION = {"GPC": (0.88, 0.045), "CPC": (0.97, 0.02), "LSPC": (0.95, 0.007)}
_FALLBACK_COMPOSITION = (0.88, 0.045)


def masses_from_name(sample: str, pellet: float) -> dict | None:
    """Back out the actual charged masses from the filename recipe ratios, scaled
    to the measured pellet mass (pellet = GPC + Fe + CaCO₃ — the same logic the
    spreadsheet used to get 'actual GPC'). Returns None if the name lacks the
    carbon/Fe ratios."""
    p = parse_run_filename(sample)
    cr, fr = p.get("carbon_ratio"), p.get("fe_ratio")
    car = p.get("caco3_ratio") or 0.0
    if not cr or not fr:
        return None
    s = cr + fr + car
    if s <= 0:
        return None
    return {"gpc_mass": pellet * cr / s, "fe_mass": pellet * fr / s,
            "caco3_mass": pellet * car / s, "grade": p.get("carbon_type")}


def compute_from_name(sample: str, *, pellet: float, post_furnace: float,
                      post_acid: float | None = None,
                      c_wt: float | None = None, s_wt: float | None = None,
                      crystalline_fraction: float | None = None) -> dict:
    """Yield for a run using ONLY the pellet / post-furnace / post-acid masses —
    the recipe ratios and feed composition are derived from the sample name.
    ``c_wt`` / ``s_wt`` optionally OVERRIDE the per-grade default composition (for
    a specific feedstock's measured carbon/sulfur). Mirrors the macOS Yield tab."""
    m = masses_from_name(sample, pellet)
    if m is None:
        raise ValueError(f"could not parse carbon/Fe ratios from sample name {sample!r}")
    g_c, g_s = DEFAULT_COMPOSITION.get((m["grade"] or "").upper(), _FALLBACK_COMPOSITION)
    eff_c = g_c if c_wt is None else c_wt
    eff_s = g_s if s_wt is None else s_wt
    out = compute_yield(gpc_mass=m["gpc_mass"], c_wt=eff_c, s_wt=eff_s,
                        fe_mass=m["fe_mass"], caco3_mass=m["caco3_mass"],
                        post_furnace=post_furnace, post_acid=post_acid, pellet=pellet,
                        crystalline_fraction=crystalline_fraction)
    out["derived"] = {"grade": m["grade"], "c_wt": eff_c, "s_wt": eff_s,
                      "c_wt_overridden": c_wt is not None, "s_wt_overridden": s_wt is not None,
                      "gpc_mass": round(m["gpc_mass"], 5), "fe_mass": round(m["fe_mass"], 5),
                      "caco3_mass": round(m["caco3_mass"], 5)}
    return out


def compute_yield(*, gpc_mass: float, c_wt: float, s_wt: float,
                  fe_mass: float, caco3_mass: float,
                  post_furnace: float, post_acid: float | None = None,
                  pellet: float | None = None,
                  crystalline_fraction: float | None = None) -> dict:
    """Carbon/graphite mass yield for one run. Masses in grams, wt% as fractions.

    The yield needs only the **post-furnace (pre-wash)** mass — the post-acid mass
    cancels out of it algebraically. ``post_acid`` is therefore OPTIONAL; if given,
    it drives a wash-completeness QC (trapped metal / wash efficiency) but does not
    change the yield. ``crystalline_fraction`` (0–1, from
    ``xrd_analyzer.crystallinity``) turns the mass yield into a crystalline-graphite
    yield.
    """
    # --- feed carbon & sulfur ------------------------------------------------
    actual_C = gpc_mass * c_wt
    actual_S = gpc_mass * s_wt
    mols_C = actual_C / M_C
    mols_S = actual_S / M_S

    # --- 1. CaCO₃ → CaO + CO₂ ------------------------------------------------
    mols_caco3 = caco3_mass / M_CaCO3
    mols_CaO = mols_caco3
    mols_CO2 = mols_caco3

    # --- 2. Boudouard: C + CO₂ → 2 CO  (carbon consumed 1:1 with CO₂) --------
    mols_C_boudouard = min(mols_CO2, mols_C)
    remaining_mols_C = mols_C - mols_C_boudouard
    remaining_C = remaining_mols_C * M_C
    boudouard_C_loss = mols_C_boudouard * M_C
    waste_mols_CO = 2.0 * mols_C_boudouard

    # --- 3. Sulfur trapping: CaO + S → CaS -----------------------------------
    mols_CaS = min(mols_S, mols_CaO)
    remaining_mols_CaO = mols_CaO - mols_CaS
    CaS_mass = mols_CaS * M_CaS
    remaining_CaO = remaining_mols_CaO * M_CaO

    # --- 4/5. graphite, residues, target wash --------------------------------
    graphite_theoretical = remaining_C                     # all remaining C → graphite
    ca_residues = CaS_mass + remaining_CaO
    target_wash = fe_mass + ca_residues                    # Fe + Ca residues acid should remove

    # carbon that actually survived the furnace — the yield basis. Uses ONLY the
    # post-furnace (pre-wash) mass; the post-acid mass cancels out of the yield.
    measured_C_after_furnace = post_furnace - fe_mass - ca_residues
    mass_yield = measured_C_after_furnace / graphite_theoretical if graphite_theoretical else 0.0
    carbon_lost_beyond_boudouard = graphite_theoretical - measured_C_after_furnace

    # optional wash-completeness QC (only when post-acid is supplied)
    wash_check = None
    if post_acid is not None:
        actual_wash = post_furnace - post_acid             # what washing actually removed
        trapped_metal = target_wash - actual_wash          # +ve: Fe/Ca left in product
        eff = (actual_wash / target_wash) if target_wash else 0.0
        wash_check = {
            "post_acid": round(float(post_acid), 5),
            "actual_wash": round(float(actual_wash), 5),
            "trapped_metal": round(float(trapped_metal), 5),
            "wash_efficiency_pct": round(float(eff) * 100, 2),
            "trapped_metal_pct_of_target": round(float(trapped_metal / target_wash) * 100, 2)
                if target_wash else None,
            "over_removed": bool(trapped_metal < 0),
            "note": "trapped_metal>0 → incomplete wash (Fe/Ca left in product); "
                    "trapped_metal<0 → over-removal, i.e. graphite fines likely lost "
                    "in washing (a loss this furnace-basis yield cannot otherwise see).",
        }

    # --- Boudouard / furnace-loss reconciliation -----------------------------
    chem_furnace_loss = waste_mols_CO * M_CO               # CO₂ + etched C leave as 2 CO
    pellet_calc = gpc_mass + fe_mass + caco3_mass
    predicted_post_furnace = (pellet - chem_furnace_loss) if pellet is not None else None
    unaccounted_furnace_loss = (predicted_post_furnace - post_furnace) if pellet is not None else None

    def r(v, n=5):
        return None if v is None else round(float(v), n)

    out = {
        "inputs": {"gpc_mass": r(gpc_mass), "c_wt": c_wt, "s_wt": s_wt,
                   "fe_mass": r(fe_mass), "caco3_mass": r(caco3_mass),
                   "pellet": r(pellet), "post_furnace": r(post_furnace),
                   "post_acid": r(post_acid),
                   "pellet_check_calc": r(pellet_calc),
                   "pellet_residual": r((pellet - pellet_calc) if pellet is not None else None)},
        "chemistry": {
            "actual_C": r(actual_C), "actual_S": r(actual_S),
            "CaO_mass": r(mols_CaO * M_CaO), "CO2_mols": r(mols_CO2),
            "boudouard_C_loss": r(boudouard_C_loss), "waste_CO_mols": r(waste_mols_CO),
            "remaining_C": r(remaining_C),
            "CaS_mass": r(CaS_mass), "remaining_CaO": r(remaining_CaO),
            "graphite_theoretical": r(graphite_theoretical),
            "ca_residues": r(ca_residues),
        },
        "wash": {"target_wash": r(target_wash), "wash_check": wash_check},
        "reconciliation": {
            "chem_furnace_loss": r(chem_furnace_loss),
            "predicted_post_furnace": r(predicted_post_furnace),
            "measured_post_furnace": r(post_furnace),
            "unaccounted_furnace_loss": r(unaccounted_furnace_loss),
            "carbon_lost_beyond_boudouard": r(carbon_lost_beyond_boudouard),
            "note": "unaccounted loss = volatiles/moisture/S-gas/extra C the sheet "
                    "chemistry omits; theoretical graphite is therefore an upper bound.",
        },
        "yield": {
            "measured_C_after_furnace": r(measured_C_after_furnace),
            "mass_yield": r(mass_yield, 4),
            "mass_yield_pct": r(mass_yield * 100, 2),
        },
    }
    if crystalline_fraction is not None:
        cg_yield = mass_yield * crystalline_fraction
        out["yield"].update({
            "crystalline_fraction": round(float(crystalline_fraction), 4),
            "crystalline_graphite_mass": r(measured_C_after_furnace * crystalline_fraction),
            "crystalline_graphite_yield": r(cg_yield, 4),
            "crystalline_graphite_yield_pct": r(cg_yield * 100, 2),
        })
    return out


# ---------------------------------------------------------------------------
# Manifest CSV — one row per run (all masses you'll have going forward)
# ---------------------------------------------------------------------------
MANIFEST_COLUMNS = ("sample", "gpc_mass", "c_wt", "s_wt", "fe_mass", "caco3_mass",
                    "pellet", "post_furnace", "post_acid", "xy_file")


def _crystalline_fraction_for(xy_file: str | None, base: Path) -> float | None:
    if not xy_file:
        return None
    p = Path(xy_file)
    if not p.is_absolute():
        p = base / xy_file
    try:
        pat = XRDPattern.from_file(p)
        return engine_crystallinity(pat.two_theta, pat.intensity)["crystalline_fraction"]
    except (FitError, ValueError, FileNotFoundError):
        return None


def from_manifest(manifest_csv: str) -> list[dict]:
    """Compute yield for every row of a CSV. Two accepted formats, per row:

      * **lean** (recommended): ``sample, pellet, post_furnace[, post_acid, xy_file]``
        — recipe ratios + composition are derived from the sample name.
      * **explicit**: also give ``gpc_mass, c_wt, s_wt, fe_mass, caco3_mass``.

    ``xy_file`` (optional) adds the crystalline-graphite yield.
    """
    base = Path(manifest_csv).resolve().parent
    rows: list[dict] = []
    with open(manifest_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            cf = _crystalline_fraction_for(row.get("xy_file"), base)
            post_acid = float(row["post_acid"]) if row.get("post_acid") else None
            if row.get("gpc_mass"):                         # explicit masses
                res = compute_yield(
                    gpc_mass=float(row["gpc_mass"]), c_wt=float(row["c_wt"]),
                    s_wt=float(row["s_wt"]), fe_mass=float(row["fe_mass"]),
                    caco3_mass=float(row["caco3_mass"]),
                    pellet=float(row["pellet"]) if row.get("pellet") else None,
                    post_furnace=float(row["post_furnace"]), post_acid=post_acid,
                    crystalline_fraction=cf)
            else:                                           # lean: derive from name
                res = compute_from_name(
                    row["sample"], pellet=float(row["pellet"]),
                    post_furnace=float(row["post_furnace"]), post_acid=post_acid,
                    c_wt=float(row["c_wt"]) if row.get("c_wt") else None,
                    s_wt=float(row["s_wt"]) if row.get("s_wt") else None,
                    crystalline_fraction=cf)
            res["sample"] = row.get("sample", "")
            rows.append(res)
    return rows


# ---------------------------------------------------------------------------
# Trend plots — yield vs synthesis parameters (parsed from the sample name)
# ---------------------------------------------------------------------------
_YIELD_FACTORS = [("temperature_C", "Temperature (°C)"),
                  ("caco3_ratio", "CaCO₃ (recipe token)"),
                  ("fe_ratio", "Fe (recipe token)"),
                  ("time_h", "Dwell time (h)")]


def make_plots(rows: list[dict], out_dir: str) -> list[str]:
    """One figure per synthesis factor: mass yield AND crystalline-graphite yield
    vs that factor. Parameters are parsed from each row's ``sample`` name. This is
    where the CaCO₃ carbon penalty (Boudouard) should show up visually."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # flatten: {factor: value} + the two yields, per row
    pts = []
    for r in rows:
        p = parse_run_filename(r.get("sample", ""))
        pts.append({**p,
                    "mass_yield": r["yield"]["mass_yield_pct"],
                    "cg_yield": r["yield"].get("crystalline_graphite_yield_pct")})
    written = []
    for factor, flabel in _YIELD_FACTORS:
        xs = [(q[factor], q["mass_yield"], q["cg_yield"]) for q in pts if q.get(factor) is not None]
        if len(xs) < 2:
            continue
        xs.sort(key=lambda t: t[0])
        fx = [t[0] for t in xs]
        fig, ax = plt.subplots(figsize=(7.5, 5.2))
        ax.plot(fx, [t[1] for t in xs], "o-", color="#007aff", label="Mass yield")
        cg = [(x, c) for x, _, c in xs if c is not None]
        if cg:
            ax.plot([x for x, _ in cg], [c for _, c in cg], "s--", color="#ff9500",
                    label="Crystalline-graphite yield")
        ax.set_xlabel(flabel, fontweight="bold")
        ax.set_ylabel("Yield (%)", fontweight="bold")
        ax.set_title(f"Yield vs {flabel}", fontsize=11)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=9)
        fig.tight_layout()
        fp = out / f"yield_vs_{factor}.png"
        fig.savefig(fp, dpi=140)
        plt.close(fig)
        written.append(str(fp))
    return written


# ---------------------------------------------------------------------------
# Self-test — synthetic demo run (no real process data), checks internal
# consistency; the exact-arithmetic port is cross-checked by the Python↔Swift
# parity test (both engines on the same inputs).
# ---------------------------------------------------------------------------
_DEMO_SAMPLE = dict(gpc_mass=2.0, c_wt=0.90, s_wt=0.05,
                    fe_mass=4.0, caco3_mass=0.6,
                    pellet=6.6, post_furnace=5.8, post_acid=1.9)


def selftest(verbose: bool = True) -> dict:
    cf = 0.90
    r = compute_yield(**_DEMO_SAMPLE, crystalline_fraction=cf)
    y, c = r["yield"], r["chemistry"]
    ok = (0.0 < y["mass_yield"] <= 1.5
          and c["graphite_theoretical"] > 0
          and abs(y["crystalline_graphite_yield"] - y["mass_yield"] * cf) < 1e-3)
    if verbose:
        print(f"(synthetic demo — not real process data)")
        print(f"graphite (theoretical)     = {c['graphite_theoretical']:.5f} g")
        print(f"measured C after furnace   = {y['measured_C_after_furnace']:.5f} g")
        print(f"MASS YIELD                 = {y['mass_yield_pct']:.2f} %")
        print(f"crystalline-graphite yield = {y['crystalline_graphite_yield_pct']:.2f} %  (× {cf})")
        print(f"self-consistent            -> {'PASS' if ok else 'FAIL'}")
    return {"result": r, "passed": bool(ok)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Carbon/graphite mass yield from weighed run masses.")
    ap.add_argument("--selftest", action="store_true", help="reproduce the spreadsheet sample.")
    ap.add_argument("--manifest", metavar="CSV", help="compute yield for every run in a CSV.")
    ap.add_argument("--csv-out", metavar="CSV", help="write a flat per-run summary CSV.")
    ap.add_argument("--plots", metavar="DIR", help="write yield-vs-parameter trend PNGs.")
    # single-run flags
    for k in ("gpc-mass", "c-wt", "s-wt", "fe-mass", "caco3-mass", "post-furnace", "post-acid"):
        ap.add_argument(f"--{k}", type=float)
    ap.add_argument("--pellet", type=float)
    ap.add_argument("--xy", help="a .xy scan → adds crystalline-graphite yield.")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(0 if selftest()["passed"] else 1)

    if args.manifest:
        rows = from_manifest(args.manifest)
        if args.plots:
            for p in make_plots(rows, args.plots):
                print(f"  plot → {p}")
        if args.csv_out:
            with open(args.csv_out, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["sample", "mass_yield_pct", "crystalline_graphite_yield_pct",
                            "graphite_theoretical_g", "trapped_metal_g", "wash_efficiency_pct",
                            "measured_C_after_furnace_g", "unaccounted_furnace_loss_g"])
                for r in rows:
                    wc = r["wash"]["wash_check"] or {}
                    w.writerow([r.get("sample", ""), r["yield"]["mass_yield_pct"],
                                r["yield"].get("crystalline_graphite_yield_pct", ""),
                                r["chemistry"]["graphite_theoretical"],
                                wc.get("trapped_metal", ""), wc.get("wash_efficiency_pct", ""),
                                r["yield"]["measured_C_after_furnace"],
                                r["reconciliation"]["unaccounted_furnace_loss"]])
            print(f"wrote {len(rows)} run(s) → {args.csv_out}")
        else:
            for r in rows:
                cg = r["yield"].get("crystalline_graphite_yield_pct")
                print(f"{r.get('sample',''):40}  mass yield {r['yield']['mass_yield_pct']:6.2f}%"
                      + (f"   crystalline-graphite {cg:6.2f}%" if cg is not None else ""))
        return

    if args.gpc_mass is not None:
        cf = None
        if args.xy:
            cf = _crystalline_fraction_for(args.xy, Path.cwd())
        r = compute_yield(gpc_mass=args.gpc_mass, c_wt=args.c_wt, s_wt=args.s_wt,
                          fe_mass=args.fe_mass, caco3_mass=args.caco3_mass,
                          pellet=args.pellet, post_furnace=args.post_furnace,
                          post_acid=args.post_acid, crystalline_fraction=cf)
        print(json.dumps(r, indent=2))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
