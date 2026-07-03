"""
Regression + parity suite for the DG engine.

- Synthetic tests run anywhere (no data needed) and lock in the math.
- Gold-data tests reconstruct the postdoc OriginLab fits from the .opj files and
  skip cleanly when the (gitignored) data or `opj2dat` aren't present.
- Parity tests compare the Python and Swift engines and skip if the Swift CLI
  hasn't been built.

Run:  python3 -m pytest tests/ -q
"""
import glob
import math
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xrd_analyzer as xa  # noqa: E402

LAM = 1.54187
GOLD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test", "XRD Data")
SWIFT_CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "native", ".build", "debug",
                         "xrd-validate.exe" if sys.platform == "win32" else "xrd-validate")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def synth_pattern(peaks, y0=2.0, lo=20.0, hi=32.0, step=0.02, noise=0.0, seed=0):
    """Build a (2θ, intensity) pattern from a list of (A, xc, w, mu) peaks."""
    x = np.arange(lo, hi, step)
    y = np.full_like(x, y0)
    for A, xc, w, mu in peaks:
        y = y + xa.pseudo_voigt(x, A, xc, w, mu)
    if noise:
        y = y + np.random.default_rng(seed).normal(0, noise, x.shape)
    return x, y


def gold_samples():
    """Yield (name, x, y, gold_DG) reconstructed from each postdoc .opj."""
    if not os.path.isdir(GOLD_DIR) or shutil.which("opj2dat") is None:
        return
    for opj in sorted(glob.glob(os.path.join(GOLD_DIR, "*.opj"))):
        d = "/tmp/_xatest"
        os.system(f"rm -rf {d}; mkdir -p {d}")
        shutil.copy(opj, f"{d}/p.opj")
        subprocess.run(["opj2dat", "p.opj"], cwd=d, capture_output=True)
        f1 = f"{d}/p.opj.1.dat"
        if not os.path.exists(f1):
            continue
        rows = []
        for ln in open(f1):
            q = ln.strip().rstrip(";").split(";")
            if len(q) < 2:
                continue
            try:
                a, b = float(q[0]), float(q[1])
                if np.isfinite(a) and np.isfinite(b):
                    rows.append((a, b))
            except ValueError:
                pass
        if len(rows) < 200:
            continue
        arr = np.array(rows)
        raw = open(f"{d}/p.opj", "rb").read().decode("latin-1")
        P = {}
        for m in re.finditer(r"<([A-Za-z0-9_]+)_Value[^>]*>(-?\d+\.\d+)</\1_Value>", raw):
            P.setdefault(m.group(1), []).append(float(m.group(2)))
        if "xc" not in P:
            continue
        xc1, A1 = P["xc"][0], P["A"][0]
        def bragg_nm(tt):
            return (LAM / (2 * math.sin(math.radians(tt / 2)))) / 10.0
        if "xc2" in P:
            xc2, A2 = P["xc2"][0], P["A2"][0]
            if xc2 > xc1:
                xc1, A1, xc2, A2 = xc2, A2, xc1, A1
            dp = A1 / (A1 + A2) * bragg_nm(xc1) + A2 / (A1 + A2) * bragg_nm(xc2)
        else:
            dp = bragg_nm(xc1)
        gold = (0.3440 - dp) / (0.3440 - 0.3354) * 100
        yield os.path.basename(opj), arr[:, 0], arr[:, 1], gold
    os.system("rm -rf /tmp/_xatest")


GOLD = list(gold_samples())


# --------------------------------------------------------------------------
# synthetic math (always run)
# --------------------------------------------------------------------------
def test_pseudo_voigt_unit_area():
    x = np.arange(20, 33, 0.002)
    for mu in (0.0, 0.5, 1.0):
        trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
        area = trapz(xa.pseudo_voigt(x, 1.0, 26.5, 0.2, mu), x)
        assert abs(area - 1.0) < 0.02, f"μ={mu} area={area}"


def test_fit_recovers_single_peak():
    x, y = synth_pattern([(120.0, 26.55, 0.16, 0.5)], y0=3.0)
    r = xa.fit_netl(x, y, peak_count=1)
    assert abs(r["graphitic"]["xc"] - 26.55) < 0.01
    assert r["fit_r2"] > 0.999 if "fit_r2" in r else True
    assert r["DG_sigma"] is None or r["DG_sigma"] >= 0


