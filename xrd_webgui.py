"""
xrd_webgui.py — Browser GUI for the XRD Graphitization Analyzer.

Self-contained local web app (stdlib http.server + headless matplotlib). For
each uploaded .xy file it computes BOTH calculation methods (A = NETL paper
standard; B = OriginLab PsdVoigt1 single-vs-dual) and returns results + fit
plots. A dropdown switches the displayed method; multiple files are paged.

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
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

# Headless backend — set before importing pyplot/Figure
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

from xrd_analyzer import (
    ANALYSIS_WINDOW,
    FitError,
    GraphitizationAnalyzer,
    XRDPattern,
    pseudo_voigt,
)
from run_parser import parse_run_filename

# ---------------------------------------------------------------------------
# Plot colours (dark theme)
# ---------------------------------------------------------------------------
PANEL     = "#2a2a3e"
RED_PEAK  = "#f7768e"
BLUE_PEAK = "#7dcfff"
FIT_COL   = "#bb9af7"
SINGLE_COL = "#e0af68"
RAW_COL   = "#a9b1d6"
MUTED     = "#565f89"
TEXT      = "#c0caf5"


# ---------------------------------------------------------------------------
# Plot rendering — one PNG per method
# ---------------------------------------------------------------------------

def render_plot(pattern: XRDPattern, res: dict) -> str:
    """Render the fit for a method's result to a base64 PNG data-URI."""
    fig = Figure(figsize=(6.4, 4.6), dpi=200, facecolor=PANEL)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#12121e")

    g, t = res["graphitic"], res["turbostratic"]

    if res["method"] == "A":
        x, y = pattern.window(*ANALYSIS_WINDOW)
        xp = np.linspace(x.min(), x.max(), 600)
        y0 = res["baseline_y0"]
        yg = pseudo_voigt(xp, g["A"], g["xc"], g["w"], g["mu"]) + y0
        yt = pseudo_voigt(xp, t["A"], t["xc"], t["w"], t["mu"]) + y0
        ytot = yg + yt - y0
        ax.scatter(x, y, s=8, color=RAW_COL, alpha=0.7, label="Raw data", zorder=2)
        ax.axhline(y0, color=MUTED, lw=0.8, ls=":", label=f"baseline y0={y0:.2f}")
        ax.fill_between(xp, yg, y0, alpha=0.25, color=RED_PEAK)
        ax.plot(xp, yg, color=RED_PEAK, lw=1.5, label=f"Graphitic 2θ={g['xc']:.3f}°")
        ax.fill_between(xp, yt, y0, alpha=0.18, color=BLUE_PEAK)
        ax.plot(xp, yt, color=BLUE_PEAK, lw=1.5, label=f"Turbostratic 2θ={t['xc']:.3f}°")
        ax.plot(xp, ytot, color=FIT_COL, lw=2.0, ls="--", label="Total fit", zorder=5)
        title = "Method A — NETL bimodal Pseudo-Voigt"
    else:  # B
        x, y, _bl = pattern.baseline_subtracted(*ANALYSIS_WINDOW)
        xp = np.linspace(x.min(), x.max(), 600)
        s = res["single_peak"]
        yg = pseudo_voigt(xp, g["A"], g["xc"], g["w"], g["mu"])
        yt = pseudo_voigt(xp, t["A"], t["xc"], t["w"], t["mu"])
        ydual = yg + yt
        ysingle = pseudo_voigt(xp, s["A"], s["xc"], s["w"], s["mu"])
        ax.scatter(x, y, s=8, color=RAW_COL, alpha=0.7, label="Baseline-subtracted", zorder=2)
        ax.fill_between(xp, yg, alpha=0.25, color=RED_PEAK)
        ax.plot(xp, yg, color=RED_PEAK, lw=1.4, label=f"Graphitic 2θ={g['xc']:.3f}°")
        ax.fill_between(xp, yt, alpha=0.18, color=BLUE_PEAK)
        ax.plot(xp, yt, color=BLUE_PEAK, lw=1.4, label=f"Turbostratic 2θ={t['xc']:.3f}°")
        ax.plot(xp, ydual, color=FIT_COL, lw=2.0, ls="--", label="Dual fit (NETL)", zorder=5)
        ax.plot(xp, ysingle, color=SINGLE_COL, lw=1.3, ls=":",
                label="Single fit (legacy)", zorder=4)
        title = "Method B — OriginLab single (legacy) vs dual (NETL)"

    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(MUTED)
    ax.set_xlabel("2θ  (degrees)", color=MUTED, fontsize=9)
    ax.set_ylabel("Intensity  (a.u.)", color=MUTED, fontsize=9)
    ax.set_title(title, color=TEXT, fontsize=9, pad=8)
    ax.legend(fontsize=7.5, facecolor=PANEL, edgecolor=MUTED, labelcolor=TEXT, framealpha=0.9)
    fig.tight_layout(pad=1.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=PANEL, dpi=200)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# Dashboard — parse run parameters, analyse, build dataset & comparison charts
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
    "DG_B":             "DG% — NETL dual (Method B)",
    "DG_A":             "DG% — Method A",
    "Lc":               "Crystallite height Lc (Å)",
    "dg_overestimation": "DG% overestimation (legacy − NETL)",
    "d_prime_B":        "d′ weighted (Å)",
}
GROUP_LABELS = {
    "carbon_type": "Carbon type",
    "form":        "Sample form",
    "wash":        "Wash state",
    "none":        "(none)",
}
# Distinct colours for grouped series
_SERIES_COLORS = ["#7aa2f7", "#f7768e", "#9ece6a", "#e0af68", "#bb9af7",
                  "#7dcfff", "#ff9e64", "#9d7cd8"]


