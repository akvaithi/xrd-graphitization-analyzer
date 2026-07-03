# CLAUDE.md

Guidance for working in this repo.

## What this is
Computes **Degree of Graphitization (DG%)** of carbon materials from XRD `.xy`
scans of the carbon (002) reflection, following the NETL method (OriginLab
PsdVoigt1 deconvolution → Bragg d-spacings → area-weighted d′ → Maire–Mering).
Validated against a postdoc's OriginLab gold fits to ~0.9% MAE. It also computes
an amorphous-aware **crystallinity index** (the *amount* of crystalline graphite,
which DG% — a d-spacing/ordering measure — is blind to) and, from weighed run
masses, a carbon/graphite **yield** (see `research/` and the app's Yield tab).
Two supported front-ends share one method:

- **Web / Docker** — `xrd_webgui.py` (stdlib `http.server` + numpy/scipy/matplotlib);
  AI assist uses the **Anthropic Claude API**.
- **Native macOS app** — `native/` (SwiftUI; the engine ported to pure Swift);
  AI assist defaults to **Apple's on-device Foundation Model** (macOS 27+), with a
  local **Ollama gemma3:4b** as fallback (zero setup, offline either way).
- **Native Windows app** — `windows/` (C#/.NET 8 + WinUI 3); full 5-tab parity
  with the macOS app, reusing the *same* Swift `XRDCore` engine through a small
  C-ABI bridge (`native/Sources/XRDBridge/` → `XRDBridge.dll`, P/Invoked from
  C#). AI assist uses **Ollama** (local, `gemma3:4b` by default — same prompt as
  macOS's Ollama path). No DG/crystallinity/yield/calibration math is
  reimplemented in C# — everything routes through the bridge.

> **Platforms:** macOS-native, Windows-native, and web all ship. The Windows app
> is the newest of the three (added after the macOS/web engines were validated) —
> when changing the DG/crystallinity/yield/calibration method, update
> `xrd_analyzer.py` **and** `native/Sources/XRDCore/`; the Windows app picks up
> the change for free since it calls the same Swift engine.

The Python engine and the Swift engine are kept numerically identical — changes
to the method must land in both, verified by the parity tests.

## Key files
- `xrd_analyzer.py` — the engine: `XRDPattern`, `fit_netl` (PsdVoigt1 + free y0),
  `dg_range` (uncertainty), `calibrate_internal_standard`, `scan_impurities`,
  `dg_from_peaks`, `crystallinity` (amorphous-aware 002 decomposition → crystalline
  *amount*, mirrored in Swift), CLI.
- `ai_suggest.py` — Claude deconvolution suggester (features → JSON via tool-use).
- `xrd_webgui.py` — single-page web app (Analyze / Compare / Stack / Manual); shows
  crystallinity alongside DG% (feature parity with the native app); endpoints
  `/fit`, `/ai_suggest`, `/report`, `/chart`, `/stack`, `/batch_analyze`.
- `run_parser.py` — parse synthesis parameters from filenames.
- `research/` — **exploratory tooling kept separate from the shipping engine**
  (Python; its own `README.md`). `amorphous.py` (crystallinity index + optional
  amorphous/turbostratic split), `calibration.py` (mixture / internal-standard →
  absolute wt%), `simulate.py` (PC+Fe+CaCO₃ mass balance + kinetics, incl. Boudouard
  etching), `trends.py` (intensity-vs-parameter EDA), `yield_calc.py` (carbon/graphite
  yield from weighed masses; recipe from filename scaled to the pellet; crystalline-
  graphite yield = mass yield × crystallinity; `--manifest`/`--plots`).
- `native/Sources/XRDCore/` — Swift engine: `GraphitizationAnalyzer`, `InternalStandard`,
  `ImpurityScan`, `AISuggester` (Ollama), `LevenbergMarquardt`, `PseudoVoigt`,
  `Crystallinity` (mirrors `xrd_analyzer.crystallinity`), `YieldCalc` (mirrors
  `research/yield_calc.compute_yield` + `defaultComposition`).
- `native/Sources/XRDApp/` — SwiftUI app. `AppModel` (a shared singleton) holds
  files + per-file `DeconvSettings` + the current `results`; `DetailView` is the
  Analyze pane. AI engine selection lives in `SettingsView` (⌘,). Supporting types:
  - `FitRunner` — the one `DeconvSettings → FitOptions → DGResult` pipeline (used by
    the live pane and the model-level recompute), so all surfaces show one number.
  - `AnalysisStore` — per-file **sidecar** `MyScan.xy.xrda.json` (settings, result
    snapshot, applied shift, redo flag, history, **`YieldInputs`**). Auto-loads on
    open; the raw `.xy` is never modified. **Tolerant decoder** — old/missing keys
    fall back to defaults (new fields like `YieldInputs.cWt/sWt` are Optional).
  - `DetailView` shows a **crystallinity (amount)** card next to DG%; `CompareView`
    adds it as a metric/column. `YieldView` — optional **Yield tab**: enter pellet /
    post-furnace / post-acid; recipe (GPC/Fe/CaCO₃) + composition come from the
    filename (scaled to the pellet), with an editable **per-run composition override**
    (`YieldInputs.cWt/sWt`, nil = per-grade default). Persisted in the sidecar.
  - `AISuggestionService` + `AIConfig` — shared suggester (calibration pre-fit +
    suggestion→settings); `FoundationModelsSuggester` is the Apple on-device backend
    (gated macOS 27+); `AISuggester` (XRDCore) is the Ollama path.
  - `OllamaServer` — private bundled Ollama + in-app model download (`pull`).
  - `ReportBuilder` (CSV), `ExportPreviewView` + `ExportChart`/`ChartOptions` (WYSIWYG
    PNG preview: title/subtitle, components, per-field params box, text scale).
  - Batch: `AppModel.suggestAllAI` / `exportAll`; the app is the **default `.xy`
    handler** (Finder open → `AppDelegate.application(_:open:)`).
  - **Persistence gotcha:** only persist the sidecar on a *genuine* user edit
    (`settingsLoaded` gate); never on a programmatic load, or a transient default
    clobbers saved settings.
- `native/scripts/make-app.sh` — wrap the binary into the `.app`. `OLLAMA_BUNDLE`
  controls the fallback: `full` (runtime+model, ~3.6 GB) / `runtime` (runtime only,
  ~455 MB, model self-downloads — **default**) / `none` (~5 MB). Auto-selects a full
  Xcode toolchain (FoundationModels macros need it) and re-stamps the linked SDK to
  27 via `vtool` (so the app adopts the macOS 26+ Liquid Glass design).
- `native/Sources/XRDBridge/` — `Bridge.swift` (`@_cdecl` C ABI: `xrd_fit`,
  `xrd_range`, `xrd_curve`, `xrd_impurities`, `xrd_crystallinity`, `xrd_yield`,
  `xrd_parse_run`, `xrd_manual`, `xrd_ai_suggest`, `xrd_free`) + `Models.swift`
  (the JSON request/response DTOs). Every function takes one UTF-8 JSON string,
  returns one heap-allocated UTF-8 JSON string freed by `xrd_free`. Built via
  `swift build --product XRDBridge` → `XRDBridge.dll`. `DecisionLog.swift` has
  the one `#if os(Windows)` path guard (`%LOCALAPPDATA%` vs `~/Library/Application
  Support`); everything else in `XRDCore` builds unmodified on Windows.
- `windows/` — the WinUI 3 app, mirroring `native/Sources/XRDApp/` structure:
  - `XRDAnalyzer.Engine/` — `NativeMethods.cs` (P/Invoke), `XrdEngine.cs` (JSON
    (de)serializing wrapper — the *only* place C# talks to the bridge),
    `Models/` (C# DTOs matching the Swift side field-for-field), `XyFile.cs`
    (`.xy` parsing — I/O, not engine math, so it stays in C#).
  - `XRDAnalyzer/ViewModels/` — `AppViewModel` (shared state: files, settings,
    results, redo flags, yield inputs, batch progress — mirrors `AppModel`),
    `AnalyzeViewModel` (per-file interactive state — mirrors `DetailView`, same
    `_settingsLoaded` gate so a programmatic file switch never clobbers the
    sidecar).
  - `XRDAnalyzer/Services/` — `AnalysisStore.cs` (sidecar `.xy.xrda.json` —
    **same schema as `AnalysisStore.swift`**, so sidecars are cross-platform),
    `PlotService.cs` (ScottPlot rendering from bridge-computed curve points),
    `ReportBuilder.cs` (CSV export, matches `ReportBuilder.swift` exactly).
  - `XRDAnalyzer/Views/` — one page per tab (`AnalyzePage`, `ComparePage`,
    `StackPage`, `ManualPage`, `YieldPage`), imperative-refresh style (not
    heavy `x:Bind`) since WinUI binding doesn't cleanly cover dynamic
    visibility/result rows at this scale.
  - `windows/scripts/build.ps1` — builds `XRDBridge.dll` (release), stages it +
    the Swift runtime DLLs (found via PATH, from the swift.org Windows
    toolchain's "Runtimes" install) into `XRDAnalyzer/Redist/` (gitignored,
    rebuilt from source; Release-only — Debug uses `XRDAnalyzer.Engine`'s own
    dev-convenience copy straight from the Swift debug build, so the two never
    collide), then `dotnet build`. `-Msix` additionally produces an MSIX
    package (see the script header for the one-time local dev-signing setup —
    self-signed cert matching `Package.appxmanifest`'s `CN=AppPublisher`,
    trusted in `LocalMachine\TrustedPeople`, which needs an elevated prompt).
  - `windows/scripts/installer.iss` — Inno Setup script for the traditional
    `.exe` installer attached to GitHub Releases (the download most users
    want — no MSIX cert-trust step, just a SmartScreen "Run anyway" click).
    Reuses the app's existing unpackaged activation path (argv file-open +
    `AppInstance` single-instance) via registry-based `.xy` file association,
    no MSIX-specific code needed. `ISCC.exe /DAppVersion=X.Y.Z installer.iss`
    → `windows/scripts/Output/` (gitignored — built locally/in CI, uploaded to
    Releases, never committed).
  - `windows/XRDAnalyzer.Tests/` — xUnit smoke tests, P/Invoke `xrd_fit`/
    `xrd_manual`/`xrd_parse_run` on a committed synthetic fixture (the one
    `.xy` file allowed through `.gitignore`'s `*.xy` rule — same synthetic
    peaks the Python parity tests use inline) and check DG against the
    Python/Swift reference.
- `tests/test_engine.py` — pytest regression + Python↔Swift parity (DG, crystallinity,
  yield); `SWIFT_CLI` resolves to `xrd-validate.exe` on Windows. `tests/test_research.py`
  — the `research/` module (index monotonicity, calibration recovery, mass-balance
  closure, yield self-consistency).

## Commands
```bash
# web (local)
python3 xrd_webgui.py --port 8642            # opens browser
# tests
python3 -m pytest tests/ -q
# native engine + CLI
cd native && swift build
.build/debug/xrd-validate <file.xy> [--peaks 1|2] [--anchor 26.54] [--calib auto]
.build/debug/xrd-validate <file.xy> --crystallinity      # crystalline (amount) fraction
# research/ tooling (crystallinity, calibration, simulation, yield)
python3 research/yield_calc.py --manifest runs.csv --plots out/   # yield + trends
python3 research/trends.py "DATA/xrd scans" --csv m.csv --plots out/
# native app bundle (lean runtime-only by default; needs a full Xcode toolchain)
cd native && ./scripts/make-app.sh           # → .build/"XRD Graphitization Analyzer.app"
OLLAMA_BUNDLE=full ./scripts/make-app.sh     # also bundle gemma3:4b (~3.6 GB)

# Windows app (needs the Swift toolchain for Windows + .NET 8 SDK)
windows\scripts\build.ps1 -Configuration Release   # → windows\XRDAnalyzer\bin\Release\...\XRDAnalyzer.exe
windows\scripts\build.ps1 -Configuration Release -Msix   # also produces an MSIX (see script header for dev-signing)
dotnet test windows\XRDAnalyzer.Tests\XRDAnalyzer.Tests.csproj   # C# bridge smoke test
```
The web `xrd-validate`/Docker need only `requirements.txt`. AI: web reads
`ANTHROPIC_API_KEY`; the desktop app uses Apple's on-device model on macOS 27+
(no setup), else bundled/downloaded gemma3:4b (or set `OLLAMA_HOST` in dev).

## Conventions / gotchas
- **Don't commit private data** — the repo is **public**. Gitignored: `test/`,
  `*.opj`, `*.xy`, `*.brml`, `math verification/`, fonts, **`DATA/`,
  `research/figures/`, `*.xlsx`, `*.pdf`, `*.xrda.json`** (scans, papers,
  spreadsheets, results, sidecars). Also **scrub real process data out of committed
  code/tests/docs** — use synthetic/placeholder masses, sample names, compositions,
  and measured fractions (there's a pending patent). Parity is proven by both
  engines agreeing on the *same synthetic* inputs, not by real numbers.
- Cu Kα λ = **1.54187 Å**; graphite d = 0.3354 nm, turbostratic = 0.3440 nm;
  NETL fit window **24–28.5°**.
- DG is very sensitive to 2θ (~1.4% per 0.01°) — peak-position/calibration changes
  matter; keep the internal-standard significance floor (~0.05°).
- After any engine change, run `pytest` (gold MAE ≤ 1.1%, calibration silence,
  Python↔Swift parity). Gold/Swift tests skip cleanly without data/binary.
- Commit to `main` triggers the GHCR Docker rebuild + the Tests workflow (Linux)
  and the Windows workflow (builds the Swift toolchain + `XRDBridge`/
  `xrd-validate.exe`, runs the parity tests for real, plus the C# smoke test).
- macOS Swift Charts: set explicit `chartXScale`/`chartYScale` domains (auto can drift).
- **Windows Swift builds:** `swift build` creates `.build/debug` (or `/release`) as
  a *symlink* to the real `.build/x86_64-unknown-windows-msvc/<config>/` path;
  without Developer Mode / symlink privilege this silently fails (harmless — the
  DLL/exe still land at the real path) but breaks anything expecting
  `.build/debug/xrd-validate.exe` (e.g. `tests/test_engine.py`'s `SWIFT_CLI`).
  Workaround: `cmd /c mklink /J native\.build\debug native\.build\x86_64-unknown-windows-msvc\debug`
  (a junction, not a symlink — doesn't need the privilege). CI works around this
  the same way (see `.github/workflows/windows.yml`).
- **PowerShell 5.1 + this repo's scripts:** avoid non-ASCII characters (em dashes,
  curly quotes) in `.ps1` files — without a UTF-8 BOM, PS 5.1 can misparse them
  mid-string. Also avoid `$ErrorActionPreference = "Stop"` around native-exe calls
  (e.g. `swift build`) — any stderr output from a native tool becomes a fatal
  `NativeCommandError` even on exit code 0; check `$LASTEXITCODE` explicitly instead.

## Author
Arun Vaithianathan — akvaithi.page — TAMU NETL/ARPA-E graphite-from-coke project.
