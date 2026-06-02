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

X-ray wavelength is fixed to the Cu Kα standard **λ = 1.54187 Å** (NETL),
defined once in `xrd_analyzer.DEFAULT_WAVELENGTH`. `OptimizeWarning` / fit
failures (e.g. high amorphous content) are caught and reported cleanly.

Dependencies: `numpy`, `scipy`, `matplotlib`.

## CLI

```bash
python3 xrd_analyzer.py sample.xy --method A
python3 xrd_analyzer.py sample.xy --method B --json

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

Choose one or more `.xy` files and click **Analyze**. **Both
methods are computed**; the dropdown switches which method's results + plot are
shown, and multiple files are paged. The server honours `$PORT`/`$HOST` (binds
`0.0.0.0` when `$PORT` is set), so it runs unchanged on container hosts.

### Run Dashboard

A second page (**Run Dashboard →**) analyses many runs at once and graphs them
against their synthesis parameters. Run parameters are extracted from the (often
non-standard) **file names** — carbon type (GPC/CPC), carbon/Fe/CaCO₃ ratios,
temperature, dwell time, sample form (puck/powder), and wash state — by a
tolerant regex parser ([run_parser.py](run_parser.py)) that doesn't care about
separator or casing. The dashboard then shows a parsed-runs table and an
interactive comparison chart (e.g. **DG% vs Temperature, grouped by carbon
type**) with selectable X / Y / grouping; the trend line follows the mean at
each X value so replicate runs don't create zig-zags.

## Deploy

The scipy/matplotlib stack wants real RAM, so a container on your own host is the
most reliable option. The included `Dockerfile` binds `0.0.0.0:$PORT` (default
8000), pre-builds matplotlib's font cache, and has a healthcheck.

### Coolify (recommended — self-hosted PaaS)

Coolify builds this repo's `Dockerfile` and runs it behind its own proxy with
automatic TLS and push-to-deploy. No config file needed.

1. **+ New Resource → Public/Private Git Repository** → select this repo / branch `main`.
2. **Build Pack: `Dockerfile`**.
3. **Ports Exposes: `8000`** (the port the app listens on).
4. (Optional) set a **Domain** — Coolify provisions Let's Encrypt TLS.
5. **Deploy**. Enable the auto-deploy webhook so each push redeploys.

The app already sets `PORT=8000` in the image, so it binds `0.0.0.0` for
Coolify's proxy without any extra env. Healthcheck path `/` (or rely on the
Dockerfile `HEALTHCHECK`).

### Plain Docker — prebuilt image (GHCR)

Each push publishes `ghcr.io/akvaithi/xrd-graphitization-analyzer:latest`
(public), so you can pull instead of building:

```bash
docker run -d --name xrd-analyzer --restart unless-stopped \
  -p 8000:8000 ghcr.io/akvaithi/xrd-graphitization-analyzer:latest
# or
docker compose -f compose.ghcr.yml up -d
```

### Plain Docker — build locally

```bash
docker compose up -d --build         # builds from the Dockerfile
# or
docker build -t xrd-analyzer .
docker run -d --restart unless-stopped -p 8000:8000 xrd-analyzer
```

### Render (managed)

A `render.yaml` blueprint + `Procfile` are included; a connected repo auto-deploys
on each push. Note the **free tier is 512 MB RAM**, which can OOM under the
scipy/matplotlib + plot-rendering workload — prefer Coolify/Docker on a larger
host. The same `Procfile` works on Railway/Heroku-style hosts.

## Limits / hardening

The web server applies basic abuse/DoS protection (no login — front it with
Cloudflare Access / a reverse proxy if exposing publicly). All are env-overridable:

| Env var | Default | Purpose |
|---|---|---|
| `XRD_MAX_UPLOAD_MB` | `50` | Max request body; larger → **413** |
| `XRD_MAX_BATCH_FILES` | `300` | Max files per dashboard batch; more → **400** |
| `XRD_MAX_CONCURRENT` | `3` | Simultaneous fit/plot operations; excess waits then → **429** |
| `XRD_BUSY_WAIT_SEC` | `20` | How long a request waits for a free slot |
| `XRD_REQUEST_TIMEOUT` | `60` | Per-socket-op timeout (slowloris guard) |

Responses also carry `X-Content-Type-Options`, `X-Frame-Options`, and
`Referrer-Policy` headers.

## Pipeline

1. Parse `.xy` (2θ, intensity); window to 24°–27.5° (baseline-subtracted for B).
2. Fit Pseudo-Voigt peak(s) with `scipy.optimize.curve_fit`.
3. Bragg d-spacing per phase: `d = λ / (2·sin θ)`.
4. Area fractions `X = A_i / ΣA`; weighted `d′ = X_g·d_g + X_t·d_t`.
5. Maire–Mering: `DG% = (3.440 − d′) / (3.440 − 3.354) × 100`.
