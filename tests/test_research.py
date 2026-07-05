"""
Tests for the research/ module (amorphous accounting, calibration, yield).

- Synthetic tests run anywhere and lock in the math.
- The one data-dependent check skips cleanly when the (gitignored) scan folder
  is absent, mirroring the gold/Swift gating in test_engine.py.

Run:  python3 -m pytest tests/test_research.py -q
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))

import amorphous  # noqa: E402
import calibration  # noqa: E402
from _shared import DATA_DIR  # noqa: E402


# --------------------------------------------------------------------------
# amorphous.py — the crystallinity index
# --------------------------------------------------------------------------
def _mix_pattern(graphite_frac, seed=0):
    """Synthetic 10–90° pattern: a sharp crystalline peak weighted by
    graphite_frac on top of a broad amorphous band weighted by the remainder."""
    x, y = calibration._synthetic_pattern(graphite_frac, seed=seed)
    return x, y


def test_crystallinity_index_monotonic_with_graphite():
    """More crystalline graphite in the blend ⇒ higher crystallinity index."""
    fracs = [0.0, 0.25, 0.5, 0.75, 1.0]
    idx = [amorphous.crystallinity_index(*_mix_pattern(f, seed=i))
           for i, f in enumerate(fracs)]
    assert all(b >= a - 1e-6 for a, b in zip(idx, idx[1:])), idx
    assert idx[-1] > idx[0] + 0.3          # spans a real range
    assert 0.0 <= idx[0] and idx[-1] <= 1.0


def test_decompose_reports_index_not_wt_pct():
    x, y = _mix_pattern(0.6, seed=3)
    d = amorphous.decompose(x, y, subdivide=True)
    assert d["is_absolute_wt_pct"] is False
    assert 0.0 <= d["crystalline_fraction"] <= 1.0
    assert abs(d["crystalline_fraction"] + d["disordered_fraction"] - 1.0) < 1e-6
    # 3-way subdivision present and flagged for reliability
    assert "broad_split_reliable" in d["broad_subdivision"]


# --------------------------------------------------------------------------
# calibration.py — recover a known composition
# --------------------------------------------------------------------------
def test_calibration_selftest_passes():
    r = calibration.selftest(verbose=False)
    assert r["passed"], r
    assert r["calibration"]["r2"] > 0.98
    assert r["test_mae_wt_pct"] < 5.0


def test_apply_calibration_inverts_metric():
    train = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    mvals = [calibration.norm_002_area(*_mix_pattern(w, seed=i)) for i, w in enumerate(train)]
    cal = calibration.fit_calibration(mvals, [w * 100 for w in train])
    pred = calibration.apply_calibration(cal, calibration.norm_002_area(*_mix_pattern(0.5, seed=99)))
    assert abs(pred["crystalline_graphite_wt_pct"] - 50.0) < 8.0


def test_internal_standard_rir():
    # graphite area == standard area, 10 wt% standard, RIR 1 → 10 wt% graphite
    r = calibration.internal_standard_wt_pct(100.0, 100.0, 10.0, 1.0)
    assert abs(r["crystalline_graphite_wt_pct_as_spiked"] - 10.0) < 1e-6


# --------------------------------------------------------------------------
# yield_calc.py — reproduce the group's spreadsheet
# --------------------------------------------------------------------------
def test_yield_selftest_self_consistent():
    import yield_calc
    r = yield_calc.selftest(verbose=False)
    assert r["passed"], r
    c = r["result"]
    assert c["chemistry"]["graphite_theoretical"] > 0
    assert 0.0 < c["yield"]["mass_yield"] <= 1.5


def test_yield_without_post_acid_is_identical():
    """Post-acid cancels out of the yield — omitting it must give the same yield,
    and simply skip the wash QC."""
    import yield_calc
    common = dict(gpc_mass=2.0, c_wt=0.90, s_wt=0.05, fe_mass=4.0,
                  caco3_mass=0.6, pellet=6.6, post_furnace=5.8)
    with_acid = yield_calc.compute_yield(**common, post_acid=1.9)
    without = yield_calc.compute_yield(**common)
    assert with_acid["yield"]["mass_yield_pct"] == without["yield"]["mass_yield_pct"]
    assert without["wash"]["wash_check"] is None
    assert with_acid["wash"]["wash_check"] is not None


def test_crystalline_graphite_yield_scales():
    import yield_calc
    r = yield_calc.compute_yield(gpc_mass=2.0, c_wt=0.9, s_wt=0.05, fe_mass=4.0,
                                 caco3_mass=0.6, pellet=6.6, post_furnace=5.9,
                                 post_acid=1.9, crystalline_fraction=0.8)
    y = r["yield"]
    assert abs(y["crystalline_graphite_yield"] - y["mass_yield"] * 0.8) < 1e-6


def test_compute_from_name_scales_to_pellet():
    """Masses derived from a filename must be the recipe ratios scaled to the
    pellet mass (pellet = GPC + Fe + CaCO₃), summing back to the pellet."""
    import yield_calc
    pellet = 6.0   # synthetic; ratios 2:4:0.5 → sum 6.5
    r = yield_calc.compute_from_name("2GPC-4Fe-0.5CaCO3-1100C-3H", pellet=pellet,
                                     post_furnace=5.2, post_acid=1.8)
    d = r["derived"]   # masses rounded to 5 dp in the output
    assert abs(d["gpc_mass"] - pellet * 2 / 6.5) < 1e-4
    assert abs(d["fe_mass"] - pellet * 4 / 6.5) < 1e-4
    assert abs(d["caco3_mass"] - pellet * 0.5 / 6.5) < 1e-4
    assert abs((d["gpc_mass"] + d["fe_mass"] + d["caco3_mass"]) - pellet) < 1e-4


def test_masses_from_name_none_when_unparseable():
    import yield_calc
    assert yield_calc.masses_from_name("random_scan.xy", pellet=6.0) is None


def test_composition_override_changes_result():
    """A per-run carbon-fraction override must be used instead of the grade default."""
    import yield_calc
    name = "2GPC-4Fe-0.5CaCO3-1100C-3H"
    common = dict(pellet=6.0, post_furnace=5.2, post_acid=1.8)
    default = yield_calc.compute_from_name(name, **common)
    override = yield_calc.compute_from_name(name, **common, c_wt=0.80)
    assert default["derived"]["c_wt_overridden"] is False
    assert override["derived"]["c_wt_overridden"] is True
    assert abs(override["derived"]["c_wt"] - 0.80) < 1e-9
    # different feed carbon → different theoretical graphite → different yield
    assert default["yield"]["mass_yield_pct"] != override["yield"]["mass_yield_pct"]


# --------------------------------------------------------------------------
# data-dependent (skips without the gitignored scan folder)
# --------------------------------------------------------------------------
def test_trends_over_real_scans():
    if not DATA_DIR.is_dir() or not list(DATA_DIR.glob("*.xy")):
        pytest.skip("no scan data present")
    import trends
    rows = trends.analyze_dir(str(DATA_DIR))
    ok = [r for r in rows if not r["error"]]
    assert len(ok) >= 1
    for r in ok:
        assert r["crystalline_fraction"] is not None
        assert r["norm_002_area"] is not None
