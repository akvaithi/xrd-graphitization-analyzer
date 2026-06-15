#!/bin/bash
# Build the SwiftUI executable and wrap it into a double-click .app bundle.
#   native/scripts/make-app.sh [debug|release]
set -euo pipefail

CONFIG="${1:-release}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="XRD Graphitization Analyzer"

swift build -c "$CONFIG" --package-path "$ROOT" --product XRDApp

BIN="$ROOT/.build/$CONFIG/XRDApp"
APP="$ROOT/.build/$APP_NAME.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$APP_NAME"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>edu.tamu.xrd.graphitization</string>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict></plist>
PLIST

# --- Bundle the Ollama runtime + gemma3:4b so AI assist needs zero setup -------
# (best-effort: copied from a local Ollama install; if absent, the app falls
#  back to a system Ollama. Assets are NOT in git — too large.)
RES="$APP/Contents/Resources"
OLLAMA_RES="${OLLAMA_RES:-/Applications/Ollama.app/Contents/Resources}"
MODELS_SRC="${OLLAMA_MODELS_SRC:-$HOME/.ollama/models}"
MANIFEST="$MODELS_SRC/manifests/registry.ollama.ai/library/gemma3/4b"

if [ -x "$OLLAMA_RES/ollama" ]; then
  mkdir -p "$RES/ollama-runtime"
  ( cd "$OLLAMA_RES" && cp -R ollama llama-server llama-quantize \
        *.dylib *.so mlx_metal_v3 mlx_metal_v4 "$RES/ollama-runtime/" 2>/dev/null ) || true
  echo "  bundled Ollama runtime"
else
  echo "  WARN: $OLLAMA_RES/ollama not found — app will use a system Ollama"
fi

if [ -f "$MANIFEST" ]; then
  mkdir -p "$RES/ollama-models/blobs" "$RES/ollama-models/manifests/registry.ollama.ai/library/gemma3"
  cp "$MANIFEST" "$RES/ollama-models/manifests/registry.ollama.ai/library/gemma3/4b"
  python3 - "$MODELS_SRC" "$RES/ollama-models" "$MANIFEST" <<'PY'
import json, shutil, sys
src, dest, man = sys.argv[1], sys.argv[2], sys.argv[3]
m = json.load(open(man))
for d in [m["config"]["digest"]] + [l["digest"] for l in m["layers"]]:
    fn = "sha256-" + d.split(":")[1]
    shutil.copy(f"{src}/blobs/{fn}", f"{dest}/blobs/{fn}")
PY
  echo "  bundled gemma3:4b model (~3.3 GB)"
else
  echo "  WARN: gemma3:4b not in $MODELS_SRC (run: ollama pull gemma3:4b) — app will use a system Ollama"
fi

codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
echo "Built: $APP  ($(du -sh "$APP" | cut -f1))"
