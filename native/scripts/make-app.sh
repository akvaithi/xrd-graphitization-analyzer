#!/bin/bash
# Build the SwiftUI executable and wrap it into a double-click .app bundle.
#   native/scripts/make-app.sh [debug|release]
#
# AI assist defaults to Apple's on-device model (macOS 27+). The bundled Ollama
# gemma3:4b is the fallback. Control how much of it ships via OLLAMA_BUNDLE:
#   full     bundle runtime + gemma3:4b model        (~3.6 GB, fully offline everywhere)
#   runtime  bundle runtime only; model self-downloads on demand (~454 MB, default)
#   none     bundle nothing; fallback needs a system Ollama        (~5 MB)
set -euo pipefail

CONFIG="${1:-release}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="XRD Graphitization Analyzer"
OLLAMA_BUNDLE="${OLLAMA_BUNDLE:-runtime}"

# The on-device AI (FoundationModels) needs a full Xcode toolchain — the bare
# Command Line Tools can't load the required Swift macros. Prefer an installed
# Xcode unless the caller already pinned DEVELOPER_DIR.
if [ -z "${DEVELOPER_DIR:-}" ]; then
  for xc in /Applications/Xcode.app /Applications/Xcode-beta.app; do
    if [ -d "$xc/Contents/Developer" ]; then export DEVELOPER_DIR="$xc/Contents/Developer"; break; fi
  done
fi

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
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>CFBundleDocumentTypes</key>
  <array><dict>
    <key>CFBundleTypeName</key><string>XRD scan</string>
    <key>CFBundleTypeRole</key><string>Viewer</string>
    <key>LSHandlerRank</key><string>Owner</string>
    <key>LSItemContentTypes</key><array><string>page.akvaithi.xrd.xy</string></array>
  </dict></array>
  <key>UTExportedTypeDeclarations</key>
  <array><dict>
    <key>UTTypeIdentifier</key><string>page.akvaithi.xrd.xy</string>
    <key>UTTypeDescription</key><string>XRD scan (.xy)</string>
    <key>UTTypeConformsTo</key><array><string>public.plain-text</string></array>
    <key>UTTypeTagSpecification</key>
    <dict>
      <key>public.filename-extension</key><array><string>xy</string></array>
    </dict>
  </dict></array>
</dict></plist>
PLIST

# --- App icon -----------------------------------------------------------------
if [ -f "$ROOT/Resources/AppIcon.icns" ]; then
  cp "$ROOT/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
  echo "  bundled app icon"
fi

# --- Bundle the Ollama runtime / gemma3:4b fallback (see OLLAMA_BUNDLE above) ---
# (best-effort: copied from a local Ollama install; if absent or skipped, the app
#  uses Apple's on-device model and can download gemma on demand. Assets are NOT
#  in git — too large.)
RES="$APP/Contents/Resources"
OLLAMA_RES="${OLLAMA_RES:-/Applications/Ollama.app/Contents/Resources}"
MODELS_SRC="${OLLAMA_MODELS_SRC:-$HOME/.ollama/models}"
MANIFEST="$MODELS_SRC/manifests/registry.ollama.ai/library/gemma3/4b"

if [ "$OLLAMA_BUNDLE" = "none" ]; then
  echo "  OLLAMA_BUNDLE=none — Apple on-device only; gemma fallback needs a system Ollama"
else
  if [ -x "$OLLAMA_RES/ollama" ]; then
    mkdir -p "$RES/ollama-runtime"
    ( cd "$OLLAMA_RES" && cp -R ollama llama-server llama-quantize \
          *.dylib *.so mlx_metal_v3 mlx_metal_v4 "$RES/ollama-runtime/" 2>/dev/null ) || true
    echo "  bundled Ollama runtime"
  else
    echo "  WARN: $OLLAMA_RES/ollama not found — app will use a system Ollama"
  fi
fi

if [ "$OLLAMA_BUNDLE" = "full" ]; then
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
    echo "  WARN: gemma3:4b not in $MODELS_SRC (run: ollama pull gemma3:4b) — model not bundled"
  fi
else
  echo "  gemma3:4b model NOT bundled — downloads on demand into Application Support"
fi

# --- Liquid Glass (macOS 26+ design) --------------------------------------------
# The new design is gated on the linked-SDK version recorded in LC_BUILD_VERSION.
# SwiftPM stamps that to the deployment target (14.0), which forces the legacy
# appearance. Re-stamp the SDK to 27 (keeping minos=14 for back-compat) so the
# system opts the app into Liquid Glass. Must run before codesign.
if xcrun vtool -set-build-version macos 14.0 27.0 -replace \
      -output "$APP/Contents/MacOS/$APP_NAME" "$APP/Contents/MacOS/$APP_NAME" >/dev/null 2>&1; then
  echo "  re-stamped linked SDK → 27.0 (Liquid Glass)"
else
  echo "  WARN: vtool re-stamp failed — app may show the legacy appearance"
fi

codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
echo "Built: $APP  ($(du -sh "$APP" | cut -f1))"