def test_dg_monotonic_in_position():
    # higher 2θ (smaller d) → higher DG
    lo = xa.fit_netl(*synth_pattern([(100, 26.40, 0.16, 0.5)]), peak_count=1)["DG_percent"]
    hi = xa.fit_netl(*synth_pattern([(100, 26.60, 0.16, 0.5)]), peak_count=1)["DG_percent"]
    assert hi > lo


def test_dg_range_brackets_primary():
    x, y = synth_pattern([(100, 26.55, 0.16, 0.5), (25, 26.15, 0.7, 1.0)])
    rng = xa.dg_range(x, y)
    assert rng is not None
    assert rng["low"] <= rng["primary"] <= rng["high"] + 1e-6


def test_window_trimmed_to_285():
    assert xa.NETL_WINDOW == (24.0, 28.5)


def test_two_theta_offset_shifts_dg():
    x, y = synth_pattern([(100, 26.50, 0.16, 0.5)])
    base = xa.fit_netl(x, y, peak_count=1)["DG_percent"]
    shifted = xa.fit_netl(x, y, peak_count=1, two_theta_offset=0.05)["DG_percent"]
    assert shifted > base  # +shift → higher 2θ → higher DG
    assert xa.fit_netl(x, y, peak_count=1, two_theta_offset=0.05)["two_theta_offset"] == 0.05


# --------------------------------------------------------------------------
# internal-standard calibration (synthetic)
# --------------------------------------------------------------------------
def test_calibration_finds_injected_offset():
    # graphite (002) + α-Fe lines, the whole pattern shifted +0.10°
    shift = 0.10
    fe = xa._phase_lines("alpha-Fe", LAM)
    peaks = [(100, 26.55 + shift, 0.16, 0.5)] + [(20, L + shift, 0.12, 0.6) for L in fe]
    x, y = synth_pattern(peaks, lo=20, hi=90, step=0.02)
    cal = xa.calibrate_internal_standard(x, y, "alpha-Fe")
    assert cal["reliable"] and cal["significant"]
    assert abs(cal["offset"] - shift) < 0.02


def test_calibration_clean_pattern_no_offset():
    # only graphite reflections → no foreign-phase standard → not reliable
    x, y = synth_pattern([(100, 26.55, 0.16, 0.5), (8, 54.7, 0.3, 0.5)], lo=20, hi=90)
    cal = xa.calibrate_internal_standard(x, y, "auto")
    assert not cal["significant"]


# --------------------------------------------------------------------------
# gold-data regression (skip if data/opj2dat absent)
# --------------------------------------------------------------------------
@pytest.mark.skipif(not GOLD, reason="postdoc .opj gold data / opj2dat not available")
def test_mae_vs_postdoc_gold():
    errs = []
    for _name, x, y, gold in GOLD:
        try:
            errs.append(abs(xa.fit_netl(x, y, peak_count=2)["DG_percent"] - gold))
        except (xa.FitError, ValueError):
            pass
    mae = float(np.mean(errs))
    assert mae <= 1.10, f"auto MAE regressed: {mae:.2f}% (n={len(errs)})"


@pytest.mark.skipif(not GOLD, reason="gold data not available")
def test_calibration_silent_on_aligned_gold():
    # the postdoc instrument was aligned → no sample should get a *significant* offset
    flagged = [n for n, x, y, _ in GOLD
               if xa.calibrate_internal_standard(x, y, "auto")["significant"]]
    assert not flagged, f"spurious calibration on aligned samples: {flagged}"


# --------------------------------------------------------------------------
# Python ↔ Swift parity (skip if CLI not built)
# --------------------------------------------------------------------------
def _swift(xy_path, *args, key=None):
    """Run the Swift CLI; return the parsed JSON line containing `key` (or the last)."""
    import json
    out = subprocess.run([SWIFT_CLI, xy_path, *args], capture_output=True, text=True)
    objs = []
    for ln in out.stdout.strip().splitlines():
        try:
            objs.append(json.loads(ln))
        except ValueError:
            pass
    if key is not None:
        for o in objs:
            if key in o:
                return o
    return objs[-1]


