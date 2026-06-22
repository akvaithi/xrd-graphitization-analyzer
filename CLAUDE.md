# CLAUDE.md

Guidance for working in this repo.

## What this is
Computes **Degree of Graphitization (DG%)** of carbon materials from XRD `.xy`
scans of the carbon (002) reflection, following the NETL method (OriginLab
PsdVoigt1 deconvolution → Bragg d-spacings → area-weighted d′ → Maire–Mering).
Validated against a postdoc's OriginLab gold fits to ~0.9% MAE. Two supported
front-ends share one method:

- **Web / Docker** — `xrd_webgui.py` (stdlib `http.server` + numpy/scipy/matplotlib);
  AI assist uses the **Anthropic Claude API**.
- **Native macOS app** — `native/` (SwiftUI; the engine ported to pure Swift);
  AI assist defaults to **Apple's on-device Foundation Model** (macOS 27+), with a
  local **Ollama gemma3:4b** as fallback (zero setup, offline either way).

> **Platforms:** macOS-native + web only. The Windows build is **deprecated** (not
> currently maintained); don't add Windows-specific code or release assets.

The Python engine and the Swift engine are kept numerically identical — changes
to the method must land in both, verified by the parity tests.

## Key files
- `xrd_analyzer.py` — the engine: `XRDPattern`, `fit_netl` (PsdVoigt1 + free y0),
  `dg_range` (uncertainty), `calibrate_internal_standard`, `scan_impurities`,
  `dg_from_peaks`, CLI.
- `ai_suggest.py` — Claude deconvolution suggester (features → JSON via tool-use).
- `xrd_webgui.py` — single-page web app (Analyze / Compare / Stack / Manual);
  endpoints `/fit`, `/ai_suggest`, `/report`, `/chart`, `/stack`, `/batch_analyze`.
- `run_parser.py` — parse synthesis parameters from filenames.
- `native/Sources/XRDCore/` — Swift engine: `GraphitizationAnalyzer`, `InternalStandard`,
  `ImpurityScan`, `AISuggester` (Ollama), `LevenbergMarquardt`, `PseudoVoigt`.
- `native/Sources/XRDApp/` — SwiftUI app; `AppModel` holds files + per-file
  `DeconvSettings`; `DetailView` is the Analyze pane (engine picker: Automatic /
  Apple on-device / Ollama). `FoundationModelsSuggester` is the Apple on-device
  backend (gated macOS 27+); `OllamaServer` manages the private Ollama + in-app
  model download (`pull` streams `/api/pull` into Application Support).
- `native/scripts/make-app.sh` — wrap the binary into the `.app`. `OLLAMA_BUNDLE`
  controls the fallback: `full` (runtime+model, ~3.6 GB) / `runtime` (runtime only,
  ~455 MB, model self-downloads — **default**) / `none` (~5 MB). Auto-selects a full
  Xcode toolchain (FoundationModels macros need it) and re-stamps the linked SDK to
  27 via `vtool` (so the app adopts the macOS 26+ Liquid Glass design).
- `tests/test_engine.py` — pytest regression + Python↔Swift parity suite.

## Commands
```bash
# web (local)
python3 xrd_webgui.py --port 8642            # opens browser
# tests
python3 -m pytest tests/ -q
# native engine + CLI
cd native && swift build
.build/debug/xrd-validate <file.xy> [--peaks 1|2] [--anchor 26.54] [--calib auto]
# native app bundle (lean runtime-only by default; needs a full Xcode toolchain)
cd native && ./scripts/make-app.sh           # → .build/"XRD Graphitization Analyzer.app"
OLLAMA_BUNDLE=full ./scripts/make-app.sh     # also bundle gemma3:4b (~3.6 GB)
```
The web `xrd-validate`/Docker need only `requirements.txt`. AI: web reads
`ANTHROPIC_API_KEY`; the desktop app uses Apple's on-device model on macOS 27+
(no setup), else bundled/downloaded gemma3:4b (or set `OLLAMA_HOST` in dev).

## Conventions / gotchas
- **Don't commit private data** — `test/`, `*.opj`, `*.xy`, `*.brml`, `math
  verification/`, fonts are gitignored (research data / proprietary). The repo is
  **public**.
- Cu Kα λ = **1.54187 Å**; graphite d = 0.3354 nm, turbostratic = 0.3440 nm;
  NETL fit window **24–28.5°**.
- DG is very sensitive to 2θ (~1.4% per 0.01°) — peak-position/calibration changes
  matter; keep the internal-standard significance floor (~0.05°).
- After any engine change, run `pytest` (gold MAE ≤ 1.1%, calibration silence,
  Python↔Swift parity). Gold/Swift tests skip cleanly without data/binary.
- Commit to `main` triggers the GHCR Docker rebuild + the Tests workflow.
- macOS Swift Charts: set explicit `chartXScale`/`chartYScale` domains (auto can drift).

## Author
Arun Vaithianathan — akvaithi.page — TAMU NETL/ARPA-E graphite-from-coke project.
