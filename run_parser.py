"""
run_parser.py — Extract synthesis-run parameters from messy .xy file names.

Sample names (no fixed format, mixed separators / casing):
    2CPC-6Fe-0.137 CaCO3-1300 C-5 hr - puck_exported.xy
    6Fe2GCP_0.8125CaCO3_1300_5h_Puck_aftr_wash_exported.xy
    1GPC-7.5CACO3-1300C-5HRS_exported (1).xy
    GPC_Fe_CaCO3_1100_5h_exported.xy

The parser is tolerant: each field is found independently by a regex, so the
order, separators and casing of tokens don't matter. Missing fields → None.
"""

from __future__ import annotations

import re

# Categorical parameters used for grouping in the dashboard
CATEGORICAL_FIELDS = ("carbon_type", "form", "wash")
# Numeric parameters usable as a chart X-axis
NUMERIC_FIELDS = ("temperature_C", "caco3_ratio", "time_h", "fe_ratio", "carbon_ratio")


def _strip_suffix(name: str) -> str:
    """Drop extension and the '_exported…' / copy-index tails."""
    base = re.sub(r"\.xy$", "", name, flags=re.I)
    # everything from "_exported" / " exported" onward is bookkeeping
    base = re.split(r"[_\s-]*exported", base, flags=re.I)[0]
    return base.strip()


def parse_run_filename(name: str) -> dict:
    """Return a dict of run parameters extracted from one filename."""
    raw = _strip_suffix(name)
    p: dict = {"filename": name, "label": raw}

    # --- carbon source: GPC / CPC (GCP is a common typo for GPC) -----------
    m = re.search(r"(\d+(?:\.\d+)?)?\s*(GPC|CPC|GCP)", raw, re.I)
    if m:
        ctype = m.group(2).upper().replace("GCP", "GPC")
        p["carbon_type"] = ctype
        p["carbon_ratio"] = float(m.group(1)) if m.group(1) else None
    else:
        p["carbon_type"] = None
        p["carbon_ratio"] = None

    # --- iron content: number directly before 'Fe' (else just presence) ----
    # NB: use letter look-arounds (not \b) so 'Fe' is detected before '_'/digit
    # as in '6Fe2GCP' or '6Fe_0.81…'.
    m = re.search(r"(\d+(?:\.\d+)?)\s*Fe(?![A-Za-z])", raw, re.I)
    if m:
        p["fe_ratio"] = float(m.group(1))
    else:
        p["fe_ratio"] = None
    p["has_fe"] = bool(re.search(r"(?<![A-Za-z])Fe(?![A-Za-z])", raw, re.I))

    # --- CaCO3 amount: number before CaCO3 ---------------------------------
    m = re.search(r"(\d+(?:\.\d+)?)\s*CaCO3", raw, re.I)
    p["caco3_ratio"] = float(m.group(1)) if m else None

    # --- temperature: 3–4 digit value in 800–1600 °C ----------------------
    temp = None
    for tm in re.finditer(r"(?<!\d)(\d{3,4})\s*C\b", raw, re.I):       # explicit "…C"
        if 800 <= int(tm.group(1)) <= 1600:
            temp = int(tm.group(1)); break
    if temp is None:                                                   # bare number fallback
        for tm in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", raw):
            if 800 <= int(tm.group(1)) <= 1600:
                temp = int(tm.group(1)); break
    p["temperature_C"] = temp

    # --- dwell time: number before hr/hrs/h (allow trailing '_'/digit) -----
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hrs|hr|h)(?![A-Za-z])", raw, re.I)
    p["time_h"] = float(m.group(1)) if m else None

    # --- sample form -------------------------------------------------------
    if re.search(r"puck", raw, re.I):
        p["form"] = "puck"
    elif re.search(r"powder", raw, re.I):
        p["form"] = "powder"
    else:
        p["form"] = None

    # --- wash state --------------------------------------------------------
    low = raw.lower()
    if re.search(r"no\s*wash", low):
        p["wash"] = "no wash"
    elif re.search(r"(aftr|after)[\s_-]*wash", low):
        p["wash"] = "after wash"
    elif re.search(r"before[\s_-]*wash", low):
        p["wash"] = "before wash"
    else:
        p["wash"] = None

    # --- optional run date (8 consecutive digits) --------------------------
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", raw)
    p["date"] = m.group(1) if m else None

    return p


if __name__ == "__main__":  # quick self-test against stdin-provided names
    import sys
    for line in sys.stdin:
        line = line.strip()
        if line:
            print(parse_run_filename(line))
