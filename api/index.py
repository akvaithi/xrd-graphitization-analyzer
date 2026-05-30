"""
Vercel serverless entrypoint.

Reuses the exact request handler from the local web GUI (xrd_webgui.Handler).
Vercel's @vercel/python runtime instantiates the module-level ``handler`` class
(a BaseHTTPRequestHandler) per request; all routing lives in the handler, which
is path-tolerant so it works behind the catch-all rewrite in vercel.json.
"""

import os
import sys

# matplotlib needs a writable cache dir; only /tmp is writable on Vercel.
# Must be set before xrd_webgui imports matplotlib.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

# Make the repo-root modules (xrd_webgui, xrd_analyzer) importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xrd_webgui import Handler as handler  # noqa: E402  (Vercel looks for `handler`)