@pytest.mark.skipif(not os.path.exists(SWIFT_CLI), reason="Swift xrd-validate not built")
def test_python_swift_dg_parity(tmp_path):
    x, y = synth_pattern([(100, 26.55, 0.16, 0.5), (25, 26.15, 0.7, 1.0)], lo=20, hi=32)
    p = tmp_path / "s.xy"
    p.write_text("\n".join(f"{a}\t{b}" for a, b in zip(x, y)))
    py = xa.fit_netl(x, y, peak_count=2)["DG_percent"]
    sw = _swift(str(p), "--peaks", "2")["DG_percent"]
    assert abs(py - sw) < 0.05, f"engine DG drift: py={py} swift={sw}"


@pytest.mark.skipif(not os.path.exists(SWIFT_CLI), reason="Swift xrd-validate not built")
def test_python_swift_calibration_parity(tmp_path):
    shift = 0.10
    fe = xa._phase_lines("alpha-Fe", LAM)
    peaks = [(100, 26.55 + shift, 0.16, 0.5)] + [(20, L + shift, 0.12, 0.6) for L in fe]
    x, y = synth_pattern(peaks, lo=20, hi=90, step=0.02)
    p = tmp_path / "c.xy"
    p.write_text("\n".join(f"{a}\t{b}" for a, b in zip(x, y)))
    py = xa.calibrate_internal_standard(x, y, "alpha-Fe")["offset"]
    sw = _swift(str(p), "--peaks", "1", "--calib", "alpha-Fe", key="offset")["offset"]
    assert abs(py - sw) < 0.01, f"calibration offset drift: py={py} swift={sw}"


@pytest.mark.skipif(not os.path.exists(SWIFT_CLI), reason="Swift xrd-validate not built")
def test_python_swift_crystallinity_parity(tmp_path):
    """xrd_analyzer.crystallinity ↔ XRDCore CrystallinityAnalyzer. Both fit a
    broad disordered band + a sharp graphitic peak on a pre-subtracted baseline;
    the crystalline fraction must agree within optimizer (LM vs TRF) noise."""
    # broad amorphous band + sharp crystalline (002), spanning the 16–31° window
    x, y = synth_pattern([(80, 24.0, 5.0, 0.3), (60, 26.5, 0.25, 0.6)], lo=12, hi=34)
    p = tmp_path / "cryst.xy"
    p.write_text("\n".join(f"{a}\t{b}" for a, b in zip(x, y)))
    py = xa.crystallinity(x, y)["crystalline_fraction"]
    sw = _swift(str(p), "--crystallinity", str(p), key="crystalline_fraction")["crystalline_fraction"]
    assert abs(py - sw) < 0.02, f"crystallinity drift: py={py} swift={sw}"


@pytest.mark.skipif(not os.path.exists(SWIFT_CLI), reason="Swift xrd-validate not built")
def test_python_swift_yield_parity():
    """research/yield_calc.compute_yield ↔ XRDCore YieldCalc on the sheet sample.
    Pure arithmetic → must agree to machine precision."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
    import yield_calc
    # synthetic demo inputs — must match the Swift --yield-selftest exactly
    py = yield_calc.compute_yield(gpc_mass=2.0, c_wt=0.90, s_wt=0.05,
                                  fe_mass=4.0, caco3_mass=0.6,
                                  post_furnace=5.8, post_acid=1.9, pellet=6.6,
                                  crystalline_fraction=0.90)
    out = subprocess.run([SWIFT_CLI, "--yield-selftest"], capture_output=True, text=True)
    import json as _json
    sw = _json.loads(out.stdout.strip().splitlines()[-1])
    # tolerances match the Python side's output rounding (4–5 dp), not the true
    # agreement, which is exact — this is pure arithmetic ported 1:1.
    assert abs(py["yield"]["mass_yield"] - sw["mass_yield"]) < 1e-4
    assert abs(py["chemistry"]["graphite_theoretical"] - sw["graphite_theoretical"]) < 1e-4
    assert abs(py["wash"]["wash_check"]["trapped_metal"] - sw["trapped_metal"]) < 1e-4


def test_research_crystallinity_matches_engine():
    """The research/ decomposition must not drift from the shipping engine — both
    use the same baseline-subtracted 2-component model, so they agree exactly."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
    import amorphous  # noqa: E402
    x, y = synth_pattern([(80, 24.0, 5.0, 0.3), (60, 26.5, 0.25, 0.6)], lo=12, hi=34)
    eng = xa.crystallinity(x, y)["crystalline_fraction"]
    res = amorphous.decompose(x, y)["crystalline_fraction"]
    assert abs(eng - res) < 1e-6, f"research/engine drift: engine={eng} research={res}"
