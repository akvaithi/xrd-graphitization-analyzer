# XRD Degree of Graphitization Analyzer

Object-oriented tool that parses X-ray Diffraction `.xy` files of synthetic
graphite and computes the **Degree of Graphitization (DG%)** of the carbon (002)
reflection. Two selectable calculation methods, a CLI (single-file + batch), and
a local/zero-Tk web GUI that computes both methods at once.

## Calculation methods (`--method`)

- **Method A — NETL paper standard**
  Bimodal Pseudo-Voigt deconvolution (graphitic + turbostratic), Bragg
  d-spacings, area fractions, weighted `d′`, and the Maire–Mering equation.

- **Method B — OriginLab PsdVoigt1 (XRD ppt) + NETL**
  1. Linear baseline subtraction over **24°–27.5°**.
  2. Fits the exact OriginLab **PsdVoigt1** line shape with strict bounds
     (`μ ∈ [0,1]`; turbostratic `xc ∈ [25.1, 26.3]`; graphitic `xc ∈ [26.3, 26.8]`).
  3. Runs **both** a single-peak (legacy) and dual-peak (NETL) fit and reports the
     legacy **DG% overestimation**.
  4. Crystallite stacking height **Lc = 0.89·λ / (B·cos(θ/2))** from the
     graphitic FWHM.

X-ray wavelength is configurable; default **λ = 1.54187 Å** (NETL standard).
`OptimizeWarning` / fit failures (e.g. high amorphous content) are caught and
reported cleanly.

Dependencies: `numpy`, `scipy`, `matplotlib`.

## CLI

```bash
python3 xrd_analyzer.py sample.xy --method A
python3 xrd_analyzer.py sample.xy --method B --json
python3 xrd_analyzer.py sample.xy --wavelength 1.5406

# Batch (multiple files / a directory) → table, JSON array, or CSV
python3 xrd_analyzer.py *.xy --method B --csv results.csv
python3 xrd_analyzer.py data_dir/ --method A --json
```

`--json` emits a strict JSON object (one file) or array (batch) containing the
methodology, calculations, and fitted parameters (`mu`, `w`, `xc`, `A`), and
suppresses all other output.

## Web GUI

```bash
python3 xrd_webgui.py            # serves http://127.0.0.1:8000
python3 xrd_webgui.py --port 8642
```

Choose one or more `.xy` files, set the wavelength, and click **Analyze**. **Both
methods are computed**; the dropdown switches which method's results + plot are
shown, and multiple files are paged. The server honours `$PORT`/`$HOST` (binds
`0.0.0.0` when `$PORT` is set), so it runs unchanged on container hosts.

## Deploy (Render)

Includes a `render.yaml` blueprint and a `Procfile`. On a connected repo, Render
auto-deploys on each push (`pip install -r requirements.txt`, then
`python3 xrd_webgui.py`). The same `Procfile` works on Railway/Heroku-style hosts.

## Pipeline

1. Parse `.xy` (2θ, intensity); window to 24°–27.5° (baseline-subtracted for B).
2. Fit Pseudo-Voigt peak(s) with `scipy.optimize.curve_fit`.
3. Bragg d-spacing per phase: `d = λ / (2·sin θ)`.
4. Area fractions `X = A_i / ΣA`; weighted `d′ = X_g·d_g + X_t·d_t`.
5. Maire–Mering: `DG% = (3.440 − d′) / (3.440 − 3.354) × 100`.
