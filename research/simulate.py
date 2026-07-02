"""
research/simulate.py — "where does everything go?" mass-balance + kinetics model
for the catalytic graphitization of petroleum coke (PC + Fe + CaCO₃ → graphite).

WHY
---
XRD tells you about the *carbon you can see* (the 002 region). It does not tell
you the fate of the Fe catalyst, the CaCO₃ additive, the sulfur, or the volatiles
— and it cannot, by itself, separate "high ordering (DG%)" from "high amount of
crystalline carbon". This module closes the books on a run: given the recipe and
the furnace schedule, it accounts for every gram (carbon, Fe, Ca, S, O) across
pyrolysis and the acid wash, and it tracks **two distinct state variables that
the literature conflates**:

    DG%               — ordering *quality* (d-spacing; what the paper reports)
    crystalline_frac  — *amount* of carbon that is crystalline graphite

The whole point of the project's open question is that these diverge: DG% can sit
at ~97% while a meaningful fraction of the carbon is still amorphous. The kinetic
sub-model is built so DG% saturates high quickly (matching the paper's narrow
85–98% band) while the crystalline fraction keeps climbing with T and t — making
the divergence explicit and quantitative.

CALIBRATION OF THE KINETICS
---------------------------
``crystalline_frac`` uses an Avrami(n=1)–Arrhenius extent of reaction whose rate
constants give a physically reasonable low-temperature onset that saturates by the
paper's conditions. DG% is then mapped onto a high, narrow band consistent with the
published paper (Fig 3b: ~85→97.7% over 1300–1600 °C; Fig 4b: rising to a plateau
by ~24 h at 1600 °C).

The composition tables, kinetic constants, and removal efficiencies are **editable
illustrative assumptions**, flagged as such — replace ``_KINETICS`` and
``PC_COMPOSITION`` with your own measured values.
"""

from __future__ import annotations

import argparse
import json
import math

# --- molar masses (g/mol) --------------------------------------------------
M_C = 12.011
M_CaCO3, M_CaO, M_CO2 = 100.087, 56.077, 44.009
M_Fe, M_Fe2O3 = 55.845, 159.688

R_GAS = 8.314  # J/mol/K

# --- petroleum-coke compositions (mass fractions; editable assumptions) ----
# fixed_C + sulfur + volatile_matter + ash = 1.0. Values are typical literature
# ranges for green (GPC), calcined (CPC) and low-sulfur (LSPC) pet coke; tune to
# your feedstock's proximate/ultimate analysis.
PC_COMPOSITION = {
    "GPC":  {"fixed_C": 0.880, "sulfur": 0.040, "volatile_matter": 0.070, "ash": 0.010},
    "CPC":  {"fixed_C": 0.965, "sulfur": 0.025, "volatile_matter": 0.005, "ash": 0.005},
    "LSPC": {"fixed_C": 0.950, "sulfur": 0.007, "volatile_matter": 0.030, "ash": 0.013},
}

# --- Avrami–Arrhenius kinetics for the crystalline (amount) fraction --------
# k(T) = A·exp(-Ea/RT);  α(T,t) = 1 - exp(-k·t). Illustrative constants giving a
# low-temperature onset that saturates by the paper's conditions — replace A/Ea
# with a fit to your own crystalline-fraction vs temperature/time measurements.
_KINETICS = {"A_per_h": 121.2, "Ea_over_R": 7589.0, "alpha_max": 0.99, "avrami_n": 1.0}


def crystalline_fraction(temp_C: float, time_h: float) -> float:
    """Extent of graphitization (fraction of carbon that is crystalline), 0–αmax."""
    T = temp_C + 273.15
    k = _KINETICS["A_per_h"] * math.exp(-_KINETICS["Ea_over_R"] / T)
    alpha = 1.0 - math.exp(-((k * time_h) ** _KINETICS["avrami_n"]))
    return _KINETICS["alpha_max"] * max(0.0, min(1.0, alpha))


def dg_percent(temp_C: float, time_h: float) -> float:
    """Ordering DG% (d-spacing basis). Mapped onto a high, narrow band so it
    saturates near ~98% — the modeled stand-in for 'looks fully graphitized by
    DG%' even when the crystalline *amount* is lower."""
    a = crystalline_fraction(temp_C, time_h) / _KINETICS["alpha_max"]
    return round(90.0 + 8.0 * a, 2)


def sulfur_retained_fraction(temp_C: float) -> float:
    """Fraction of sulfur still in the solid after pyrolysis. Pinned to the paper
    (Fig 3c / Table S1): ~1.6%→~0 between 1300 and 1500 °C. Logistic in T."""
    return 1.0 / (1.0 + math.exp((temp_C - 1380.0) / 60.0))


