"""
xrd_webgui.py — Browser-based GUI for the XRD Graphitization Analyzer.

A self-contained local web app. Uses only the Python standard library
(http.server) plus the project's existing numpy/scipy/matplotlib stack.
No Tk — works reliably on macOS where the system Tcl/Tk 8.5 is broken.

The browser reads the chosen .xy file as text (client-side FileReader) and
POSTs its contents to /analyze. The server runs the deconvolution, renders
the fit plot to a PNG via matplotlib's headless 'Agg' backend, and returns a
JSON payload with all values plus the plot as a base64 data-URI.

Usage:
    python3 xrd_webgui.py                # serves on http://127.0.0.1:8000
    python3 xrd_webgui.py --port 9000
    python3 xrd_webgui.py --no-browser   # don't auto-open the browser
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

# Headless rendering backend — must be set before importing pyplot/Figure
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

from xrd_analyzer import (
    MODELS,
    default_metadata,
    fit_and_report,
    make_bimodal_doublet,
    make_bimodal_origin,
    origin_pseudo_voigt,
    parse_brml_bytes,
    parse_xy_text,
    pseudo_voigt,
)

# ---------------------------------------------------------------------------
# Plot colours (mirror the original dark theme)
# ---------------------------------------------------------------------------
PANEL     = "#2a2a3e"
RED_PEAK  = "#f7768e"
BLUE_PEAK = "#7dcfff"
FIT_COL   = "#bb9af7"
RAW_COL   = "#a9b1d6"
MUTED     = "#565f89"
TEXT      = "#c0caf5"


# ---------------------------------------------------------------------------
# Plot rendering
# ---------------------------------------------------------------------------

def render_plot_png(popt, tw, iw, results) -> str:
    """Render the (002) fit to a base64-encoded PNG data-URI string."""
    fig = Figure(figsize=(6.4, 4.6), dpi=110, facecolor=PANEL)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#12121e")

    x_plot = np.linspace(tw.min(), tw.max(), 600)
    model = results.get("model", "legacy")

    if model == "legacy":
        # Total fit = full Kα1/Kα2 doublet; components = deconvolved Kα1 primaries
        meta = {
            "wavelength_alpha1": results["wavelength_alpha1"],
            "wavelength_alpha2": results["wavelength_alpha2"],
            "wavelength_ratio":  results["wavelength_ratio"],
        }
        y_fit = make_bimodal_doublet(meta)(x_plot, *popt)
        y_g = pseudo_voigt(x_plot, popt[0], popt[1], popt[2], popt[3])
        y_t = pseudo_voigt(x_plot, popt[4], popt[5], popt[6], popt[7])
        g_label = f"Graphitic Kα1  2θ={results['graphitic_peak_center_2theta_deg']:.3f}°"
        t_label = f"Turbostratic Kα1  2θ={results['turbostratic_peak_center_2theta_deg']:.3f}°"
        fit_label = "Total fit (Kα1+Kα2)"
        title = "Carbon (002) Reflection — Doublet Pseudo-Voigt (legacy)"
    else:  # origin (PsdVoigt2): area-normalised peaks on a shared y0 baseline
        y0 = popt[8]
        y_fit = make_bimodal_origin()(x_plot, *popt)
        # Components drawn on top of the baseline so they sit in the raw data
        y_g = origin_pseudo_voigt(x_plot, popt[0], popt[1], popt[2], popt[3]) + y0
        y_t = origin_pseudo_voigt(x_plot, popt[4], popt[5], popt[6], popt[7]) + y0
        g_label = f"Graphitic  2θ={results['graphitic_peak_center_2theta_deg']:.3f}°"
        t_label = f"Turbostratic  2θ={results['turbostratic_peak_center_2theta_deg']:.3f}°"
        fit_label = "Total fit (PsdVoigt2 + y0)"
        title = "Carbon (002) Reflection — Origin PsdVoigt2 (NETL)"

    ax.scatter(tw, iw, s=8, color=RAW_COL, alpha=0.7, label="Raw data", zorder=2)

    ax.fill_between(x_plot, y_g, alpha=0.25, color=RED_PEAK)
    ax.plot(x_plot, y_g, color=RED_PEAK, lw=1.5, label=g_label)

    ax.fill_between(x_plot, y_t, alpha=0.18, color=BLUE_PEAK)
    ax.plot(x_plot, y_t, color=BLUE_PEAK, lw=1.5, label=t_label)

    ax.plot(x_plot, y_fit, color=FIT_COL, lw=2.0, ls="--", label=fit_label, zorder=5)

    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(MUTED)
    ax.set_xlabel("2θ  (degrees)", color=MUTED, fontsize=9)
    ax.set_ylabel("Intensity  (a.u.)", color=MUTED, fontsize=9)
    ax.set_title(title, color=TEXT, fontsize=9, pad=8)
    ax.legend(fontsize=8, facecolor=PANEL, edgecolor=MUTED,
              labelcolor=TEXT, framealpha=0.9)
    ax.set_xlim(tw.min() - 0.2, tw.max() + 0.2)
    fig.tight_layout(pad=1.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=PANEL)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# ---------------------------------------------------------------------------
# HTML page  (single self-contained string)
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
           align-items:center; gap:18px; flex-wrap:wrap;
           border-bottom:1px solid #12121e; }
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
  main { display:grid; grid-template-columns:360px 1fr; gap:16px; padding:16px; }
  @media (max-width:820px){ main{ grid-template-columns:1fr; } }
  .card { background:var(--panel); border-radius:10px; padding:18px; }
  .section { color:var(--accent); font-size:13px; font-weight:600;
             margin:16px 0 6px; border-bottom:1px solid var(--muted); padding-bottom:4px; }
  .section:first-child { margin-top:0; }
  .row { display:flex; justify-content:space-between; align-items:baseline;
         padding:3px 0; font-size:13px; }
  .row .lbl { color:var(--text); }
  .row .val { color:var(--green); font-family:monospace; font-weight:600; }
  .row .unit { color:var(--muted); font-size:11px; margin-left:4px; }
  .dgbox { margin-top:22px; background:#2d2038; border-radius:10px;
           padding:18px; text-align:center; }
  .dgbox .cap { color:var(--muted); font-size:12px; }
  .dgbox .dg { color:var(--amber); font-size:40px; font-weight:700; margin:6px 0; }
  #plot { width:100%; border-radius:8px; display:block; }
  #plotwrap { display:flex; align-items:center; justify-content:center;
              min-height:360px; color:var(--muted); }
  #status { padding:8px 22px; background:#12121e; color:var(--muted);
            font-size:12px; }
  #status.error { color:#f7768e; }
  .placeholder { color:var(--muted); text-align:center; }
  select { background:var(--btn); color:var(--text); border:none; border-radius:6px;
           padding:8px 10px; font-size:13px; cursor:pointer; }
  #pager { display:none; align-items:center; gap:10px; padding:8px 22px;
           background:var(--panel); border-bottom:1px solid #12121e; font-size:13px; }
  #pager button { background:var(--btn); color:var(--text); border:none; border-radius:6px;
                  padding:6px 12px; font-size:13px; cursor:pointer; }
  #pager button:disabled { opacity:.4; cursor:not-allowed; }
  #pagerInfo { color:var(--muted); }
  #fileSel { max-width:420px; }
  #fileSel option.err { color:#f7768e; }
</style>
</head>
<body>
<header>
  <h1>XRD Graphitization Analyzer</h1>
  <div class="controls">
    <label class="filebtn">Choose .xy file(s)…
      <input id="file" type="file" accept=".xy,.txt,.dat,text/plain" multiple style="display:none">
    </label>
    <span id="fname">no file selected</span>
    <label class="filebtn">.brml (optional)…
      <input id="brml" type="file" accept=".brml" style="display:none">
    </label>
    <span id="brmlname">Cu defaults</span>
    <select id="model" title="Curve-fitting model">
      <option value="legacy">Model: legacy (Kα1/Kα2 doublet)</option>
      <option value="origin">Model: origin (NETL PsdVoigt2)</option>
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
    <div class="placeholder">Choose a file and click Analyze.</div>
  </div>
  <div class="card">
    <div id="plotwrap"><span class="placeholder">Plot appears here after analysis.</span></div>
  </div>
</main>

<div id="status">Ready.</div>

<script>
const fileInput = document.getElementById('file');
const brmlInput = document.getElementById('brml');
const modelSel  = document.getElementById('model');
const fnameEl   = document.getElementById('fname');
const brmlNameEl= document.getElementById('brmlname');
const analyzeBtn= document.getElementById('analyze');
const statusEl  = document.getElementById('status');
const resultsEl = document.getElementById('results');
const plotWrap  = document.getElementById('plotwrap');
const pagerEl   = document.getElementById('pager');
const prevBtn   = document.getElementById('prevBtn');
const nextBtn   = document.getElementById('nextBtn');
const fileSel   = document.getElementById('fileSel');
const pagerInfo = document.getElementById('pagerInfo');

let xyFiles = [];      // [{name, text}]
let brmlB64 = null;    // optional .brml as base64 (no data-URI prefix)
let brmlName = null;
let batch = [];        // [{name, status:'ok'|'error', data?, error?}]
let current = 0;

function setStatus(msg, isError=false) {
  statusEl.textContent = msg;
  statusEl.className = isError ? 'error' : '';
}

function readText(f) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = ev => resolve(ev.target.result);
    r.onerror = () => reject(new Error('read failed: ' + f.name));
    r.readAsText(f);
  });
}

fileInput.addEventListener('change', async e => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  try {
    xyFiles = await Promise.all(files.map(async f => ({ name: f.name, text: await readText(f) })));
  } catch (err) {
    setStatus('Could not read .xy file(s): ' + err, true);
    return;
  }
  fnameEl.textContent = xyFiles.length === 1
    ? xyFiles[0].name : `${xyFiles.length} files selected`;
  analyzeBtn.disabled = false;
  setStatus(`${xyFiles.length} file(s) loaded — click Analyze.`);
});

brmlInput.addEventListener('change', e => {
  const f = e.target.files[0];
  if (!f) { brmlB64 = null; brmlName = null; brmlNameEl.textContent = 'Cu defaults'; return; }
  brmlName = f.name;
  brmlNameEl.textContent = f.name;
  const reader = new FileReader();
  reader.onload = ev => {
    // readAsDataURL → "data:...;base64,XXXX"; keep only the base64 payload
    brmlB64 = ev.target.result.split(',', 2)[1];
    setStatus(`.brml loaded (${f.name}) — wavelengths will be read from it.`);
  };
  reader.onerror = () => setStatus('Could not read .brml file.', true);
  reader.readAsDataURL(f);
});

analyzeBtn.addEventListener('click', runAnalysis);
prevBtn.addEventListener('click', () => showResult(current - 1));
nextBtn.addEventListener('click', () => showResult(current + 1));
fileSel.addEventListener('change', () => showResult(parseInt(fileSel.value, 10)));

async function analyzeOne(file, model) {
  try {
    const resp = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        xy: file.text, brml_b64: brmlB64, brml_name: brmlName, model: model,
      }),
    });
    const data = await resp.json();
    if (!resp.ok || data.error)
      return { name: file.name, status: 'error', error: data.error || resp.statusText };
    return { name: file.name, status: 'ok', data: data };
  } catch (err) {
    return { name: file.name, status: 'error', error: String(err) };
  }
}

async function runAnalysis() {
  if (!xyFiles.length) { setStatus('No .xy file selected.', true); return; }
  analyzeBtn.disabled = true;
  analyzeBtn.textContent = 'Working…';
  const model = modelSel.value;
  batch = [];

  for (let i = 0; i < xyFiles.length; i++) {
    setStatus(`Fitting ${model} model — ${i + 1}/${xyFiles.length}: ${xyFiles[i].name}…`);
    batch.push(await analyzeOne(xyFiles[i], model));
  }

  current = 0;
  renderPager();
  showResult(0);

  const ok = batch.filter(b => b.status === 'ok').length;
  if (batch.length === 1 && ok === 1) {
    const d = batch[0].data;
    const src = d.metadata_source === 'brml' ? `.brml (${brmlName})` : 'Cu defaults';
    setStatus(`Done — ${batch[0].name}   DG% = ${d.DG_percent.toFixed(2)}%   ` +
              `d′ = ${d.d_spacing_weighted_angstrom.toFixed(6)} Å   [${src}]`);
  } else {
    setStatus(`Done — ${ok}/${batch.length} file(s) analyzed (${model} model).`);
  }
  analyzeBtn.disabled = false;
  analyzeBtn.textContent = 'Analyze';
}

function renderPager() {
  if (batch.length <= 1) { pagerEl.style.display = 'none'; return; }
  pagerEl.style.display = 'flex';
  fileSel.innerHTML = batch.map((b, i) => {
    const tag = b.status === 'ok' ? `DG ${b.data.DG_percent.toFixed(2)}%` : 'ERROR';
    const cls = b.status === 'ok' ? '' : ' class="err"';
    return `<option value="${i}"${cls}>${i + 1}/${batch.length}  ${b.name}  —  ${tag}</option>`;
  }).join('');
}

function showResult(i) {
  if (i < 0 || i >= batch.length) return;
  current = i;
  if (batch.length > 1) {
    fileSel.value = String(i);
    pagerInfo.textContent = `File ${i + 1} of ${batch.length}`;
    prevBtn.disabled = (i === 0);
    nextBtn.disabled = (i === batch.length - 1);
  }
  const entry = batch[i];
  if (entry.status === 'ok') {
    renderResults(entry.data);
  } else {
    resultsEl.innerHTML =
      `<div class="section">${entry.name}</div>` +
      `<div class="placeholder" style="color:#f7768e">Analysis failed:<br><br>${entry.error}</div>`;
    plotWrap.innerHTML = `<span class="placeholder">No plot — analysis failed.</span>`;
  }
}

function row(lbl, val, unit='') {
  return `<div class="row"><span class="lbl">${lbl}</span>` +
         `<span><span class="val">${val}</span>` +
         (unit ? `<span class="unit">${unit}</span>` : '') + `</span></div>`;
}

function renderResults(d) {
  const pct = x => (x*100).toFixed(2) + '%';
  const srcLabel = d.metadata_source === 'brml' ? 'Bruker .brml' : 'Cu default';
  const isLegacy = (d.model || 'legacy') === 'legacy';
  const modelLabel = isLegacy ? 'legacy — Kα1/Kα2 doublet'
                              : 'origin — NETL PsdVoigt2';

  // Model-specific peak / shape rows
  let peakRows =
    `<div class="section">Peak Positions (2θ)</div>` +
    row('Graphitic (narrow)',   d.graphitic_peak_center_2theta_deg.toFixed(4), '°') +
    row('Turbostratic (broad)', d.turbostratic_peak_center_2theta_deg.toFixed(4), '°');
  if (isLegacy) {
    peakRows +=
      row('Kα2 shadow (graph.)',  d.graphitic_kalpha2_center_2theta_deg.toFixed(4), '°') +
      row('Kα2 shadow (turbo.)',  d.turbostratic_kalpha2_center_2theta_deg.toFixed(4), '°') +
      `<div class="section">Peak Shape (σ, η)</div>` +
      row('σ graphitic',    d.graphitic_sigma_deg.toFixed(4), '°') +
      row('σ turbostratic', d.turbostratic_sigma_deg.toFixed(4), '°') +
      row('η graphitic',    d.graphitic_eta.toFixed(4)) +
      row('η turbostratic', d.turbostratic_eta.toFixed(4));
  } else {
    peakRows +=
      `<div class="section">Peak Shape (w FWHM, μ)</div>` +
      row('w graphitic',    d.graphitic_fwhm_deg.toFixed(4), '°') +
      row('w turbostratic', d.turbostratic_fwhm_deg.toFixed(4), '°') +
      row('μ graphitic',    d.graphitic_mu.toFixed(4)) +
      row('μ turbostratic', d.turbostratic_mu.toFixed(4)) +
      row('baseline y0',    d.baseline_y0.toFixed(6));
  }

  resultsEl.innerHTML =
    `<div class="section">Fitting Model</div>` +
    row('Model', modelLabel) +
    `<div class="section">Hardware Metadata (${srcLabel})</div>` +
    row('λ Kα1',        d.wavelength_alpha1.toFixed(6), 'Å') +
    row('λ Kα2',        d.wavelength_alpha2.toFixed(6), 'Å') +
    row('Kα2/Kα1 ratio', d.wavelength_ratio.toFixed(6)) +
    peakRows +
    `<div class="section">d-Spacings (Bragg, Kα1 λ = ${d.wavelength_alpha1.toFixed(5)} Å)</div>` +
    row('d graphitic',    d.d_spacing_graphitic_angstrom.toFixed(6), 'Å') +
    row('d turbostratic', d.d_spacing_turbostratic_angstrom.toFixed(6), 'Å') +
    row('d′ weighted',    d.d_spacing_weighted_angstrom.toFixed(6), 'Å') +
    `<div class="section">Integrated Area Fractions</div>` +
    row('X_g (graphitic)',    pct(d.graphitic_area_fraction)) +
    row('X_t (turbostratic)', pct(d.turbostratic_area_fraction)) +
    `<div class="dgbox"><div class="cap">Degree of Graphitization</div>` +
    `<div class="dg">${d.DG_percent.toFixed(2)} %</div>` +
    `<div class="cap">Maire-Mering equation</div></div>`;

  if (d.plot_png) {
    plotWrap.innerHTML = `<img id="plot" src="${d.plot_png}" alt="fit plot">`;
  }
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    # Silence default per-request logging to keep the console clean
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        # Serve the single-page app for any GET except the analyze endpoint.
        # Path-tolerant so it also works behind a Vercel rewrite (where the
        # function may be reached as /api/index rather than /).
        if self.path.rstrip("/").endswith("/analyze"):
            self._send(405, "text/plain; charset=utf-8", b"Use POST for /analyze")
        else:
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))

    def do_POST(self) -> None:
        # Any POST is treated as an analyze request (single endpoint), so it
        # works whether the path is /analyze locally or rewritten on Vercel.
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            # Body is JSON: {"xy", "brml_b64"|null, "model": "legacy"|"origin"}
            payload = json.loads(body.decode("utf-8", errors="replace"))
            xy_text = payload.get("xy", "")
            model = payload.get("model", "legacy")
            if model not in MODELS:
                model = "legacy"

            # Optional .brml → hardware metadata, else Cu defaults
            brml_b64 = payload.get("brml_b64")
            if brml_b64:
                meta = parse_brml_bytes(base64.b64decode(brml_b64),
                                        label=payload.get("brml_name", "uploaded .brml"))
            else:
                meta = default_metadata()

            two_theta, intensity = parse_xy_text(xy_text)
            results, popt, tw, iw = fit_and_report(two_theta, intensity, meta, model)
            results["plot_png"] = render_plot_png(popt, tw, iw, results)

            self._send(200, "application/json",
                       json.dumps(results).encode("utf-8"))
        except ValueError as exc:
            self._send(400, "application/json",
                       json.dumps({"error": str(exc)}).encode("utf-8"))
        except RuntimeError as exc:
            self._send(400, "application/json",
                       json.dumps({"error": f"curve fitting did not converge — {exc}"}).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(500, "application/json",
                       json.dumps({"error": f"unexpected error — {exc}"}).encode("utf-8"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Cloud hosts (Render/Railway/Fly/Heroku-likes) inject $PORT and expect the
    # process to bind 0.0.0.0. Use those as defaults when present; locally fall
    # back to 127.0.0.1:8000. CLI flags still override everything.
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

    # Don't try to open a browser on a headless cloud host.
    if not args.no_browser and not is_cloud:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
