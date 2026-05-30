# XRD Degree of Graphitization Analyzer

Parses X-ray Diffraction `.xy` files and calculates the **Degree of
Graphitization (DG%)** of carbon materials using the NETL standard: a bimodal
Pseudo-Voigt deconvolution of the carbon (002) peak followed by the Maire–Mering
equation.

## Features

- **Two fitting models**
  - `legacy` — Kα1/Kα2 *doublet* Pseudo-Voigt (height-parametrised; analytic Kα2
    shadow positioned via Bragg's Law).
  - `origin` — OriginLab **PsdVoigt2** (area-normalised, shared FWHM, shared `y0`
    baseline; no Kα2), faithful to NETL's Origin procedure.
- **Bruker `.brml` support** — reads `WaveLengthAlpha1/Alpha2` and
  `WaveLengthRatio` from `MeasurementContainer.xml` (defaults: 1.5406, 1.54439,
  0.5).
- **CLI** with single-file and **batch** processing (directories / globs), plus
  `--json` and `--csv` output.
- **Local web GUI** with multi-file upload, paginated per-file results, and an
  embedded fit plot.

Dependencies: `numpy`, `scipy`, `matplotlib`.

## CLI

```bash
# Single file
python3 xrd_analyzer.py sample.xy
python3 xrd_analyzer.py sample.xy --json
python3 xrd_analyzer.py sample.xy --model origin --brml sample.brml

# Batch (multiple files / a directory) → table, JSON array, or CSV
python3 xrd_analyzer.py *.xy --csv results.csv
python3 xrd_analyzer.py data_dir/ --model origin --json
```

## Web GUI

```bash
python3 xrd_webgui.py            # serves http://127.0.0.1:8000
python3 xrd_webgui.py --port 8642
```

Choose one or more `.xy` files (optionally a `.brml`), pick a model, and click
**Analyze**. With multiple files, page between per-file results using the pager.

## Pipeline

1. Parse `.xy` (2θ, intensity); window to 22°–30° 2θ.
2. Fit a bimodal Pseudo-Voigt (chosen model) with `scipy.optimize.curve_fit`.
3. Bragg d-spacing per phase: `d = λ / (2·sin θ)` (Kα1 only).
4. Area fractions `X = A_i / ΣA`; weighted `d′ = X_g·d_g + X_t·d_t`.
5. Maire–Mering: `DG% = (3.440 − d′) / (3.440 − 3.354) × 100`.