def build_dashboard_rows(files: list[dict]) -> list[dict]:
    """
    For each {name, xy} entry: parse run parameters from the name and analyse
    the pattern with both methods. Returns one flat row per file.
    """
    rows: list[dict] = []
    for f in files:
        name = f.get("name", "")
        row = parse_run_filename(name)
        row["file"] = name
        # defaults
        for k in ("DG_A", "DG_B", "Lc", "dg_overestimation", "d_prime_B", "graphitic_xc_B"):
            row[k] = None
        try:
            analyzer = GraphitizationAnalyzer(XRDPattern.from_text(f.get("xy", "")))
            try:
                row["DG_A"] = analyzer.run("A")["DG_percent"]
            except (FitError, ValueError):
                pass
            try:
                b = analyzer.run("B")
                row["DG_B"] = b["DG_percent"]
                row["Lc"] = b["crystallite_height_Lc_angstrom"]
                row["dg_overestimation"] = b["dg_overestimation_percent"]
                row["d_prime_B"] = b["d_spacing_weighted_angstrom"]
                row["graphitic_xc_B"] = b["graphitic"]["xc"]
            except (FitError, ValueError):
                pass
        except ValueError as exc:
            row["error"] = str(exc)
        if row.get("DG_A") is None and row.get("DG_B") is None and "error" not in row:
            row["error"] = "fit failed (possible high amorphous content)"
        rows.append(row)
    return rows


def render_dashboard_chart(rows: list[dict], x: str, y: str, group: str) -> str:
    """Scatter/line chart of metric ``y`` vs parameter ``x``, grouped by ``group``."""
    if x not in X_LABELS:
        x = "temperature_C"
    if y not in Y_LABELS:
        y = "DG_B"
    if group not in GROUP_LABELS:
        group = "carbon_type"

    fig = Figure(figsize=(6.6, 4.6), dpi=200, facecolor=PANEL)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#12121e")

    # bucket points by group value
    series: dict[str, list[tuple]] = {}
    for r in rows:
        xv, yv = r.get(x), r.get(y)
        if xv is None or yv is None:
            continue
        gv = "all" if group == "none" else (r.get(group) or "unspecified")
        series.setdefault(str(gv), []).append((float(xv), float(yv)))

    if not series:
        ax.text(0.5, 0.5, "No data for this combination", transform=ax.transAxes,
                ha="center", va="center", color=MUTED, fontsize=11)
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
                ax.plot(mx, my, "-", color=color, lw=1.3, alpha=0.45, zorder=2)
            ax.scatter(xs, ys, s=46, color=color, edgecolor="#12121e",
                       linewidth=0.6, zorder=3,
                       label=(gv if group != "none" else None))
        if group != "none":
            ax.legend(title=GROUP_LABELS[group], fontsize=8, title_fontsize=8.5,
                      facecolor=PANEL, edgecolor=MUTED, labelcolor=TEXT, framealpha=0.9)

    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(MUTED)
    ax.grid(True, color="#23233a", lw=0.6)
    ax.set_xlabel(X_LABELS[x], color=MUTED, fontsize=9)
    ax.set_ylabel(Y_LABELS[y], color=MUTED, fontsize=9)
    ax.set_title(f"{Y_LABELS[y]}  vs  {X_LABELS[x]}", color=TEXT, fontsize=9.5, pad=8)
    fig.tight_layout(pad=1.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=PANEL, dpi=200)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XRD Graphitization Analyzer</title>
