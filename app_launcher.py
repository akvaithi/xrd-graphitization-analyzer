"""
app_launcher.py — Desktop launcher for the XRD Graphitization Analyzer.

Boots the local web app on a free port, opens it in the default browser, and
shows a small menu-bar / system-tray icon (Open / Quit). PyInstaller bundles
this into a macOS .app and a Windows .exe so lab users can run everything
locally with a double-click — no Docker, no Python install, no terminal.

Build (see packaging/xrd.spec):  pyinstaller packaging/xrd.spec --noconfirm
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import webbrowser

# matplotlib needs a writable cache dir; use a per-user temp location so the
# frozen app never tries to write inside its (read-only) bundle.
os.environ.setdefault("MPLCONFIGDIR",
                      os.path.join(tempfile.gettempdir(), "xrd-analyzer-mpl"))

from http.server import ThreadingHTTPServer

import xrd_webgui  # noqa: E402 — must follow the MPLCONFIGDIR setup above


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_icon():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, 58, 58], radius=14, fill=(0, 122, 255, 255))
    d.text((13, 24), "XRD", fill=(255, 255, 255, 255))
    return img


def _run_tray(url: str, server) -> None:
    """Menu-bar (macOS) / system-tray (Windows) icon with Open / Quit."""
    import pystray
    from pystray import Menu, MenuItem

    def _open(icon, item):
        webbrowser.open(url)

    def _quit(icon, item):
        threading.Thread(target=server.shutdown, daemon=True).start()
        icon.stop()

    pystray.Icon(
        "xrd", _make_icon(), "XRD Graphitization Analyzer",
        menu=Menu(MenuItem("Open in browser", _open, default=True),
                  MenuItem("Quit", _quit)),
    ).run()


def _console_wait(url: str) -> None:
    """Fallback when no tray backend is available (keeps the process alive)."""
    print(f"\nXRD Graphitization Analyzer is running at {url}")
    print("Leave this window open. Close it (or press Ctrl+C) to quit.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    host = "127.0.0.1"
    # XRD_PORT pins a port (else a free one); XRD_HEADLESS skips browser + tray.
    port = int(os.environ.get("XRD_PORT") or _free_port())
    headless = bool(os.environ.get("XRD_HEADLESS"))
    server = ThreadingHTTPServer((host, port), xrd_webgui.Handler)
    url = f"http://{host}:{port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if headless:
        _console_wait(url)
    else:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            _run_tray(url, server)     # blocks until "Quit"
        except Exception:              # noqa: BLE001 — any tray failure → console mode
            _console_wait(url)
    server.shutdown()


if __name__ == "__main__":
    main()