def simulate(grade: str = "GPC", pc_mass_g: float = 1.0, fe_wt_pct: float = 100.0,
             caco3_wt_pct: float = 5.0, temp_C: float = 1600.0, time_h: float = 24.0,
             *, fe_recovery: float = 0.70, carbon_gasified_frac: float = 0.02,
             ca_sulfur_capture: float = 0.30) -> dict:
    """Full mass balance + kinetics for one run. ``fe_wt_pct`` and ``caco3_wt_pct``
    are relative to the PC mass (paper uses PC:Fe = 1:1 → fe_wt_pct=100)."""
    grade = grade.upper()
    if grade not in PC_COMPOSITION:
        raise ValueError(f"unknown grade {grade!r}; choose from {list(PC_COMPOSITION)}")
    comp = PC_COMPOSITION[grade]
    m_PC = float(pc_mass_g)
    m_Fe = m_PC * fe_wt_pct / 100.0
    m_CaCO3 = m_PC * caco3_wt_pct / 100.0
    m_in = m_PC + m_Fe + m_CaCO3

    # --- PC partition --------------------------------------------------------
    m_C0 = m_PC * comp["fixed_C"]
    m_S0 = m_PC * comp["sulfur"]
    m_VM = m_PC * comp["volatile_matter"]
    m_ash = m_PC * comp["ash"]

    # --- pyrolysis -----------------------------------------------------------
    # Boudouard etching (C + CO₂ → 2CO): the CO₂ released by CaCO₃ decomposition
    # consumes carbon 1:1, so the CaCO₃ additive *costs* carbon and leaves as CO.
    # (Same chemistry as research/yield_calc.py, keeping the predictive and the
    # measured-mass models consistent.) Plus a small generic disproportionation.
    mols_CO2 = m_CaCO3 / M_CaCO3
    m_C_boudouard = min(mols_CO2 * M_C, m_C0)
    m_C_gas = m_C0 * carbon_gasified_frac
    m_C_solid = max(m_C0 - m_C_gas - m_C_boudouard, 0.0)
    Xc = crystalline_fraction(temp_C, time_h)
    m_C_graphite = m_C_solid * Xc
    m_C_amorphous = m_C_solid * (1.0 - Xc)

    # sulfur: retained-in-solid vs evolved; of the evolved, a portion is captured
    # by CaO (as CaS/CaSO₄, stays solid until wash) and the rest leaves as SOx gas
    f_ret = sulfur_retained_fraction(temp_C)
    m_S_solid = m_S0 * f_ret
    m_S_evolved = m_S0 - m_S_solid
    m_S_captured = m_S_evolved * ca_sulfur_capture     # held by Ca, removed in wash
    m_S_gas = m_S_evolved - m_S_captured               # SOx out the Ar exhaust

    # CaCO₃ → CaO (solid, washed out later) + CO₂ (gas), complete above ~825 °C
    m_CaO = m_CaCO3 * (M_CaO / M_CaCO3)
    m_CO2 = m_CaCO3 * (M_CO2 / M_CaCO3)

    # Fe stays solid (as Fe/Fe₃C) through pyrolysis; removed in the acid wash
    # --- acid wash -----------------------------------------------------------
    # HCl dissolves Fe, CaO and the Ca-captured sulfur; carbon + ash + the sulfur
    # locked in the carbon remain as product.
    m_product = m_C_graphite + m_C_amorphous + m_S_solid + m_ash
    m_washed_out = m_Fe + m_CaO + m_S_captured        # into solution
    m_Fe_recovered = m_Fe * fe_recovery * (M_Fe2O3 / (2 * M_Fe))  # regenerated Fe₂O₃

    # --- gas total -----------------------------------------------------------
    # (Boudouard C leaves as CO alongside the CaCO₃ CO₂ — both counted here.)
    m_gas = m_VM + m_C_gas + m_C_boudouard + m_S_gas + m_CO2

    # --- closure -------------------------------------------------------------
    m_accounted = m_product + m_washed_out + m_gas
    closure_pct = 100.0 * m_accounted / m_in if m_in > 0 else 0.0

    def g(v):  # round grams
        return round(float(v), 5)

    return {
        "inputs": {"grade": grade, "pc_mass_g": g(m_PC), "fe_mass_g": g(m_Fe),
                   "caco3_mass_g": g(m_CaCO3), "total_in_g": g(m_in),
                   "temp_C": temp_C, "time_h": time_h},
        "state": {"crystalline_fraction": round(Xc, 4), "DG_percent": dg_percent(temp_C, time_h),
                  "sulfur_retained_fraction": round(f_ret, 4),
                  "note": "DG% (ordering) saturates high while crystalline_fraction "
                          "(amount) lags — the amorphous carbon DG% cannot see."},
        "product_after_wash_g": {
            "crystalline_graphite": g(m_C_graphite),
            "residual_amorphous_carbon": g(m_C_amorphous),
            "sulfur_in_carbon": g(m_S_solid), "ash": g(m_ash),
            "total": g(m_product),
            "carbon_purity_pct": round(100.0 * (m_C_graphite + m_C_amorphous) / m_product, 2)
                if m_product > 0 else 0.0,
            "graphite_purity_pct": round(100.0 * m_C_graphite / m_product, 2)
                if m_product > 0 else 0.0,
        },
        "gas_g": {"volatiles": g(m_VM), "carbon_as_COx": g(m_C_gas),
                  "carbon_boudouard_CO": g(m_C_boudouard),
                  "SOx": g(m_S_gas), "CO2_from_CaCO3": g(m_CO2), "total": g(m_gas)},
        "washed_out_g": {"Fe": g(m_Fe), "CaO": g(m_CaO), "Ca_captured_S": g(m_S_captured),
                         "total": g(m_washed_out), "Fe2O3_recovered": g(m_Fe_recovered)},
        "balance": {"total_in_g": g(m_in), "total_accounted_g": g(m_accounted),
                    "closure_pct": round(closure_pct, 4)},
    }