<style>
  :root {
    --bg:#1e1e2e; --panel:#2a2a3e; --accent:#7aa2f7; --green:#9ece6a;
    --amber:#e0af68; --text:#c0caf5; --muted:#565f89; --btn:#364a82; --btnact:#4a6296;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:-apple-system,Helvetica,Arial,sans-serif; }
  header { background:var(--panel); padding:14px 22px; display:flex;
           align-items:center; gap:14px; flex-wrap:wrap; border-bottom:1px solid #12121e; }
  header h1 { font-size:18px; color:var(--accent); margin:0; }
  .controls { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  button, .filebtn {
    background:var(--btn); color:var(--text); border:none; border-radius:6px;
    padding:8px 16px; font-size:13px; cursor:pointer; transition:background .15s;
  }
  button:hover, .filebtn:hover { background:var(--btnact); }
  button.primary { background:var(--accent); color:var(--bg); font-weight:600; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  #fname { color:var(--muted); font-size:13px; font-family:monospace; }
  select { background:var(--btn); color:var(--text); border:none;
           border-radius:6px; padding:8px 10px; font-size:13px; }
  .navlink { color:var(--amber); text-decoration:none; font-size:13px; font-weight:600;
             padding:6px 12px; border:1px solid var(--amber); border-radius:6px; }
  .navlink:hover { background:var(--amber); color:var(--bg); }
  main { display:grid; grid-template-columns:380px 1fr; gap:16px; padding:16px; }
  @media (max-width:860px){ main{ grid-template-columns:1fr; } }
  .card { background:var(--panel); border-radius:10px; padding:18px; }
  .section { color:var(--accent); font-size:13px; font-weight:600;
             margin:16px 0 6px; border-bottom:1px solid var(--muted); padding-bottom:4px; }
  .section:first-child { margin-top:0; }
  .row { display:flex; justify-content:space-between; align-items:baseline;
         padding:3px 0; font-size:13px; }
  .row .lbl { color:var(--text); }
  .row .val { color:var(--green); font-family:monospace; font-weight:600; }
  .row .unit { color:var(--muted); font-size:11px; margin-left:4px; }
  .dgbox { margin-top:20px; background:#2d2038; border-radius:10px; padding:18px; text-align:center; }
  .dgbox .cap { color:var(--muted); font-size:12px; }
  .dgbox .dg { color:var(--amber); font-size:38px; font-weight:700; margin:6px 0; }
  #plot { width:100%; border-radius:8px; display:block; }
  #plotwrap { display:flex; align-items:center; justify-content:center; min-height:360px; color:var(--muted); }
  #status { padding:8px 22px; background:#12121e; color:var(--muted); font-size:12px; }
  #status.error { color:#f7768e; }
  .placeholder { color:var(--muted); text-align:center; }
  #pager { display:none; align-items:center; gap:10px; padding:8px 22px;
           background:var(--panel); border-bottom:1px solid #12121e; font-size:13px; }
  #pager button { padding:6px 12px; }
  #pager button:disabled { opacity:.4; cursor:not-allowed; }
  #pagerInfo { color:var(--muted); }
  #fileSel { max-width:420px; }
  #fileSel option.err { color:#f7768e; }
</style>
</head>
<body>
<header>
  <h1>XRD Graphitization Analyzer</h1>
  <a class="navlink" href="/dashboard">Run Dashboard →</a>
  <div class="controls">
    <label class="filebtn">Choose .xy file(s)…
      <input id="file" type="file" accept=".xy,.txt,.dat,text/plain" multiple style="display:none">
    </label>
    <span id="fname">no file selected</span>
    <select id="method" title="Which method's results to view (both are computed)">
      <option value="A">View: Method A (NETL paper)</option>
      <option value="B">View: Method B (OriginLab single/dual)</option>
    </select>
    <button id="analyze" class="primary" disabled>Analyze</button>
  </div>
</header>

<div id="pager">
  <button id="prevBtn">◀ Prev</button>
  <select id="fileSel" title="Jump to a file"></select>
  <button id="nextBtn">Next ▶</button>
  <span id="pagerInfo"></span>
</div>

<main>
  <div class="card" id="results">
    <div class="placeholder">Choose file(s) and click Analyze. Both methods are computed.</div>
  </div>
  <div class="card">
    <div id="plotwrap"><span class="placeholder">Plot appears here after analysis.</span></div>
  </div>
</main>

<div id="status">Ready.</div>

<script>
const fileInput = document.getElementById('file');
const methodSel = document.getElementById('method');
const fnameEl   = document.getElementById('fname');
const analyzeBtn= document.getElementById('analyze');
const statusEl  = document.getElementById('status');
const resultsEl = document.getElementById('results');
const plotWrap  = document.getElementById('plotwrap');
const pagerEl   = document.getElementById('pager');
const prevBtn   = document.getElementById('prevBtn');
const nextBtn   = document.getElementById('nextBtn');
const fileSel   = document.getElementById('fileSel');
const pagerInfo = document.getElementById('pagerInfo');

let xyFiles = [];   // [{name, text}]
let batch   = [];   // [{name, A, B}] each method = result | {error}
let current = 0;

function setStatus(msg, isError=false){ statusEl.textContent = msg; statusEl.className = isError?'error':''; }
function readText(f){ return new Promise((res,rej)=>{ const r=new FileReader();
  r.onload=e=>res(e.target.result); r.onerror=()=>rej(new Error('read failed: '+f.name)); r.readAsText(f); }); }

fileInput.addEventListener('change', async e => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  try { xyFiles = await Promise.all(files.map(async f => ({name:f.name, text:await readText(f)}))); }
  catch (err) { setStatus('Could not read .xy file(s): '+err, true); return; }
  fnameEl.textContent = xyFiles.length===1 ? xyFiles[0].name : `${xyFiles.length} files selected`;
  analyzeBtn.disabled = false;
  setStatus(`${xyFiles.length} file(s) loaded — click Analyze.`);
});

analyzeBtn.addEventListener('click', runAnalysis);
methodSel.addEventListener('change', () => showResult(current));
prevBtn.addEventListener('click', () => showResult(current-1));
nextBtn.addEventListener('click', () => showResult(current+1));
fileSel.addEventListener('change', () => showResult(parseInt(fileSel.value,10)));

async function analyzeOne(file){
  try {
    const resp = await fetch('/analyze', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({xy:file.text}),
    });
    const data = await resp.json();
    if (!resp.ok || data.error) return {name:file.name, error:(data.error||resp.statusText)};
    return {name:file.name, A:data.A, B:data.B};
  } catch (err) { return {name:file.name, error:String(err)}; }
}

async function runAnalysis(){
  if (!xyFiles.length){ setStatus('No .xy file selected.', true); return; }
  analyzeBtn.disabled = true; analyzeBtn.textContent = 'Working…';
  batch = [];
  for (let i=0;i<xyFiles.length;i++){
    setStatus(`Analyzing ${i+1}/${xyFiles.length}: ${xyFiles[i].name}… (both methods)`);
    batch.push(await analyzeOne(xyFiles[i]));
  }
  current = 0; renderPager(); showResult(0);
  const ok = batch.filter(b=>!b.error).length;
  if (batch.length===1 && ok===1){
    const e=batch[0];
    const a = e.A && !e.A.error ? e.A.DG_percent.toFixed(2)+'%' : 'fail';
    const b = e.B && !e.B.error ? e.B.DG_percent.toFixed(2)+'%' : 'fail';
    setStatus(`Done — ${e.name}   |   A: DG ${a}   |   B: DG ${b}`);
  } else setStatus(`Done — ${ok}/${batch.length} file(s) analyzed (both methods).`);
  analyzeBtn.disabled = false; analyzeBtn.textContent = 'Analyze';
}

function renderPager(){
  if (batch.length<=1){ pagerEl.style.display='none'; return; }
  pagerEl.style.display='flex';
  fileSel.innerHTML = batch.map((b,i)=>{
    let tag='ERROR', cls=' class="err"';
    if (!b.error){ const m=methodSel.value; const r=b[m];
      tag = (r && !r.error) ? `DG ${r.DG_percent.toFixed(2)}%` : 'fit fail'; cls = (r && !r.error)?'':' class="err"'; }
    return `<option value="${i}"${cls}>${i+1}/${batch.length}  ${b.name}  —  ${tag}</option>`;
  }).join('');
}

function showResult(i){
  if (i<0 || i>=batch.length) return;
  current = i;
  if (batch.length>1){
    fileSel.value=String(i); pagerInfo.textContent=`File ${i+1} of ${batch.length}`;
    prevBtn.disabled=(i===0); nextBtn.disabled=(i===batch.length-1);
  }
  const entry = batch[i];
  if (entry.error){ showError(entry.name, entry.error); return; }
  const res = entry[methodSel.value];
  if (!res || res.error){ showError(entry.name, (res && res.error) || 'no result'); return; }
  renderResults(res);
  if (res.plot_png) plotWrap.innerHTML = `<img id="plot" src="${res.plot_png}" alt="fit plot">`;
  else plotWrap.innerHTML = `<span class="placeholder">No plot.</span>`;
}

function showError(name, msg){
  resultsEl.innerHTML = `<div class="section">${name}</div>` +
    `<div class="placeholder" style="color:#f7768e">Analysis failed:<br><br>${msg}</div>`;
  plotWrap.innerHTML = `<span class="placeholder">No plot — analysis failed.</span>`;
}

function row(lbl,val,unit=''){ return `<div class="row"><span class="lbl">${lbl}</span>`+
  `<span><span class="val">${val}</span>`+(unit?`<span class="unit">${unit}</span>`:'')+`</span></div>`; }
function peakRows(p){ return row('  xc (2θ)', p.xc.toFixed(4),'°')+row('  w (FWHM)', p.w.toFixed(4),'°')+
  row('  μ', p.mu.toFixed(4))+row('  A (area)', p.A.toFixed(4))+row('  d-spacing', p.d_spacing_angstrom.toFixed(6),'Å'); }

function renderResults(d){
  const pct = x => (x*100).toFixed(2)+'%';
  let html = `<div class="section">${d.method_name}</div>` +
    row('Wavelength λ', d.wavelength_angstrom.toFixed(5),'Å');
  if (d.method === 'A'){
    html += `<div class="section">Graphitic peak</div>` + peakRows(d.graphitic) +
            `<div class="section">Turbostratic peak</div>` + peakRows(d.turbostratic) +
            `<div class="section">Result</div>` +
            row('X_g / X_t', pct(d.area_fraction_graphitic)+' / '+pct(d.area_fraction_turbostratic)) +
            row('baseline y0', d.baseline_y0.toFixed(4)) +
            row("d′ weighted", d.d_spacing_weighted_angstrom.toFixed(6),'Å');
  } else {
    html += `<div class="section">Dual fit — Graphitic (NETL)</div>` + peakRows(d.graphitic) +
            `<div class="section">Dual fit — Turbostratic (NETL)</div>` + peakRows(d.turbostratic) +
            `<div class="section">Dual result (NETL)</div>` +
            row('X_g / X_t', pct(d.area_fraction_graphitic)+' / '+pct(d.area_fraction_turbostratic)) +
            row("d′ weighted", d.d_spacing_weighted_angstrom.toFixed(6),'Å') +
            row('Crystallite Lc', d.crystallite_height_Lc_angstrom.toFixed(2),'Å') +
            `<div class="section">Single fit (legacy)</div>` + peakRows(d.single_peak) +
            row('DG% legacy', d.DG_single_percent.toFixed(2),'%') +
            row('DG% overestimation', d.dg_overestimation_percent.toFixed(2),'%');
  }
  html += `<div class="dgbox"><div class="cap">Degree of Graphitization` +
          (d.method==='B' ? ' (NETL dual)' : '') + `</div>` +
          `<div class="dg">${d.DG_percent.toFixed(2)} %</div>` +
          `<div class="cap">Maire-Mering equation</div></div>`;
  resultsEl.innerHTML = html;
}
</script>
</body>
</html>
"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XRD Run Dashboard</title>
<style>
  :root { --bg:#1e1e2e; --panel:#2a2a3e; --accent:#7aa2f7; --green:#9ece6a;
          --amber:#e0af68; --text:#c0caf5; --muted:#565f89; --btn:#364a82; --btnact:#4a6296; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:-apple-system,Helvetica,Arial,sans-serif; }
  header { background:var(--panel); padding:14px 22px; display:flex; align-items:center;
           gap:14px; flex-wrap:wrap; border-bottom:1px solid #12121e; }
  header h1 { font-size:18px; color:var(--accent); margin:0; }
  .navlink { color:var(--amber); text-decoration:none; font-size:13px; font-weight:600;
             padding:6px 12px; border:1px solid var(--amber); border-radius:6px; }
  .navlink:hover { background:var(--amber); color:var(--bg); }
  button, .filebtn { background:var(--btn); color:var(--text); border:none; border-radius:6px;
    padding:8px 16px; font-size:13px; cursor:pointer; transition:background .15s; }
  button:hover, .filebtn:hover { background:var(--btnact); }
  button.primary { background:var(--accent); color:var(--bg); font-weight:600; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  #fname { color:var(--muted); font-size:13px; font-family:monospace; }
  select { background:var(--btn); color:var(--text); border:none; border-radius:6px;
           padding:7px 10px; font-size:13px; }
  main { display:grid; grid-template-columns:1fr 1fr; gap:16px; padding:16px; }
  @media (max-width:980px){ main{ grid-template-columns:1fr; } }
  .card { background:var(--panel); border-radius:10px; padding:16px; overflow:auto; }
  .card h2 { font-size:14px; color:var(--accent); margin:0 0 10px; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th, td { padding:5px 8px; text-align:right; border-bottom:1px solid #23233a; white-space:nowrap; }
  th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--panel); }
  td.lbl, th.lbl { text-align:left; max-width:240px; overflow:hidden; text-overflow:ellipsis; }
  td.miss { color:var(--muted); }
  tr.err td { color:#f7768e; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }
  .chip { background:#12121e; color:var(--text); border-radius:12px; padding:3px 10px; font-size:11px; }
  .ctrls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
  .ctrls label { color:var(--muted); font-size:12px; }
  .tablecard { grid-column:1 / -1; }
  .mini { font-size:11px; padding:4px 10px; margin-left:10px; vertical-align:middle; }
  .chartimg { width:100%; border-radius:8px; display:block; }
  #status { padding:8px 22px; background:#12121e; color:var(--muted); font-size:12px; }
  #status.error { color:#f7768e; }
  .placeholder { color:var(--muted); text-align:center; padding:40px 0; }
</style>
</head>
<body>
<header>
  <h1>XRD Run Dashboard</h1>
  <a class="navlink" href="/">← Analyzer</a>
  <label class="filebtn">Choose .xy files…
    <input id="files" type="file" accept=".xy,.txt,.dat,text/plain" multiple style="display:none">
  </label>
  <span id="fname">no files selected</span>
  <button id="run" class="primary" disabled>Analyze runs</button>
</header>

<main>
  <div class="card tablecard">
    <h2>Parsed runs &amp; results
      <button id="csvBtn" class="mini" disabled>Download CSV</button></h2>
    <div id="chips" class="chips"></div>
    <div id="tablewrap"><div class="placeholder">Upload .xy files to extract run parameters.</div></div>
  </div>
  <div class="card">
    <h2>Chart 1</h2>
    <div class="ctrls">
      <label>Y<select id="ySel1"></select></label>
      <label>X<select id="xSel1"></select></label>
      <label>Group<select id="gSel1"></select></label>
    </div>
    <div id="chartwrap1"><div class="placeholder">Chart appears after analysis.</div></div>
  </div>
  <div class="card">
    <h2>Chart 2</h2>
    <div class="ctrls">
      <label>Y<select id="ySel2"></select></label>
      <label>X<select id="xSel2"></select></label>
      <label>Group<select id="gSel2"></select></label>
    </div>
    <div id="chartwrap2"><div class="placeholder">Chart appears after analysis.</div></div>
  </div>
</main>

<div id="status">Ready.</div>

<script>
const X = {temperature_C:"Temperature (°C)", caco3_ratio:"CaCO₃ ratio", time_h:"Dwell time (h)",
           fe_ratio:"Fe ratio", carbon_ratio:"Carbon ratio"};
const Y = {DG_B:"DG% (NETL dual)", DG_A:"DG% (Method A)", Lc:"Crystallite Lc (Å)",
           dg_overestimation:"DG% overestimation", d_prime_B:"d′ weighted (Å)"};
const G = {carbon_type:"Carbon type", form:"Sample form", wash:"Wash state", none:"(none)"};

const filesEl=document.getElementById('files'), fnameEl=document.getElementById('fname');
const runBtn=document.getElementById('run'), statusEl=document.getElementById('status');
const csvBtn=document.getElementById('csvBtn');
const tablewrap=document.getElementById('tablewrap'), chipsEl=document.getElementById('chips');
// Two independent chart panels (suffix 1 / 2)
const panels={
  '1':{x:document.getElementById('xSel1'), y:document.getElementById('ySel1'),
       g:document.getElementById('gSel1'), wrap:document.getElementById('chartwrap1')},
  '2':{x:document.getElementById('xSel2'), y:document.getElementById('ySel2'),
       g:document.getElementById('gSel2'), wrap:document.getElementById('chartwrap2')},
};
let xy=[], rows=[];

function setStatus(m,e=false){ statusEl.textContent=m; statusEl.className=e?'error':''; }
function opts(sel,map){ sel.innerHTML=Object.entries(map).map(([k,v])=>`<option value="${k}">${v}</option>`).join(''); }
for(const id of ['1','2']){ const p=panels[id]; opts(p.x,X); opts(p.y,Y); opts(p.g,G);
  p.x.value='temperature_C'; p.g.value='carbon_type';
  [p.x,p.y,p.g].forEach(s=>s.addEventListener('change',()=>drawChart(id))); }
panels['1'].y.value='DG_B';   // default: DG% vs T
panels['2'].y.value='Lc';     // default: Lc  vs T  (side-by-side comparison)
function readText(f){ return new Promise((res,rej)=>{const r=new FileReader();
  r.onload=e=>res(e.target.result); r.onerror=()=>rej(new Error('read '+f.name)); r.readAsText(f);}); }

filesEl.addEventListener('change', async e=>{
  const fs=Array.from(e.target.files||[]); if(!fs.length) return;
  try{ xy=await Promise.all(fs.map(async f=>({name:f.name, xy:await readText(f)}))); }
  catch(err){ setStatus('Read error: '+err,true); return; }
  fnameEl.textContent=`${xy.length} file(s) selected`; runBtn.disabled=false;
  setStatus(`${xy.length} file(s) loaded — click Analyze runs.`);
});

runBtn.addEventListener('click', async ()=>{
  if(!xy.length){ setStatus('No files selected.',true); return; }
  runBtn.disabled=true; runBtn.textContent='Working…'; setStatus(`Analyzing ${xy.length} runs (both methods)…`);
  try{
    const resp=await fetch('/batch_analyze',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({files:xy})});
    const data=await resp.json();
    if(!resp.ok||data.error){ setStatus('Error: '+(data.error||resp.statusText),true); }
    else { rows=data.rows; renderTable(); renderChips(); csvBtn.disabled=false;
      await Promise.all([drawChart('1'), drawChart('2')]);
      const ok=rows.filter(r=>!r.error).length;
      setStatus(`Done — ${ok}/${rows.length} run(s) analyzed.`); }
  }catch(err){ setStatus('Request failed: '+err,true); }
  finally{ runBtn.disabled=false; runBtn.textContent='Analyze runs'; }
});

csvBtn.addEventListener('click', downloadCSV);

function cell(v,dig){ if(v===null||v===undefined||v==='') return '<td class="miss">–</td>';
  return `<td>${typeof v==='number'?v.toFixed(dig):v}</td>`; }

function renderTable(){
  const head=['Run','Type','C','Fe','CaCO₃','T(°C)','t(h)','Form','Wash','DG%·A','DG%·B','Lc(Å)'];
  let h='<table><thead><tr><th class="lbl">'+head[0]+'</th>'+head.slice(1).map(x=>`<th>${x}</th>`).join('')+'</tr></thead><tbody>';
  for(const r of rows){
    const cls=r.error?' class="err"':'';
    h+=`<tr${cls}><td class="lbl" title="${r.file}">${r.label||r.file}</td>`+
       cell(r.carbon_type)+cell(r.carbon_ratio,0)+cell(r.fe_ratio,0)+cell(r.caco3_ratio,4)+
       cell(r.temperature_C,0)+cell(r.time_h,0)+cell(r.form)+cell(r.wash)+
       cell(r.DG_A,2)+cell(r.DG_B,2)+cell(r.Lc,1)+'</tr>';
  }
  tablewrap.innerHTML=h+'</tbody></table>';
}

function renderChips(){
  const count=(k)=>{ const m={}; rows.forEach(r=>{const v=r[k]??'—'; m[v]=(m[v]||0)+1;});
    return Object.entries(m).map(([v,n])=>`${v}:${n}`).join('  '); };
  chipsEl.innerHTML = [`${rows.length} runs`, 'Carbon — '+count('carbon_type'),
    'Form — '+count('form'), 'Temp — '+count('temperature_C')]
    .map(t=>`<span class="chip">${t}</span>`).join('');
}

async function drawChart(id){
  if(!rows.length) return;
  const p=panels[id];
  p.wrap.innerHTML='<div class="placeholder">Rendering…</div>';
  const resp=await fetch('/chart',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({rows, x:p.x.value, y:p.y.value, group:p.g.value})});
  const data=await resp.json();
  if(data.chart_png) p.wrap.innerHTML=`<img class="chartimg" src="${data.chart_png}" alt="chart">`;
  else p.wrap.innerHTML=`<div class="placeholder">${data.error||'no chart'}</div>`;
}

function downloadCSV(){
  if(!rows.length) return;
  const cols=['file','carbon_type','carbon_ratio','fe_ratio','caco3_ratio',
    'temperature_C','time_h','form','wash','date','DG_A','DG_B','Lc',
    'dg_overestimation','d_prime_B','graphitic_xc_B','error'];
  const esc=v=>{ if(v===null||v===undefined) return '';
    const s=String(v); return /[",\n]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s; };
  const lines=[cols.join(',')];
  for(const r of rows) lines.push(cols.map(c=>esc(r[c])).join(','));
  const blob=new Blob([lines.join('\n')],{type:'text/csv'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download='xrd_runs.csv';
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence per-request logging
        pass

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))

    def do_GET(self) -> None:
        path = self.path.rstrip("/")
        if path.endswith(("/analyze", "/batch_analyze", "/chart")):
            self._send(405, "text/plain; charset=utf-8", b"Use POST for this endpoint")
        elif "dashboard" in path:
            self._send(200, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8"))
        else:
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))

    def do_POST(self) -> None:
        path = self.path.rstrip("/")
        try:
            if path.endswith("/batch_analyze"):
                self._handle_batch()
            elif path.endswith("/chart"):
                self._handle_chart()
            else:
                self._handle_analyze()
        except ValueError as exc:
            self._send(400, "application/json", json.dumps({"error": str(exc)}).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(500, "application/json",
                       json.dumps({"error": f"unexpected error — {exc}"}).encode("utf-8"))

    # -- endpoints -----------------------------------------------------------

    def _handle_analyze(self) -> None:
        payload = self._read_json()
        pattern = XRDPattern.from_text(payload.get("xy", ""))
        analyzer = GraphitizationAnalyzer(pattern)
        out: dict = {}
        for method in ("A", "B"):
            try:
                res = analyzer.run(method)
                res["plot_png"] = render_plot(pattern, res)
            except (FitError, ValueError) as exc:
                res = {"method": method, "error": str(exc)}
            out[method] = res
        self._send(200, "application/json", json.dumps(out).encode("utf-8"))

    def _handle_batch(self) -> None:
        payload = self._read_json()
        files = payload.get("files", [])
        rows = build_dashboard_rows(files)
        self._send(200, "application/json", json.dumps({"rows": rows}).encode("utf-8"))

    def _handle_chart(self) -> None:
        payload = self._read_json()
        png = render_dashboard_chart(payload.get("rows", []),
                                     payload.get("x", "temperature_C"),
                                     payload.get("y", "DG_B"),
                                     payload.get("group", "carbon_type"))
        self._send(200, "application/json", json.dumps({"chart_png": png}).encode("utf-8"))


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
