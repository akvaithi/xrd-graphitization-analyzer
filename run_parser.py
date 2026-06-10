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
    """
    Drop the extension and bookkeeping tokens ('exported', a '(1)' copy index),
    but KEEP real parameter tokens that may follow them — e.g. a trailing 'Puck'
    in '..._exported_Puck.xy'. Removing 'exported' in place (rather than cutting
    everything after it) preserves those.
    """
    base = re.sub(r"\.(xy|txt|dat)$", "", name, flags=re.I)
    base = re.sub(r"[_\s-]*exported[_\s-]*", " ", base, flags=re.I)
    base = re.sub(r"\(\s*\d+\s*\)", " ", base)          # drop copy index like "(1)"
    return re.sub(r"\s+", " ", base).strip()


def parse_run_filename(name: str) -> dict:
    """Return a dict of run parameters extracted from one filename."""
    raw = _strip_suffix(name)
    p: dict = {"filename": name, "label": raw}

    # --- carbon source: LSPC / GPC / CPC (GCP is a common typo for GPC) -----
    # Amount may precede the token ("2CPC") or follow it in grams ("LSPC(2g)").
    m = re.search(
        r"(\d+(?:\.\d+)?)?\s*(LSPC|GPC|CPC|GCP)(?:\s*\(\s*(\d+(?:\.\d+)?)\s*g?\s*\))?",
        raw, re.I)
    if m:
        p["carbon_type"] = m.group(2).upper().replace("GCP", "GPC")
        ratio = m.group(1) or m.group(3)
        p["carbon_ratio"] = float(ratio) if ratio else None
    else:
        p["carbon_type"] = None
        p["carbon_ratio"] = None

    # --- iron content: number before 'Fe' or grams after ('Fe(6g)') --------
    # NB: use letter look-arounds (not \b) so 'Fe' is detected before '_'/digit
    # as in '6Fe2GCP' or '6Fe_0.81…'.
    m = re.search(
        r"(?:(\d+(?:\.\d+)?)\s*Fe(?![A-Za-z])|Fe\s*\(\s*(\d+(?:\.\d+)?)\s*g)",
        raw, re.I)
    p["fe_ratio"] = float(m.group(1) or m.group(2)) if m else None
    p["has_fe"] = bool(re.search(r"(?<![A-Za-z])Fe(?![A-Za-z])", raw, re.I))

    # --- CaCO3 amount: number before CaCO3 or grams after ('CaCO3(0.125g)') -
    m = re.search(
        r"(?:(\d+(?:\.\d+)?)\s*CaCO3|CaCO3\s*\(\s*(\d+(?:\.\d+)?)\s*g)",
        raw, re.I)
    p["caco3_ratio"] = float(m.group(1) or m.group(2)) if m else None

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

    # --- dwell time: hr/hrs/h, else minutes converted to hours -------------
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hrs|hr|h)(?![A-Za-z])", raw, re.I)
    if m:
        p["time_h"] = float(m.group(1))
    else:
        mm = re.search(r"(\d+(?:\.\d+)?)\s*min(?:s|utes?)?(?![A-Za-z])", raw, re.I)
        p["time_h"] = round(float(mm.group(1)) / 60.0, 4) if mm else None

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
    elif re.search(r"wash", low):
        p["wash"] = "washed"
    else:
        p["wash"] = None

    # --- optional run date (8 consecutive digits) --------------------------
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", raw)
    p["date"] = m.group(1) if m else None

    # --- standardized display name (consistent across the whole app) -------
    p["display_name"] = standard_name(p, fallback=raw)
    p["label"] = p["display_name"]
    return p


def _fmt_num(x) -> str | None:
    """Compact number: drop a trailing '.0' (2.0 -> '2', 0.137 -> '0.137')."""
    if x is None:
        return None
    f = float(x)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def standard_name(p: dict, fallback: str = "") -> str:
    """
    Build one canonical, human-readable run name from the parsed parameters so
    every view labels a file the same way, e.g.:
        2g LSPC · 6g Fe · 0.8125g CaCO₃ · 1300°C · 5H · Powder
    Amounts carry a 'g' suffix; temperature has no space; hours use 'H'; form
    and wash are title-cased. Falls back to the cleaned filename when nothing
    parses.
    """
    parts: list[str] = []

    ct = p.get("carbon_type")
    if ct:
        r = _fmt_num(p.get("carbon_ratio"))
        parts.append(f"{r}g {ct}" if r else ct)

    fr = _fmt_num(p.get("fe_ratio"))
    if fr:
        parts.append(f"{fr}g Fe")
    elif p.get("has_fe"):
        parts.append("Fe")

    cc = _fmt_num(p.get("caco3_ratio"))
    if cc:
        parts.append(f"{cc}g CaCO₃")

    if p.get("temperature_C") is not None:
        parts.append(f"{int(p['temperature_C'])}°C")

    th = _fmt_num(p.get("time_h"))
    if th:
        parts.append(f"{th}H")

    if p.get("form"):
        parts.append(str(p["form"]).title())
    if p.get("wash"):
        parts.append(str(p["wash"]).title())

    return " · ".join(parts) if parts else (fallback or p.get("filename", ""))


if __name__ == "__main__":  # quick self-test against stdin-provided names
    import sys
    for line in sys.stdin:
        line = line.strip()
        if line:
            print(parse_run_filename(line))