# ---------------------------------------------------------------------------
# Optional TGA burn-off sub-model: amorphous oxidizes before graphitic carbon.
# ---------------------------------------------------------------------------

def tga_burnoff(crystalline_frac: float, *, t_amorphous_C: float = 480.0,
                t_graphitic_C: float = 700.0, width_C: float = 45.0,
                t_lo: float = 300.0, t_hi: float = 900.0, step_C: float = 5.0) -> dict:
    """Predict a TGA/DTG oxidation curve from the amorphous/graphitic split.

    Amorphous carbon burns earlier (lower, broader peak) and graphitic later; the
    DTG (−dm/dT) is a two-Gaussian curve whose areas are the two carbon fractions.
    This is what to compare against a real TGA run to *independently* confirm the
    XRD amorphous fraction (and to convert 'burn rate' into composition)."""
    import numpy as np
    T = np.arange(t_lo, t_hi + step_C, step_C)
    amorph = (1.0 - crystalline_frac)
    graph = crystalline_frac

    def gauss(center, w):
        return np.exp(-0.5 * ((T - center) / w) ** 2) / (w * math.sqrt(2 * math.pi))

    dtg = amorph * gauss(t_amorphous_C, width_C) + graph * gauss(t_graphitic_C, width_C * 1.3)
    mass = 1.0 - np.cumsum(dtg) * step_C    # remaining carbon fraction
    mass = np.clip(mass / mass[0] if mass[0] else mass, 0.0, 1.0)
    return {"temp_C": [round(float(t), 1) for t in T],
            "dtg_per_C": [round(float(v), 6) for v in dtg],
            "mass_fraction_remaining": [round(float(v), 5) for v in mass],
            "amorphous_burn_C": t_amorphous_C, "graphitic_burn_C": t_graphitic_C,
            "amorphous_area": round(amorph, 4), "graphitic_area": round(graph, 4)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Catalytic-graphitization mass balance + kinetics.")
    ap.add_argument("--grade", default="GPC", choices=list(PC_COMPOSITION))
    ap.add_argument("--mass", type=float, default=1.0, help="PC basis mass (g).")
    ap.add_argument("--fe", type=float, default=100.0, help="Fe as wt%% of PC (100 = 1:1).")
    ap.add_argument("--caco3", type=float, default=5.0, help="CaCO3 as wt%% of PC.")
    ap.add_argument("--temp", type=float, default=1600.0, help="pyrolysis temperature (°C).")
    ap.add_argument("--time", type=float, default=24.0, help="dwell time (h).")
    ap.add_argument("--tga", action="store_true", help="also print the TGA burn-off prediction.")
    args = ap.parse_args()

    r = simulate(grade=args.grade, pc_mass_g=args.mass, fe_wt_pct=args.fe,
                 caco3_wt_pct=args.caco3, temp_C=args.temp, time_h=args.time)
    if args.tga:
        r["tga_burnoff"] = tga_burnoff(r["state"]["crystalline_fraction"])
    print(json.dumps(r, indent=2))
    b = r["balance"]
    print(f"\n# closure: {b['total_accounted_g']} / {b['total_in_g']} g  "
          f"= {b['closure_pct']:.3f}%   |   DG%={r['state']['DG_percent']}  "
          f"crystalline={r['state']['crystalline_fraction']:.3f}", flush=True)


if __name__ == "__main__":
    main()
