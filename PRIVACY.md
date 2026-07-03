# Privacy Policy — XRD Graphitization Analyzer

**Last updated: July 3, 2026**

**Summary: this app does not collect, store, sell, or share any personal
data.** It has no user accounts, no advertising, no analytics, and no crash
reporting. It's a scientific analysis tool — the `.xy` X-ray diffraction scans
you open are processed entirely on your own device.

This policy covers all three ways the app ships — the Windows app, the native
macOS app, and the self-hosted web app — and calls out the one place their
behavior differs (the optional AI-assist feature, below).

## What the app does with your files

- **The `.xy` scan files you open** are read from disk and analyzed locally
  by the app's built-in engine. The app never uploads your scan files or your
  results anywhere.
- **Sidecar files** (`MyScan.xy.xrda.json`) — the analysis settings and
  results for a scan — are written next to the original file, on your own
  disk. They're never transmitted. The original `.xy` file is never modified.
- **File association.** Installing the Windows app can register `.xy` files
  to open with it (an optional installer setting). This is a standard
  Windows file-type association, not data collection.

## Optional AI-assisted deconvolution suggestion

Every surface computes the actual analysis result (Degree of Graphitization,
crystallinity, yield) locally and deterministically — AI is never involved in
that number. A separate, optional "Suggest" feature can propose *how* to set
up the deconvolution (peak count, background, etc.); the human always
confirms it. If you use this feature, a small set of *derived numeric
features* describing the peak shape — not your raw scan data — is sent
on-device to:

- **Windows app:** a **locally-running [Ollama](https://ollama.com) server on
  your own computer** (`http://localhost:11434` by default). This is local
  inference — nothing leaves your device, unless you've independently
  configured Ollama itself to run on a remote machine.
- **Native macOS app:** Apple's on-device Foundation Models framework by
  default (macOS 26+, nothing leaves your device), or the same local Ollama
  path as above as a fallback.
- **Self-hosted web app:** the operator's deployment sends the derived
  features to the **Anthropic Claude API** (a third-party service) using an
  API key the operator provides. See
  [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy). If
  you're using someone else's deployment of the web app, that operator
  controls this, not the app's author.

If the AI feature isn't used, none of this applies — the app works fully
offline either way.

## Local-only data the app keeps

- The sidecar files described above.
- **Native macOS app only:** a local decision log
  (`~/Library/Application Support/XRD Graphitization Analyzer/decisions.jsonl`)
  recording each AI suggestion and the human-confirmed result, kept to
  improve future prompt tuning. It never leaves your Mac and you can delete
  it at any time. The Windows app does not currently keep this log.

Nothing above is an account, profile, or identifier tied to you personally —
it's local analysis history tied to the files on your own disk.

## What this app does not do

- No user accounts, sign-in, or authentication.
- No analytics, telemetry, or crash reporting.
- No advertising or ad tracking.
- No access to your camera, microphone, location, or contacts.
- No selling or sharing of data — because none is collected.

## Children's privacy

The app is a scientific/engineering tool not directed at children and
collects no data from anyone, regardless of age.

## Open source

This app is open source, so these claims are independently verifiable rather
than something you have to take on faith:
<https://github.com/akvaithi/xrd-graphitization-analyzer>

## Changes to this policy

If this policy changes, the update will be reflected here with a new "Last
updated" date and noted in the project's release notes.

## Contact

Arun Vaithianathan — [akvaithi.page](https://akvaithi.page) — or open an issue:
<https://github.com/akvaithi/xrd-graphitization-analyzer/issues>
