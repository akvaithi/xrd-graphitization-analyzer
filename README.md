# downloads

This orphan branch exists only to give the Windows installer a stable,
non-redirecting direct-download URL (via `raw.githubusercontent.com`), for
submissions (Microsoft Store / winget) that require one — GitHub's normal
`.../releases/download/...` URLs 302-redirect to a signed, time-limited
Azure Blob URL, which those submission flows reject.

Not part of the project's source history — see the
[`main`](https://github.com/akvaithi/xrd-graphitization-analyzer/tree/main)
branch for the actual repository. This branch is force-updated on each
Windows release; old installer versions are not kept here (they're still
attached to their respective [GitHub Releases](https://github.com/akvaithi/xrd-graphitization-analyzer/releases)).

Current installer, direct link (no redirect):
<https://raw.githubusercontent.com/akvaithi/xrd-graphitization-analyzer/downloads/XRD-Graphitization-Analyzer-Windows-Setup.exe>
