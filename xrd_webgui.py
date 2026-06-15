"""
xrd_webgui.py — Browser GUI for the XRD Graphitization Analyzer.

Single-page local web app (stdlib http.server + headless matplotlib). One shared
file upload feeds four tabbed views:

  • Analyze       — per-file DG fit (graphitic + pure-Lorentzian turbostratic over
                    the 24–27.5° window) with a high-resolution plot; page files.
  • Compare       — parsed run parameters table + ONE comparison chart whose points
                    are toggled on/off by per-group and per-run checkboxes; CSV.
  • Stack spectra — overlay/waterfall of raw intensities (offset slider) to compare
                    peak heights across runs.
  • Manual calc   — DG from hand-entered Origin peaks (NETL excel sheet).

No Tk (works where macOS Tcl/Tk is broken) and no .brml. Wavelength is fixed to
the Cu Kα standard (1.54187 Å) in xrd_analyzer.DEFAULT_WAVELENGTH.

Usage:
    python3 xrd_webgui.py                 # http://127.0.0.1:8000 (opens browser)
    python3 xrd_webgui.py --port 8642
    PORT=8642 python3 xrd_webgui.py       # cloud: binds 0.0.0.0:$PORT, no browser
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

# Headless backend — set before importing pyplot/Figure
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

# Plot text uses a clean open sans-serif (no bundled/proprietary fonts shipped).
matplotlib.rcParams["font.family"] = "sans-serif"
matplotlib.rcParams["font.sans-serif"] = ["DejaVu Sans", "Helvetica", "Arial"]

from xrd_analyzer import (
    ANALYSIS_WINDOW,
    FitError,
    GraphitizationAnalyzer,
    XRDPattern,
    dg_from_peaks,
    fit_netl,
    pseudo_voigt,
)
from run_parser import parse_run_filename

# ---------------------------------------------------------------------------
# Request limits (env-overridable) — basic abuse / DoS protection
# ---------------------------------------------------------------------------
MAX_UPLOAD_MB   = float(os.environ.get("XRD_MAX_UPLOAD_MB", "50"))    # per request
MAX_BODY_BYTES  = int(MAX_UPLOAD_MB * 1024 * 1024)
MAX_BATCH_FILES = int(os.environ.get("XRD_MAX_BATCH_FILES", "300"))   # files per batch
MAX_CONCURRENT  = int(os.environ.get("XRD_MAX_CONCURRENT", "3"))      # simultaneous heavy ops
BUSY_WAIT_SEC   = float(os.environ.get("XRD_BUSY_WAIT_SEC", "20"))    # wait for a slot before 429
REQUEST_TIMEOUT = float(os.environ.get("XRD_REQUEST_TIMEOUT", "60"))  # per-socket op (slowloris guard)

# Limits concurrent CPU/RAM-heavy fitting+plotting across all POST endpoints.
_work_sem = threading.BoundedSemaphore(MAX_CONCURRENT)


# ---------------------------------------------------------------------------
# Plot colours
# ---------------------------------------------------------------------------
# macOS system palette (Apple HIG) for plot strokes
RED_PEAK  = "#ff3b30"   # graphitic     — systemRed
BLUE_PEAK = "#30b0c7"   # turbostratic  — systemTeal
FIT_COL   = "#5e5ce6"   # total fit     — systemIndigo
RAW_COL   = "#aeaeb2"   # raw points    — systemGray


def _plot_theme(theme: str) -> dict:
    """Plot palette matched to the macOS light/dark appearance (plot face = card)."""
    if theme == "dark":
        return {"face": "#2c2c2e", "axes": "#2c2c2e", "text": "#f5f5f7",
                "muted": "#8e8e93", "grid": "#3a3a3c", "raw": "#aeaeb2"}
    return {"face": "#ffffff", "axes": "#ffffff", "text": "#1d1d1f",
            "muted": "#8e8e93", "grid": "#e5e5ea", "raw": "#48484a"}


# Plot font sizes (points). Small points + a large figure → when the browser
# scales the PNG into its card the text lands near the ~13px UI text, while the
# big figure keeps the plot area large and high-resolution.
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND = 12, 11, 10, 10


# ---------------------------------------------------------------------------
# Plot rendering — per-file fit
# ---------------------------------------------------------------------------

def render_plot(pattern: XRDPattern, res: dict, theme: str = "dark") -> str:
    """Render the fit (high-res, with raw points) to a base64 PNG data-URI."""
    pal = _plot_theme(theme)
    fig = Figure(figsize=(9.0, 5.6), dpi=240, facecolor=pal["face"])
    ax = fig.add_subplot(111)
    ax.set_facecolor(pal["axes"])

    g, t = res["graphitic"], res["turbostratic"]
    x, y, _bl = pattern.baseline_subtracted(*ANALYSIS_WINDOW)
    xp = np.linspace(x.min(), x.max(), 1200)
    yg = pseudo_voigt(xp, g["A"], g["xc"], g["w"], g["mu"])
    yt = pseudo_voigt(xp, t["A"], t["xc"], t["w"], t["mu"])   # mu=1 (Lorentzian)
    ytot = yg + yt
    # raw data points — open circles so the fit line stays visible underneath
    ax.scatter(x, y, s=30, facecolors="none", edgecolors=pal["raw"], linewidths=1.0,
               alpha=0.95, label="Raw data (baseline-subtracted)", zorder=6)
    ax.fill_between(xp, yg, alpha=0.22, color=RED_PEAK)
    ax.plot(xp, yg, color=RED_PEAK, lw=1.8, label=f"Graphitic 2θ={g['xc']:.3f}°")
    ax.fill_between(xp, yt, alpha=0.16, color=BLUE_PEAK)
    ax.plot(xp, yt, color=BLUE_PEAK, lw=1.8,
            label=f"Turbostratic 2θ={t['xc']:.3f}° (Lorentzian)")
    ax.plot(xp, ytot, color=FIT_COL, lw=2.4, ls="--", label="Total fit", zorder=5)

    ax.tick_params(colors=pal["muted"], labelsize=FS_TICK)
    for spine in ax.spines.values():
        spine.set_edgecolor(pal["muted"])
    ax.grid(True, color=pal["grid"], lw=0.6)
    ax.set_xlabel("2θ  (degrees)", color=pal["muted"], fontsize=FS_LABEL)
    ax.set_ylabel("Intensity  (a.u.)", color=pal["muted"], fontsize=FS_LABEL)
    ax.set_title(f"Carbon (002) fit — DG {res['DG_percent']:.1f}%",
                 color=pal["text"], fontsize=FS_TITLE, pad=8)
    ax.legend(fontsize=FS_LEGEND, facecolor=pal["face"], edgecolor=pal["muted"],
              labelcolor=pal["text"], framealpha=0.9)
    fig.tight_layout(pad=1.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=pal["face"], dpi=240)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


def render_plot_netl(pattern: XRDPattern, res: dict, theme: str = "dark") -> str:
    """Render a NETL-faithful fit (``fit_netl`` result, includes y0 and 1/2 peaks)."""
    pal = _plot_theme(theme)
    fig = Figure(figsize=(9.0, 5.6), dpi=240, facecolor=pal["face"])
    ax = fig.add_subplot(111)
    ax.set_facecolor(pal["axes"])

    x = np.asarray(res["points_x"], float)
    y = np.asarray(res["points_y"], float)
    y0 = float(res.get("y0", 0.0))
    g, t = res["graphitic"], res.get("turbostratic")
    xp = np.linspace(x.min(), x.max(), 1200)
    yg = y0 + pseudo_voigt(xp, g["A"], g["xc"], g["w"], g["mu"])

    ax.scatter(x, y, s=30, facecolors="none", edgecolors=pal["raw"], linewidths=1.0,
               alpha=0.95, label="Raw data", zorder=6)
    ax.fill_between(xp, yg, y0, alpha=0.22, color=RED_PEAK)
    ax.plot(xp, yg, color=RED_PEAK, lw=1.8, label=f"Graphitic 2θ={g['xc']:.3f}°")
    if t is not None:
        yt = y0 + pseudo_voigt(xp, t["A"], t["xc"], t["w"], 1.0)
        ytot = yg + yt - y0
        ax.fill_between(xp, yt, y0, alpha=0.16, color=BLUE_PEAK)
        ax.plot(xp, yt, color=BLUE_PEAK, lw=1.8,
                label=f"Turbostratic 2θ={t['xc']:.3f}° (Lorentzian)")
    else:
        ytot = yg
    ax.plot(xp, ytot, color=FIT_COL, lw=2.4, ls="--", label="Total fit", zorder=5)

    ax.tick_params(colors=pal["muted"], labelsize=FS_TICK)
    for spine in ax.spines.values():
        spine.set_edgecolor(pal["muted"])
    ax.grid(True, color=pal["grid"], lw=0.6)
    ax.set_xlabel("2θ  (degrees)", color=pal["muted"], fontsize=FS_LABEL)
    ax.set_ylabel("Intensity  (a.u.)", color=pal["muted"], fontsize=FS_LABEL)
    ax.set_title(f"Carbon (002) fit — DG {res['DG_percent']:.1f}%  (AI-assisted)",
                 color=pal["text"], fontsize=FS_TITLE, pad=8)
    ax.legend(fontsize=FS_LEGEND, facecolor=pal["face"], edgecolor=pal["muted"],
              labelcolor=pal["text"], framealpha=0.9)
    fig.tight_layout(pad=1.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=pal["face"], dpi=240)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# Compare — parse run parameters, analyse, build dataset & comparison chart
# ---------------------------------------------------------------------------

# Selectable chart axes / metrics and their display labels
X_LABELS = {
    "temperature_C": "Temperature (°C)",
    "caco3_ratio":   "CaCO₃ ratio",
    "time_h":        "Dwell time (h)",
    "fe_ratio":      "Fe ratio",
    "carbon_ratio":  "Carbon ratio",
}
Y_LABELS = {
    "DG":            "Degree of Graphitization (%)",
    "Lc":            "Crystallite height Lc (Å)",
    "d_prime":       "d′ weighted (Å)",
    "graphitic_xc":  "Graphitic (002) 2θ (°)",
}
GROUP_LABELS = {
    "carbon_type": "Carbon type",
    "form":        "Sample form",
    "wash":        "Wash state",
    "none":        "(none)",
}
# macOS system colours for grouped series / stacked spectra
_SERIES_COLORS = ["#007aff", "#ff3b30", "#34c759", "#ff9500", "#af52de",
                  "#5ac8fa", "#ff2d55", "#5856d6", "#ffcc00", "#00c7be",
                  "#a2845e", "#30b0c7", "#ff9f0a", "#bf5af2", "#64d2ff", "#ac8e68"]


def build_dashboard_rows(files: list[dict]) -> list[dict]:
    """
    For each {name, xy} entry: parse run parameters from the name and analyse
    the pattern with the standard pipeline. Returns one flat row per file,
    index-aligned with ``files``.
    """
    rows: list[dict] = []
    for f in files:
        name = f.get("name", "")
        row = parse_run_filename(name)
        row["file"] = name
        for k in ("DG", "Lc", "d_prime", "graphitic_xc", "turbostratic_xc"):
            row[k] = None
        try:
            res = GraphitizationAnalyzer(XRDPattern.from_text(f.get("xy", ""))).run()
            row["DG"] = res["DG_percent"]
            row["Lc"] = res["crystallite_height_Lc_angstrom"]
            row["d_prime"] = res["d_spacing_weighted_angstrom"]
            row["graphitic_xc"] = res["graphitic"]["xc"]
            row["turbostratic_xc"] = res["turbostratic"]["xc"]
        except (FitError, ValueError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def render_dashboard_chart(rows: list[dict], x: str, y: str, group: str,
                           theme: str = "dark") -> str:
    """
    Scatter/line chart of metric ``y`` vs parameter ``x``, coloured by ``group``.

    ``rows`` is the already-filtered set the caller wants plotted (the page hides
    runs/groups via checkboxes and sends only the visible ones).
    """
    if x not in X_LABELS:
        x = "temperature_C"
    if y not in Y_LABELS:
        y = "DG"
    if group not in GROUP_LABELS:
        group = "carbon_type"

    pal = _plot_theme(theme)
    fig = Figure(figsize=(8.6, 5.4), dpi=220, facecolor=pal["face"])
    ax = fig.add_subplot(111)
    ax.set_facecolor(pal["axes"])

    # bucket points by group value
    series: dict[str, list[tuple]] = {}
    for r in rows:
        xv, yv = r.get(x), r.get(y)
        if xv is None or yv is None:
            continue
        gv = "all" if group == "none" else (r.get(group) or "unspecified")
        series.setdefault(str(gv), []).append((float(xv), float(yv)))

    if not series:
        ax.text(0.5, 0.5, "No data — check at least one run/group",
                transform=ax.transAxes, ha="center", va="center",
                color=pal["muted"], fontsize=FS_LABEL)
    else:
        for i, (gv, pts) in enumerate(sorted(series.items())):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
            # Trend line through the MEAN y at each distinct x (avoids zigzag
            # when several runs share a temperature / parameter value).
            buckets: dict[float, list[float]] = {}
            for xv, yv in pts:
                buckets.setdefault(xv, []).append(yv)
            mx = sorted(buckets)
            if len(mx) > 1:
                my = [sum(buckets[v]) / len(buckets[v]) for v in mx]
                ax.plot(mx, my, "-", color=color, lw=1.5, alpha=0.45, zorder=2)
            ax.scatter(xs, ys, s=58, color=color, edgecolor=pal["axes"],
                       linewidth=0.7, zorder=3,
                       label=(gv if group != "none" else None))
        if group != "none":
            ax.legend(title=GROUP_LABELS[group], fontsize=FS_LEGEND, title_fontsize=FS_LEGEND,
                      facecolor=pal["face"], edgecolor=pal["muted"],
                      labelcolor=pal["text"], framealpha=0.9)

    ax.tick_params(colors=pal["muted"], labelsize=FS_TICK)
    for spine in ax.spines.values():
        spine.set_edgecolor(pal["muted"])
    ax.grid(True, color=pal["grid"], lw=0.6)
    ax.set_xlabel(X_LABELS[x], color=pal["muted"], fontsize=FS_LABEL)
    ax.set_ylabel(Y_LABELS[y], color=pal["muted"], fontsize=FS_LABEL)
    ax.set_title(f"{Y_LABELS[y]}  vs  {X_LABELS[x]}", color=pal["text"], fontsize=FS_TITLE, pad=8)
    fig.tight_layout(pad=1.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=pal["face"], dpi=220)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# Stack spectra — overlay / waterfall of raw intensities to compare peak heights
# ---------------------------------------------------------------------------

def render_stack(files: list[dict], offset: float = 0.25, theme: str = "dark",
                 baseline: bool = False, window: tuple | None = None) -> str:
    """
    Overlay each file's raw intensity vs 2θ. ``offset`` (0–1, fraction of the
    largest peak) shifts each successive curve up: 0 → flat overlay, >0 →
    waterfall. ``window`` restricts the 2θ range; ``baseline`` removes a linear
    background across the shown range so peak heights compare directly.
    """
    pal = _plot_theme(theme)
    fig = Figure(figsize=(9.4, 6.0), dpi=220, facecolor=pal["face"])
    ax = fig.add_subplot(111)
    ax.set_facecolor(pal["axes"])

    series: list[tuple[str, np.ndarray, np.ndarray]] = []
    gmax = 0.0
    for f in files:
        try:
            p = XRDPattern.from_text(f.get("xy", ""))
        except Exception:  # noqa: BLE001 — skip an unreadable file, keep the rest
            continue
        x, y = p.two_theta, p.intensity
        if window is not None:
            m = (x >= window[0]) & (x <= window[1])
            x, y = x[m], y[m]
        if x.size == 0:
            continue
        if baseline and x.size > 1:
            y = y - np.linspace(y[0], y[-1], y.size)
        name = str(f.get("name", ""))
        if len(name) > 48:
            name = name[:47] + "…"
        series.append((name, x, y))
        gmax = max(gmax, float(np.nanmax(y)))

    if not series:
        ax.text(0.5, 0.5, "Select files to stack", transform=ax.transAxes,
                ha="center", va="center", color=pal["muted"], fontsize=FS_LABEL)
    else:
        step = max(offset, 0.0) * gmax
        for i, (name, x, y) in enumerate(series):
            color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
            ax.plot(x, y + i * step, lw=1.0, color=color, alpha=0.95, label=name)
        # A long legend gets unreadable; only show it for a manageable count.
        if len(series) <= 14:
            ax.legend(fontsize=FS_LEGEND, facecolor=pal["face"], edgecolor=pal["muted"],
                      labelcolor=pal["text"], framealpha=0.9, loc="upper right")
        else:
            ax.text(0.99, 0.98, f"{len(series)} spectra", transform=ax.transAxes,
                    ha="right", va="top", color=pal["muted"], fontsize=FS_LEGEND)

    ax.tick_params(colors=pal["muted"], labelsize=FS_TICK)
    for spine in ax.spines.values():
        spine.set_edgecolor(pal["muted"])
    ax.grid(True, color=pal["grid"], lw=0.6)
    ax.set_xlabel("2θ  (degrees)", color=pal["muted"], fontsize=FS_LABEL)
    ax.set_ylabel("Intensity  (a.u.)" + ("  — offset" if (series and offset > 0) else ""),
                  color=pal["muted"], fontsize=FS_LABEL)
    mode = "waterfall" if offset > 0 else "overlay"
    ax.set_title(f"Stacked raw spectra ({mode})", color=pal["text"], fontsize=FS_TITLE, pad=8)
    fig.tight_layout(pad=1.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=pal["face"], dpi=220)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# Single-page HTML (tabs share one upload)
# ---------------------------------------------------------------------------

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XRD Graphitization Analyzer</title>
<style>
  /* System UI font stack — renders as SF Pro on macOS/iOS, Segoe UI on Windows,
     Roboto on Android/ChromeOS. Nothing bundled, nothing proprietary shipped. */

  /* macOS semantic colour system (Apple HIG) — light is the default appearance */
  :root {
    --bg:#f2f2f7; --bg2:#ffffff; --card:#ffffff;
    --bar:rgba(246,246,248,0.72); --bar-border:rgba(0,0,0,0.10);
    --label:rgba(0,0,0,0.85); --label2:rgba(0,0,0,0.50); --label3:rgba(0,0,0,0.28);
    --sep:rgba(0,0,0,0.10); --sep2:rgba(0,0,0,0.06);
    --fill:rgba(118,118,128,0.12); --fill2:rgba(118,118,128,0.20);
    --accent:#007aff; --accent-press:#0063cc; --on-accent:#ffffff;
    --green:#34c759; --red:#ff3b30; --orange:#ff9500;
    --shadow:0 1px 2px rgba(0,0,0,0.10), 0 6px 18px rgba(0,0,0,0.06);
    --focus:rgba(0,122,255,0.45);
    --font-text:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
    --font-display:var(--font-text);
  }
  :root[data-theme="dark"] {
    --bg:#1c1c1e; --bg2:#000000; --card:#2c2c2e;
    --bar:rgba(40,40,42,0.72); --bar-border:rgba(255,255,255,0.12);
    --label:rgba(255,255,255,0.92); --label2:rgba(255,255,255,0.55); --label3:rgba(255,255,255,0.30);
    --sep:rgba(255,255,255,0.12); --sep2:rgba(255,255,255,0.07);
    --fill:rgba(118,118,128,0.24); --fill2:rgba(118,118,128,0.36);
    --accent:#0a84ff; --accent-press:#3a9bff; --on-accent:#ffffff;
    --green:#30d158; --red:#ff453a; --orange:#ff9f0a;
    --shadow:0 1px 2px rgba(0,0,0,0.40), 0 8px 24px rgba(0,0,0,0.36);
    --focus:rgba(10,132,255,0.55);
  }
  * { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; }
  body { margin:0; background:var(--bg); color:var(--label); font-family:var(--font-text);
         font-size:13px; line-height:1.45; -webkit-font-smoothing:antialiased;
         -moz-osx-font-smoothing:grayscale; text-rendering:optimizeLegibility; }
  ::selection { background:color-mix(in srgb, var(--accent) 26%, transparent); }
  a { color:var(--accent); }

  /* Toolbar — vibrant material */
  header { position:sticky; top:0; z-index:20; display:flex; align-items:center; gap:12px;
           flex-wrap:wrap; padding:11px 20px; background:var(--bar);
           -webkit-backdrop-filter:saturate(180%) blur(20px); backdrop-filter:saturate(180%) blur(20px);
           border-bottom:0.5px solid var(--bar-border); }
  header h1 { font-family:var(--font-display); font-size:17px; font-weight:600; letter-spacing:-0.01em;
              color:var(--label); margin:0; flex:0 0 auto; }
  .spacer { flex:1 1 auto; }
  #fname { color:var(--label2); font-size:12px; }

  /* Push buttons */
  button, .filebtn { font-family:inherit; font-size:13px; font-weight:500; cursor:pointer;
    border:none; border-radius:8px; padding:7px 14px; color:var(--label); background:var(--fill);
    transition:background .12s, transform .04s, box-shadow .12s; }
  button:hover, .filebtn:hover { background:var(--fill2); }
  button:active, .filebtn:active { transform:scale(0.975); }
  button.primary, .filebtn { background:var(--accent); color:var(--on-accent); font-weight:600;
    box-shadow:0 1px 1.5px rgba(0,0,0,0.18); }
  button.primary:hover, .filebtn:hover { background:var(--accent-press); }
  button:disabled { opacity:.4; cursor:not-allowed; transform:none; }
  .themebtn { background:var(--fill); color:var(--label); padding:6px 12px; font-size:12px; box-shadow:none; }
  .themebtn:hover { background:var(--fill2); }
  .mini { font-size:12px; padding:5px 11px; }

  /* Pop-up buttons / fields / controls */
  select { font-family:inherit; font-size:13px; color:var(--label); background:var(--fill);
    border:0.5px solid var(--sep); border-radius:7px; padding:6px 10px; transition:background .12s; }
  select:hover { background:var(--fill2); }
  input.num { font-family:inherit; font-size:13px; color:var(--label); background:var(--bg2);
    border:0.5px solid var(--sep); border-radius:7px; padding:6px 10px; width:130px; }
  input[type=checkbox] { accent-color:var(--accent); width:15px; height:15px; }
  input[type=range] { accent-color:var(--accent); }
  :focus-visible { outline:3px solid var(--focus); outline-offset:1px; border-radius:7px; }

  /* Segmented control (tabs) */
  nav.tabs { position:sticky; top:48px; z-index:15; display:flex; justify-content:center;
             padding:10px 16px; background:var(--bar);
             -webkit-backdrop-filter:saturate(180%) blur(20px); backdrop-filter:saturate(180%) blur(20px);
             border-bottom:0.5px solid var(--bar-border); }
  .seg { display:inline-flex; gap:2px; padding:2px; background:var(--fill); border-radius:9px; }
  .tab { background:transparent; color:var(--label); font-size:13px; font-weight:500;
         padding:5px 16px; border-radius:7px; transition:background .12s, box-shadow .12s; }
  .tab:hover { background:transparent; color:var(--label); }
  .tab.active { background:var(--card); color:var(--label); font-weight:600;
    box-shadow:0 1px 2px rgba(0,0,0,0.16); }

  .panel { display:none; padding:20px; max-width:1240px; margin:0 auto; }
  .panel.active { display:block; }
  .grid2 { display:grid; grid-template-columns:minmax(320px,400px) 1fr; gap:18px; align-items:start; }
  @media (max-width:900px){ .grid2{ grid-template-columns:1fr; } }
  .grid-chart { display:grid; grid-template-columns:1fr 260px; gap:18px; align-items:start; }
  @media (max-width:840px){ .grid-chart{ grid-template-columns:1fr; } }

  /* Cards — grouped content */
  .card { background:var(--card); border-radius:14px; padding:20px; margin-bottom:18px;
          box-shadow:var(--shadow); overflow:auto; }
  .card:last-child { margin-bottom:0; }
  .card h2 { font-family:var(--font-display); font-size:15px; font-weight:600; letter-spacing:-0.01em;
             color:var(--label); margin:0 0 14px; }
  .filetitle { font-family:var(--font-display); font-size:16px; font-weight:600; letter-spacing:-0.01em;
               color:var(--label); margin:0 0 2px; line-height:1.3; word-break:break-word; }
  .filesub { color:var(--label3); font-size:11px; margin:0 0 14px; word-break:break-all; }
  .hsub { color:var(--label2); font-size:12px; font-weight:400; }
  .section { color:var(--label2); font-size:11px; font-weight:600; letter-spacing:0.04em;
             text-transform:uppercase; margin:18px 0 6px; padding-bottom:5px;
             border-bottom:0.5px solid var(--sep); }
  .section:first-child { margin-top:0; }
  .row { display:flex; justify-content:space-between; align-items:baseline; gap:12px;
         padding:5px 0; font-size:13px; border-bottom:0.5px solid var(--sep2); }
  .row:last-child { border-bottom:none; }
  .row .val { color:var(--label); font-weight:600; font-variant-numeric:tabular-nums; }
  .row .unit { color:var(--label2); font-size:11px; margin-left:4px; font-weight:400; }

  /* DG callout */
  .dgbox.dgtop { margin-top:0; margin-bottom:18px; }
  .dgbox { margin-top:20px; background:color-mix(in srgb, var(--accent) 12%, var(--card));
           border:0.5px solid color-mix(in srgb, var(--accent) 30%, transparent);
           border-radius:14px; padding:20px; text-align:center; }
  .dgbox .cap { color:var(--label2); font-size:12px; }
  .dgbox .dg { font-family:var(--font-display); color:var(--accent); font-size:44px; font-weight:600;
               letter-spacing:-0.02em; margin:6px 0; font-variant-numeric:tabular-nums; }

  img.plot, img.chartimg { width:100%; border-radius:10px; display:block; }
  #plotwrap, #chartwrap, #stackwrap { display:flex; align-items:center; justify-content:center;
            min-height:340px; color:var(--label2); }
  /* empty Analyze cards match heights; once analysed, cards size to content (no big void) */
  #results > .placeholder { min-height:340px; display:flex; align-items:center; justify-content:center; padding:0; }

  /* Category filter pills */
  .filters { display:flex; flex-direction:column; gap:8px; margin-bottom:16px; }
  .filtergrp { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .filterlbl { color:var(--label2); font-size:11px; font-weight:600; letter-spacing:0.04em;
               text-transform:uppercase; width:48px; flex:0 0 auto; }
  .filterpill { font-size:12px; font-weight:500; padding:3px 12px; border-radius:14px;
                background:var(--fill); color:var(--label2); }
  .filterpill:hover { background:var(--fill2); }
  .filterpill.active { background:var(--accent); color:var(--on-accent); }
  .runlink { color:var(--accent); cursor:pointer; text-decoration:none; }
  .runlink:hover { text-decoration:underline; }
  th.ck-col, td.ck-col { width:30px; text-align:center; padding-left:6px; padding-right:6px; }

  .filebar { display:flex; align-items:center; gap:10px; margin-bottom:16px; font-size:13px; }
  .filebar button { padding:5px 12px; }
  #fileSel { max-width:560px; }
  .muted { color:var(--label2); font-size:12px; }

  .ctrls { display:flex; gap:14px; flex-wrap:wrap; align-items:center; margin-bottom:16px; }
  .ctrls label { color:var(--label2); font-size:12px; display:flex; gap:7px; align-items:center; }
  .ctrls input[type=range] { vertical-align:middle; }
  .tog { display:flex; gap:7px; align-items:center; color:var(--label)!important; }

  /* Checklists */
  .checks { font-size:12px; }
  .checkhdr { display:flex; justify-content:space-between; align-items:baseline;
              color:var(--label2); font-weight:600; font-size:11px; letter-spacing:0.03em;
              text-transform:uppercase; margin:0 0 7px; }
  .checkhdr:not(:first-child) { margin-top:16px; }
  .checkhdr a { color:var(--accent); cursor:pointer; text-decoration:none; font-weight:500;
                font-size:11px; text-transform:none; letter-spacing:0; }
  .checkhdr a:hover { text-decoration:underline; }
  .checklist { max-height:300px; overflow:auto; border:0.5px solid var(--sep);
               border-radius:10px; padding:8px 10px; background:var(--bg); }
  .ck { display:flex; gap:8px; align-items:center; padding:3px 0; color:var(--label);
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; cursor:pointer; }
  .ck input { flex:0 0 auto; }

  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px; }
  .chip { background:var(--fill); color:var(--label2); border-radius:8px; padding:3px 10px;
          font-size:11px; font-weight:500; }

  /* Table */
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:7px 10px; text-align:right; border-bottom:0.5px solid var(--sep);
           white-space:nowrap; font-variant-numeric:tabular-nums; }
  th { color:var(--label2); font-weight:600; position:sticky; top:0; background:var(--card); }
  td.lbl, th.lbl { text-align:left; min-width:230px; white-space:normal;
                   font-variant-numeric:normal; }
  td.miss { color:var(--label3); }
  tr.err td { color:var(--red); }

  .peakrow { display:flex; gap:10px; align-items:center; margin-bottom:10px; }
  .peakrow label { color:var(--label2); font-size:12px; width:80px; }
  .hint { color:var(--label2); font-size:12px; margin:8px 0 16px; line-height:1.5; }

  /* Status bar */
  #status { position:sticky; bottom:0; padding:8px 20px; background:var(--bar);
            -webkit-backdrop-filter:saturate(180%) blur(20px); backdrop-filter:saturate(180%) blur(20px);
            border-top:0.5px solid var(--bar-border); color:var(--label2); font-size:11px; }
  #status.error { color:var(--red); }
  .placeholder { color:var(--label2); text-align:center; padding:34px 0; font-size:13px; }
</style>
<script>
(function(){var d=document.documentElement;
  d.dataset.theme=localStorage.getItem('xrd-theme')||'light';
  addEventListener('DOMContentLoaded',function(){var b=document.getElementById('themeBtn');
    if(b)b.onclick=function(){var n=d.dataset.theme==='dark'?'light':'dark';
      d.dataset.theme=n;localStorage.setItem('xrd-theme',n);b.textContent=n==='dark'?'◐ Light':'◑ Dark';
      if(window.onThemeChange)window.onThemeChange(n);};
    if(b)b.textContent=d.dataset.theme==='dark'?'◐ Light':'◑ Dark';});})();
</script>
</head>
<body>
<header>
  <h1>XRD Graphitization Analyzer</h1>
  <div class="spacer"></div>
  <label class="filebtn">Choose .xy file(s)…
    <input id="file" type="file" accept=".xy,.txt,.dat,text/plain" multiple style="display:none">
  </label>
  <span id="fname">no files selected</span>
  <button id="themeBtn" class="themebtn"></button>
</header>

<nav class="tabs"><div class="seg">
  <button class="tab active" data-tab="analyze">Analyze</button>
  <button class="tab" data-tab="compare">Compare</button>
  <button class="tab" data-tab="stack">Stack spectra</button>
  <button class="tab" data-tab="manual">Manual calc</button>
</div></nav>

<!-- ANALYZE ----------------------------------------------------------------->
<section id="tab-analyze" class="panel active">
  <div class="filebar" id="fileBar" style="display:none">
    <button id="prevBtn">◀ Prev</button>
    <select id="fileSel" title="Jump to a file"></select>
    <button id="nextBtn">Next ▶</button>
    <span id="fileInfo" class="muted"></span>
  </div>
  <div class="ctrls" id="aiBar" style="display:none">
    <button id="aiBtn" class="mini">✨ Suggest deconvolution (local AI)</button>
    <span id="aiNote" class="muted"></span>
  </div>
  <div class="grid2">
    <div class="card" id="results"><div class="placeholder">Choose .xy file(s) — analysis runs automatically.</div></div>
    <div class="card"><div id="plotwrap"><span class="placeholder">Fit plot appears here.</span></div></div>
  </div>
</section>

<!-- COMPARE ----------------------------------------------------------------->
<section id="tab-compare" class="panel">
  <div class="card">
    <div class="ctrls">
      <label>Y <select id="ySel"></select></label>
      <label>X <select id="xSel"></select></label>
      <label>Color by <select id="gSel"></select></label>
      <button id="csvBtn" class="mini" disabled>Download CSV</button>
    </div>
    <div id="filters" class="filters"></div>
    <div id="chartwrap"><div class="placeholder">Upload runs to compare.</div></div>
  </div>
  <div class="card">
    <h2>Parsed runs &amp; results <span class="hsub">— tick to include in the chart · click a run name to open its fit</span></h2>
    <div id="chips" class="chips"></div>
    <div id="tablewrap"><div class="placeholder">Parsed run parameters appear here.</div></div>
  </div>
</section>

<!-- STACK ------------------------------------------------------------------->
<section id="tab-stack" class="panel">
  <div class="card">
    <div class="ctrls">
      <label>Offset <input id="offset" type="range" min="0" max="1" step="0.05" value="0">
        <span id="offVal" class="muted">0.00</span></label>
      <label class="tog"><input type="checkbox" id="stkZoom"> Zoom (002) 24–30°</label>
      <label class="tog"><input type="checkbox" id="stkBase"> Baseline subtract</label>
    </div>
    <div class="grid-chart">
      <div id="stackwrap"><div class="placeholder">Upload spectra, then check files to stack.</div></div>
      <div class="checks">
        <div class="checkhdr">Files <span><a id="stkAll">all</a> · <a id="stkNone">none</a></span></div>
        <div id="stkChecks" class="checklist"></div>
      </div>
    </div>
  </div>
</section>

<!-- MANUAL ------------------------------------------------------------------>
<section id="tab-manual" class="panel">
  <div class="grid2">
    <div class="card">
      <h2>Enter Origin fit peaks (NETL excel sheet)</h2>
      <label class="tog" style="margin-bottom:14px"><input type="checkbox" id="two"> Two peaks (graphitic + turbostratic)</label>
      <div class="peakrow">
        <label>Graphitic</label>
        <input id="xc1" class="num" type="number" step="0.0001" placeholder="xc (2θ °)">
        <input id="a1"  class="num" type="number" step="0.0001" placeholder="area (A)">
      </div>
      <div class="peakrow" id="row2" style="display:none">
        <label>Turbostratic</label>
        <input id="xc2" class="num" type="number" step="0.0001" placeholder="xc (2θ °)">
        <input id="a2"  class="num" type="number" step="0.0001" placeholder="area (A)">
      </div>
      <div class="hint">λ fixed at Cu Kα 1.54187 Å. Graphitic = higher 2θ peak.
        One peak → DG from its d-spacing; two → area-weighted (Maire-Mering).</div>
      <button id="calc" class="primary">Calculate DG%</button>
    </div>
    <div class="card">
      <h2>Result</h2>
      <div id="out"><div class="placeholder">Enter peak values and calculate.</div></div>
    </div>
  </div>
</section>

<div id="status">Ready.</div>

<script>
const X = {temperature_C:"Temperature (°C)", caco3_ratio:"CaCO₃ ratio", time_h:"Dwell time (h)",
           fe_ratio:"Fe ratio", carbon_ratio:"Carbon ratio"};
const Y = {DG:"DG%", Lc:"Crystallite Lc (Å)", d_prime:"d′ weighted (Å)",
           graphitic_xc:"Graphitic 2θ (°)"};
const G = {carbon_type:"Carbon type", form:"Sample form", wash:"Wash state", none:"(none)"};

const $ = id => document.getElementById(id);
const fileInput=$('file'), fnameEl=$('fname'), statusEl=$('status');
const resultsEl=$('results'), plotWrap=$('plotwrap');
const fileBar=$('fileBar'), fileSel=$('fileSel'), prevBtn=$('prevBtn'), nextBtn=$('nextBtn'), fileInfo=$('fileInfo');
const aiBar=$('aiBar'), aiBtn=$('aiBtn'), aiNote=$('aiNote');
const ySel=$('ySel'), xSel=$('xSel'), gSel=$('gSel'), csvBtn=$('csvBtn');
const chartWrap=$('chartwrap'), filtersEl=$('filters');
const tablewrap=$('tablewrap'), chipsEl=$('chips');
const stackWrap=$('stackwrap'), stkChecks=$('stkChecks'), offset=$('offset'), offVal=$('offVal');
const stkZoom=$('stkZoom'), stkBase=$('stkBase');

let files=[];        // {name, text}
let rows=[];         // batch rows, index-aligned with files
let fitCache={};     // idx -> {data}|{error}  (current theme; cleared on theme switch)
let curFit=0;
let activeTab='analyze';
const theme=()=>document.documentElement.dataset.theme||'dark';

function setStatus(m,e=false){ statusEl.textContent=m; statusEl.className=e?'error':''; }
function readText(f){ return new Promise((res,rej)=>{const r=new FileReader();
  r.onload=e=>res(e.target.result); r.onerror=()=>rej(new Error('read '+f.name)); r.readAsText(f);}); }
function fillSel(sel,map,def){ const cur=sel.value;
  sel.innerHTML=Object.entries(map).map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
  sel.value=(cur && map[cur])?cur:def; }
function debounce(fn,ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; }

// -- tabs -------------------------------------------------------------------
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));
function switchTab(name){
  activeTab=name;
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id==='tab-'+name));
  refreshActive();
}
function refreshActive(){
  if(activeTab==='analyze'){ if(files.length) showFit(curFit); }
  else if(activeTab==='compare'){ if(rows.length) drawCompare(); }
  else if(activeTab==='stack'){ if(files.length) drawStack(); }
}

// -- shared upload ----------------------------------------------------------
fileInput.addEventListener('change', async e=>{
  const fs=Array.from(e.target.files||[]); if(!fs.length) return;
  try{ files=await Promise.all(fs.map(async f=>({name:f.name, text:await readText(f)}))); }
  catch(err){ setStatus('Read error: '+err,true); return; }
  fnameEl.textContent = files.length===1?files[0].name:`${files.length} files selected`;
  fitCache={}; curFit=0;
  await runBatch();
});

async function runBatch(){
  setStatus(`Analyzing ${files.length} file(s)…`);
  try{
    const resp=await fetch('/batch_analyze',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({files:files.map(f=>({name:f.name, xy:f.text}))})});
    const data=await resp.json();
    if(!resp.ok||data.error){ setStatus('Error: '+(data.error||resp.statusText),true); return; }
    rows=data.rows;
    buildFileSel(); buildCompareControls(); buildStackChecks();
    csvBtn.disabled=false;
    const ok=rows.filter(r=>!r.error).length;
    setStatus(`Done — ${ok}/${rows.length} file(s) analyzed.`);
    switchTab('analyze');   // always land on the per-file fit, single or batch
  }catch(err){ setStatus('Request failed: '+err,true); }
}

// -- ANALYZE ----------------------------------------------------------------
function buildFileSel(){
  fileBar.style.display = files.length>1?'flex':'none';
  aiBar.style.display = files.length?'flex':'none';
  fileSel.innerHTML = rows.map((r,i)=>{
    const tag = r.error?'ERROR':(r.DG!=null?`DG ${r.DG.toFixed(2)}%`:'—');
    return `<option value="${i}">${i+1}/${rows.length}  ${r.label||r.file}  —  ${tag}</option>`;
  }).join('');
}
prevBtn.addEventListener('click',()=>showFit(curFit-1));
nextBtn.addEventListener('click',()=>showFit(curFit+1));
aiBtn.addEventListener('click',()=>runAISuggest(curFit));

async function runAISuggest(i){
  if(i<0||i>=files.length) return;
  aiBtn.disabled=true; aiNote.textContent=''; setStatus('Local AI analyzing…');
  try{
    const resp=await fetch('/ai_suggest',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({xy:files[i].text, theme:theme()})});
    const d=await resp.json();
    if(!resp.ok){ setStatus('AI error: '+(d.error||resp.statusText),true); aiBtn.disabled=false; return; }
    const s=d.suggestion||{};
    if(s.amorphous_invalid){ aiNote.textContent='⚠︎ too amorphous for the method — '+(s.rationale||''); setStatus('AI: amorphous'); aiBtn.disabled=false; return; }
    const res=d.result;
    if(!res){ setStatus('AI: '+(d.error||'no fit'),true); aiBtn.disabled=false; return; }
    if(res.plot_png) plotWrap.innerHTML=`<img class="plot" src="${res.plot_png}" alt="AI fit">`;
    renderResultsNetl(res, (rows[i]&&rows[i].label)||files[i].name, files[i].name, s);
    const c=s.confidence||0;
    aiNote.innerHTML=`<b style="color:${c<0.8?'var(--orange)':'var(--green)'}">${(c*100).toFixed(0)}% conf</b>`+
      (c<0.8?' · review suggested':'')+` · ${s.peak_count} peak(s), turbo ${(+s.turbostratic_2theta).toFixed(3)}° · ${s.rationale}`;
    setStatus(`AI done — DG ${res.DG_percent.toFixed(2)}%`);
  }catch(err){ setStatus('AI request failed: '+err,true); }
  finally{ aiBtn.disabled=false; }
}
function renderResultsNetl(d, title, sub, s){
  const pct=x=>(x*100).toFixed(2)+'%';
  let h=fileHead(title,sub)+
    `<div class="dgbox dgtop"><div class="cap">Degree of Graphitization (AI-assisted)</div>`+
    `<div class="dg">${d.DG_percent.toFixed(2)} %</div><div class="cap">${d.method_name}</div></div>`+
    `<div class="section">Graphitic peak</div>`+
    row('2θ centre',d.graphitic.xc.toFixed(4),'°')+row('FWHM',d.graphitic.w.toFixed(4),'°')+
    row('μ',d.graphitic.mu.toFixed(4))+row('Area',d.graphitic.A.toFixed(2))+
    row('d-spacing',d.graphitic.d_spacing_angstrom.toFixed(6),'Å');
  if(d.turbostratic){
    h+=`<div class="section">Turbostratic peak (Lorentzian)</div>`+
       row('2θ centre',d.turbostratic.xc.toFixed(4),'°')+row('FWHM',d.turbostratic.w.toFixed(4),'°')+
       row('Area',d.turbostratic.A.toFixed(2))+row('d-spacing',d.turbostratic.d_spacing_angstrom.toFixed(6),'Å');
  }
  h+=`<div class="section">Result</div>`+
     row('X_g / X_t',pct(d.area_fraction_graphitic)+' / '+pct(d.area_fraction_turbostratic))+
     row("d′ weighted",d.d_spacing_weighted_angstrom.toFixed(6),'Å')+
     row('Crystallite Lc',d.crystallite_height_Lc_angstrom.toFixed(2),'Å (apparent)')+
     row('Baseline y0',d.y0.toFixed(3));
  resultsEl.innerHTML=h;
}
fileSel.addEventListener('change',()=>showFit(parseInt(fileSel.value,10)));

async function showFit(i){
  if(i<0||i>=files.length) return; curFit=i;
  if(files.length>1){ fileSel.value=String(i); prevBtn.disabled=i===0; nextBtn.disabled=i===files.length-1;
    fileInfo.textContent=`File ${i+1} of ${files.length}`; }
  let res=fitCache[i];
  if(!res){
    plotWrap.innerHTML='<span class="placeholder">Rendering…</span>';
    try{
      const resp=await fetch('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({xy:files[i].text, theme:theme()})});
      const d=await resp.json();
      res=(!resp.ok||d.error)?{error:(d.error||resp.statusText)}:{data:d};
    }catch(err){ res={error:String(err)}; }
    fitCache[i]=res;
  }
  const r=rows[i]||{};
  const title=r.label||files[i].name;
  if(res.error){ showError(title, files[i].name, res.error); return; }
  renderResults(res.data, title, files[i].name);
  plotWrap.innerHTML = res.data.plot_png?`<img class="plot" src="${res.data.plot_png}" alt="fit plot">`
                                        :'<span class="placeholder">No plot.</span>';
}
function fileHead(title,sub){
  return `<div class="filetitle">${title}</div>`+
    (sub && sub!==title ? `<div class="filesub">${sub}</div>` : '');
}
function showError(title,sub,msg){
  resultsEl.innerHTML=fileHead(title,sub)+
    `<div class="placeholder" style="color:var(--red)">Analysis failed:<br><br>${msg}</div>`;
  plotWrap.innerHTML='<span class="placeholder">No plot — analysis failed.</span>';
}
function row(l,v,u=''){ return `<div class="row"><span>${l}</span><span><span class="val">${v}</span>`+
  (u?`<span class="unit">${u}</span>`:'')+`</span></div>`; }
function peakRows(p){ return row('  xc (2θ)',p.xc.toFixed(4),'°')+row('  w (FWHM)',p.w.toFixed(4),'°')+
  row('  μ',p.mu.toFixed(4))+row('  A (area)',p.A.toFixed(4))+row('  d-spacing',p.d_spacing_angstrom.toFixed(6),'Å'); }
function renderResults(d, title, sub){
  const pct=x=>(x*100).toFixed(2)+'%';
  let h=(title?fileHead(title,sub):'')+
    `<div class="dgbox dgtop"><div class="cap">Degree of Graphitization</div>`+
    `<div class="dg">${d.DG_percent.toFixed(2)} %</div><div class="cap">Maire-Mering equation</div></div>`+
    `<div class="section">${d.method_name}</div>`+
    row('Wavelength λ',d.wavelength_angstrom.toFixed(5),'Å')+
    `<div class="section">Graphitic peak</div>`+peakRows(d.graphitic)+
    `<div class="section">Turbostratic peak (Lorentzian)</div>`+peakRows(d.turbostratic)+
    `<div class="section">Result</div>`+
    row('X_g / X_t',pct(d.area_fraction_graphitic)+' / '+pct(d.area_fraction_turbostratic))+
    row("d′ weighted",d.d_spacing_weighted_angstrom.toFixed(6),'Å')+
    row('Crystallite Lc',d.crystallite_height_Lc_angstrom.toFixed(2),'Å')+
    row('fit R²',d.fit_r2.toFixed(5));
  resultsEl.innerHTML=h;
}

// -- COMPARE ----------------------------------------------------------------
const FILTER_FIELDS = {carbon_type:'Type', form:'Form', wash:'Wash'};
function buildCompareControls(){
  fillSel(ySel,Y,'DG'); fillSel(xSel,X,'temperature_C'); fillSel(gSel,G,'carbon_type');
  buildFilters(); renderTable(); renderChips(); syncPills();
}
[ySel,xSel,gSel].forEach(s=>s.addEventListener('change',drawCompare));

// reusable checkbox-row item (still used by the Stack tab)
function ckItem(group,val,label,checked){
  return `<label class="ck" title="${label}"><input type="checkbox" data-g="${group}" value="${val}"`+
         (checked?' checked':'')+`>${label}</label>`;
}

// category filters — a toggle pill per distinct value of each categorical field
function buildFilters(){
  let html='';
  for(const [f,lbl] of Object.entries(FILTER_FIELDS)){
    const vals=[...new Set(rows.map(r=>r[f]).filter(v=>v!==null&&v!==undefined&&v!==''))].sort();
    if(vals.length<2) continue;                       // nothing to filter on
    html+=`<div class="filtergrp"><span class="filterlbl">${lbl}</span>`+
      vals.map(v=>`<button class="filterpill active" data-f="${f}" data-v="${v}">${v}</button>`).join('')+`</div>`;
  }
  filtersEl.innerHTML = html || '<span class="muted">No categorical fields to filter.</span>';
}
// Pills and the table checkboxes are ONE control: a pill bulk-toggles every row
// of that value, and pill state mirrors whether any such row is still checked.
function rowBox(i){ return tablewrap.querySelector(`.rowck[data-i="${i}"]`); }
filtersEl.addEventListener('click', e=>{ const b=e.target.closest('.filterpill');
  if(!b) return;
  const want=!b.classList.contains('active'), f=b.dataset.f, v=b.dataset.v;
  rows.forEach((r,i)=>{ if(String(r[f]??'')===v){ const c=rowBox(i); if(c) c.checked=want; } });
  syncPills(); drawCompare();
});
function syncPills(){
  filtersEl.querySelectorAll('.filterpill').forEach(b=>{
    const f=b.dataset.f, v=b.dataset.v;
    const anyOn=rows.some((r,i)=>{ const c=rowBox(i); return c && c.checked && String(r[f]??'')===v; });
    b.classList.toggle('active', anyOn);
  });
  const all=tablewrap.querySelector('#rowAll'), boxes=[...tablewrap.querySelectorAll('.rowck')];
  if(all) all.checked = boxes.length>0 && boxes.every(c=>c.checked);
}
function effectiveRows(){
  const on=new Set([...tablewrap.querySelectorAll('.rowck:checked')].map(c=>c.dataset.i));
  return rows.filter((r,i)=> on.has(String(i)));
}
async function drawCompare(){
  if(!rows.length) return;
  chartWrap.innerHTML='<div class="placeholder">Rendering…</div>';
  try{
    const resp=await fetch('/chart',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({rows:effectiveRows(), x:xSel.value, y:ySel.value, group:gSel.value, theme:theme()})});
    const d=await resp.json();
    chartWrap.innerHTML = d.chart_png?`<img class="chartimg" src="${d.chart_png}" alt="comparison chart">`
                                     :`<div class="placeholder">${d.error||'no chart'}</div>`;
  }catch(err){ chartWrap.innerHTML=`<div class="placeholder">Request failed: ${err}</div>`; }
}

// the table itself is the per-run selector: tick to include, click a name → its fit
function tcell(v,dig){ if(v===null||v===undefined||v==='') return '<td class="miss">–</td>';
  return `<td>${typeof v==='number'?v.toFixed(dig):v}</td>`; }
function renderTable(){
  const head=['Type','C','Fe','CaCO₃','T(°C)','t(h)','Form','Wash','DG%','Lc(Å)'];
  let h='<table><thead><tr><th class="ck-col"><input type="checkbox" id="rowAll" checked></th>'+
        '<th class="lbl">Run</th>'+head.map(x=>`<th>${x}</th>`).join('')+'</tr></thead><tbody>';
  rows.forEach((r,i)=>{
    const cls=r.error?' class="err"':'';
    h+=`<tr${cls}><td class="ck-col"><input type="checkbox" class="rowck" data-i="${i}" checked></td>`+
       `<td class="lbl"><a class="runlink" data-i="${i}" title="${r.file}">${r.label||r.file}</a></td>`+
       tcell(r.carbon_type)+tcell(r.carbon_ratio,0)+tcell(r.fe_ratio,0)+tcell(r.caco3_ratio,4)+
       tcell(r.temperature_C,0)+tcell(r.time_h,0)+tcell(r.form)+tcell(r.wash)+
       tcell(r.DG,2)+tcell(r.Lc,1)+'</tr>';
  });
  tablewrap.innerHTML=h+'</tbody></table>';
}
tablewrap.addEventListener('change', e=>{
  if(e.target.id==='rowAll'){ tablewrap.querySelectorAll('.rowck').forEach(c=>c.checked=e.target.checked); }
  else if(!e.target.classList.contains('rowck')) return;
  syncPills(); drawCompare();
});
tablewrap.addEventListener('click', e=>{ const a=e.target.closest('.runlink');
  if(a){ e.preventDefault(); openRun(parseInt(a.dataset.i,10)); } });
function openRun(i){ curFit=i; switchTab('analyze'); }   // refreshActive renders showFit(curFit)

function renderChips(){
  const count=k=>{ const m={}; rows.forEach(r=>{const v=r[k]??'—'; m[v]=(m[v]||0)+1;});
    return Object.entries(m).map(([v,n])=>`${v}:${n}`).join('  '); };
  chipsEl.innerHTML=[`${rows.length} runs`,'Carbon — '+count('carbon_type'),
    'Form — '+count('form'),'Temp — '+count('temperature_C')]
    .map(t=>`<span class="chip">${t}</span>`).join('');
}

csvBtn.addEventListener('click',downloadCSV);
function downloadCSV(){
  if(!rows.length) return;
  const cols=['file','carbon_type','carbon_ratio','fe_ratio','caco3_ratio',
    'temperature_C','time_h','form','wash','date','DG','Lc',
    'd_prime','graphitic_xc','turbostratic_xc','error'];
  const esc=v=>{ if(v===null||v===undefined) return '';
    const s=String(v); return /[",\\n]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s; };
  const lines=[cols.join(',')];
  for(const r of rows) lines.push(cols.map(c=>esc(r[c])).join(','));
  const blob=new Blob([lines.join('\\n')],{type:'text/csv'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download='xrd_runs.csv';
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
}

// -- STACK ------------------------------------------------------------------
function buildStackChecks(){
  // default-check the first 8 to avoid an unreadable overlay of huge batches
  stkChecks.innerHTML=files.map((f,i)=>ckItem('stk',i,(rows[i]&&rows[i].label)||f.name, i<8)).join('');
}
stkChecks.addEventListener('change',drawStack);
$('stkAll').onclick =()=>setStk(true);
$('stkNone').onclick=()=>setStk(false);
function setStk(on){ stkChecks.querySelectorAll('input').forEach(c=>c.checked=on); drawStack(); }
offset.addEventListener('input',()=>{ offVal.textContent=parseFloat(offset.value).toFixed(2); drawStackDebounced(); });
stkZoom.addEventListener('change',drawStack);
stkBase.addEventListener('change',drawStack);
const drawStackDebounced=debounce(()=>drawStack(),220);

async function drawStack(){
  if(!files.length) return;
  const sel=[]; stkChecks.querySelectorAll('input:checked').forEach(c=>sel.push(parseInt(c.value,10)));
  if(!sel.length){ stackWrap.innerHTML='<div class="placeholder">Check files to stack.</div>'; return; }
  stackWrap.innerHTML='<div class="placeholder">Rendering…</div>';
  try{
    const resp=await fetch('/stack',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({files:sel.map(i=>({name:(rows[i]&&rows[i].label)||files[i].name, xy:files[i].text})),
        offset:parseFloat(offset.value), zoom:stkZoom.checked, baseline:stkBase.checked, theme:theme()})});
    const d=await resp.json();
    stackWrap.innerHTML = d.stack_png?`<img class="chartimg" src="${d.stack_png}" alt="stacked spectra">`
                                     :`<div class="placeholder">${d.error||'no plot'}</div>`;
  }catch(err){ stackWrap.innerHTML=`<div class="placeholder">Request failed: ${err}</div>`; }
}

// -- MANUAL -----------------------------------------------------------------
const two=$('two'), row2=$('row2'), out=$('out');
two.addEventListener('change',()=>{ row2.style.display=two.checked?'flex':'none'; });
function mrow(l,v,u=''){ return `<div class="row"><span>${l}</span><span class="val">${v}${u?' '+u:''}</span></div>`; }
$('calc').addEventListener('click', async ()=>{
  const peaks=[{xc:parseFloat($('xc1').value), area:parseFloat($('a1').value)}];
  if(two.checked) peaks.push({xc:parseFloat($('xc2').value), area:parseFloat($('a2').value)});
  for(const p of peaks){ if(!isFinite(p.xc)||!isFinite(p.area)){ setStatus('Enter numeric xc and area for each peak.',true); return; } }
  setStatus('Calculating…');
  try{
    const r=await fetch('/calc_peaks',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({peaks})});
    const d=await r.json();
    if(!r.ok||d.error){ setStatus('Error: '+(d.error||r.statusText),true); return; }
    let h=`<div class="section">${d.method_name}</div>`+
      mrow('λ',d.wavelength_angstrom.toFixed(5),'Å')+
      `<div class="section">Graphitic</div>`+
      mrow('xc',d.graphitic.xc.toFixed(4),'°')+mrow('area',d.graphitic.A.toFixed(4))+
      mrow('d-spacing',d.graphitic.d_spacing_angstrom.toFixed(6),'Å');
    if(d.n_peaks===2){
      h+=`<div class="section">Turbostratic</div>`+
         mrow('xc',d.turbostratic.xc.toFixed(4),'°')+mrow('area',d.turbostratic.A.toFixed(4))+
         mrow('d-spacing',d.turbostratic.d_spacing_angstrom.toFixed(6),'Å')+
         `<div class="section">Weighted</div>`+
         mrow('X_g / X_t',(d.area_fraction_graphitic*100).toFixed(2)+'% / '+(d.area_fraction_turbostratic*100).toFixed(2)+'%')+
         mrow("d′",d.d_spacing_weighted_angstrom.toFixed(6),'Å');
    }
    h+=`<div class="dgbox"><div class="cap">Degree of Graphitization</div>`+
       `<div class="dg">${d.DG_percent.toFixed(2)} %</div>`+
       `<div class="cap">${d.n_peaks===1?'single peak':'area-weighted'} · Maire-Mering</div></div>`;
    out.innerHTML=h; setStatus('Done.');
  }catch(err){ setStatus('Request failed: '+err,true); }
});

// -- theme re-render --------------------------------------------------------
window.onThemeChange=()=>{ fitCache={}; refreshActive(); };
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT          # per-socket-op timeout (slowloris guard)

    def log_message(self, fmt, *args):  # silence per-request logging
        pass

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Never serve a stale page/JS (so image updates take effect on reload)
        self.send_header("Cache-Control", "no-store, must-revalidate")
        # Lightweight hardening headers
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, code: int, msg: str) -> None:
        self.close_connection = True
        self._send(code, "application/json", json.dumps({"error": msg}).encode("utf-8"))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith(("/analyze", "/batch_analyze", "/chart", "/stack", "/calc_peaks", "/ai_suggest")):
            self._send(405, "text/plain; charset=utf-8", b"Use POST for this endpoint")
        else:
            # one single-page app; /dashboard and /manual kept as aliases
            self._send(200, "text/html; charset=utf-8", PAGE_HTML.encode("utf-8"))

    def do_POST(self) -> None:
        path = self.path.rstrip("/")

        # 1) Body-size guard — reject before reading a huge body into memory.
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY_BYTES:
            self._json_error(413, f"request too large (limit {MAX_UPLOAD_MB:.0f} MB)")
            return

        # 2) Concurrency cap — bound simultaneous CPU/RAM-heavy fitting/plotting.
        if not _work_sem.acquire(timeout=BUSY_WAIT_SEC):
            self._json_error(429, "server busy — too many analyses in progress, retry shortly")
            return
        try:
            if path.endswith("/batch_analyze"):
                self._handle_batch()
            elif path.endswith("/chart"):
                self._handle_chart()
            elif path.endswith("/stack"):
                self._handle_stack()
            elif path.endswith("/calc_peaks"):
                self._handle_calc_peaks()
            elif path.endswith("/ai_suggest"):
                self._handle_ai_suggest()
            else:
                self._handle_analyze()
        except ValueError as exc:
            self._send(400, "application/json", json.dumps({"error": str(exc)}).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(500, "application/json",
                       json.dumps({"error": f"unexpected error — {exc}"}).encode("utf-8"))
        finally:
            _work_sem.release()

    # -- endpoints -----------------------------------------------------------

    def _handle_analyze(self) -> None:
        payload = self._read_json()
        pattern = XRDPattern.from_text(payload.get("xy", ""))
        try:
            res = GraphitizationAnalyzer(pattern).run()
            res["plot_png"] = render_plot(pattern, res, payload.get("theme", "dark"))
        except (FitError, ValueError) as exc:
            res = {"error": str(exc)}
        self._send(200, "application/json", json.dumps(res).encode("utf-8"))

    def _handle_calc_peaks(self) -> None:
        payload = self._read_json()
        result = dg_from_peaks(payload.get("peaks", []))   # ValueError → 400
        self._send(200, "application/json", json.dumps(result).encode("utf-8"))

    def _handle_ai_suggest(self) -> None:
        """AI-assisted deconvolution: features → local Ollama model → NETL fit."""
        import ai_suggest  # lazy: only needed when the AI button is used
        payload = self._read_json()
        pattern = XRDPattern.from_text(payload.get("xy", ""))
        feats = ai_suggest.compute_features(pattern.two_theta, pattern.intensity)
        try:
            dec = ai_suggest.suggest(feats, payload.get("model"))
        except Exception as exc:  # noqa: BLE001 — surface provider/credential errors cleanly
            self._send(502, "application/json",
                       json.dumps({"error": f"AI provider error — {exc}"}).encode("utf-8"))
            return
        out: dict = {"suggestion": dec, "features": feats,
                     "confidence": dec.get("confidence"), "rationale": dec.get("rationale")}
        if not dec.get("amorphous_invalid"):
            try:
                res = fit_netl(pattern.two_theta, pattern.intensity,
                               peak_count=int(dec.get("peak_count", 2)),
                               turbostratic_center=dec.get("turbostratic_2theta"),
                               lock_turbostratic=int(dec.get("peak_count", 2)) == 2,
                               subtract_background=bool(dec.get("subtract_background")))
                res["plot_png"] = render_plot_netl(pattern, res, payload.get("theme", "dark"))
                out["result"] = res
            except (FitError, ValueError) as exc:
                out["error"] = str(exc)
        self._send(200, "application/json", json.dumps(out).encode("utf-8"))

    def _handle_batch(self) -> None:
        payload = self._read_json()
        files = payload.get("files", [])
        if len(files) > MAX_BATCH_FILES:
            raise ValueError(
                f"too many files in one batch ({len(files)}); limit is {MAX_BATCH_FILES} — "
                "upload fewer at a time")
        rows = build_dashboard_rows(files)
        self._send(200, "application/json", json.dumps({"rows": rows}).encode("utf-8"))

    def _handle_chart(self) -> None:
        payload = self._read_json()
        png = render_dashboard_chart(payload.get("rows", []),
                                     payload.get("x", "temperature_C"),
                                     payload.get("y", "DG"),
                                     payload.get("group", "carbon_type"),
                                     payload.get("theme", "dark"))
        self._send(200, "application/json", json.dumps({"chart_png": png}).encode("utf-8"))

    def _handle_stack(self) -> None:
        payload = self._read_json()
        files = payload.get("files", [])
        if len(files) > MAX_BATCH_FILES:
            raise ValueError(
                f"too many files to stack ({len(files)}); limit is {MAX_BATCH_FILES}")
        window = (24.0, 30.0) if payload.get("zoom") else None
        png = render_stack(files,
                           float(payload.get("offset", 0.25)),
                           payload.get("theme", "dark"),
                           bool(payload.get("baseline", False)),
                           window)
        self._send(200, "application/json", json.dumps({"stack_png": png}).encode("utf-8"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Cloud hosts inject $PORT and expect 0.0.0.0; locally use 127.0.0.1:8000.
    env_port = os.environ.get("PORT")
    default_port = int(env_port) if env_port else 8000
    default_host = "0.0.0.0" if env_port else "127.0.0.1"
    is_cloud = bool(env_port)

    parser = argparse.ArgumentParser(
        prog="xrd_webgui",
        description="Local browser GUI for the XRD Graphitization Analyzer.",
    )
    parser.add_argument("--port", type=int, default=default_port,
                        help=f"Port (default {default_port}; honours $PORT).")
    parser.add_argument("--host", default=default_host,
                        help=f"Bind host (default {default_host}).")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the default web browser.")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"XRD Graphitization Analyzer — serving at {url}", flush=True)
    print(f"Limits: upload ≤ {MAX_UPLOAD_MB:.0f} MB/request, ≤ {MAX_BATCH_FILES} files/batch, "
          f"{MAX_CONCURRENT} concurrent analyses.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    if not args.no_browser and not is_cloud:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
