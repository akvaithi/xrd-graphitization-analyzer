"""
ai_suggest.py — provider-agnostic AI deconvolution suggester.

Given an XRD (002) spectrum it computes numeric features and asks an LLM
(Anthropic Claude *or* a local Ollama model) to choose the NETL deconvolution
setup — peak count, turbostratic shoulder position, background. It does NOT
compute DG%: the deterministic pipeline does that. Stdlib-only (urllib) so the
container needs no extra dependencies.

Config (env, overridable per call):
    AI_PROVIDER      "claude" | "ollama"            (default "claude")
    ANTHROPIC_API_KEY   Claude key
    ANTHROPIC_MODEL  default "claude-opus-4-8"
    OLLAMA_HOST      default "http://localhost:11434"
    OLLAMA_MODEL     default "qwen2.5"
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
import warnings

import numpy as np
from scipy.optimize import curve_fit

warnings.simplefilter("ignore")

DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
DEFAULT_OLLAMA_MODEL = "qwen2.5"

SYSTEM_PROMPT = """You are an expert XRD analyst applying the NETL standard procedure to deconvolve the carbon (002) reflection of Fe-catalyzed petroleum-coke graphite, to SET UP a Degree of Graphitization calculation. You decide ONLY the deconvolution setup; you do NOT compute DG%.

CRITICAL DOMAIN FACT: these Fe-catalyzed samples almost always retain a SMALL but physically real TURBOSTRATIC fraction - a broad low-angle shoulder (2-theta ~26.0-26.45, below the sharp graphitic peak ~26.4-26.7). It is usually SUBTLE (a few percent of peak height), so a high single-peak R2 (>0.99) does NOT rule it out. Experts fit TWO peaks in the large majority of these samples.

Rules:
1. DEFAULT to peak_count=2. Place turbostratic_2theta at the low-angle shoulder, using low_angle_residual_2theta and automatic_two_peak_turbostratic_2theta as anchors (typically 26.0-26.4, below the graphitic peak).
2. peak_count=1 ONLY if truly symmetric: low_angle_residual_fraction < ~0.015 AND dR2 < ~0.0005. The exception, not the norm. (When peak_count=1, still output a plausible turbostratic_2theta; it is ignored.)
3. amorphous_invalid=true only if no resolvable (002) peak (very broad/weak, low SNR).
4. subtract_background only if an obvious sloped background; else false.
Respond with ONLY a JSON object with keys: peak_count (1 or 2), turbostratic_2theta (number), subtract_background (bool), amorphous_invalid (bool), confidence (0-1), rationale (short string)."""

# Number-typed turbostratic (not nullable) so the schema works on Ollama too.
SCHEMA = {
    "type": "object",
    "properties": {
        "peak_count": {"type": "integer", "enum": [1, 2]},
        "turbostratic_2theta": {"type": "number"},
        "subtract_background": {"type": "boolean"},
        "amorphous_invalid": {"type": "boolean"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["peak_count", "turbostratic_2theta", "subtract_background",
                 "amorphous_invalid", "confidence", "rationale"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------

def _pv(x, A, xc, w, mu):
    ln2 = math.log(2.0)
    dx = x - xc
    return A * (mu * (2 / np.pi) * (w / (4 * dx ** 2 + w ** 2))
                + (1 - mu) * (np.sqrt(4 * ln2) / (np.sqrt(np.pi) * w))
                * np.exp(-(4 * ln2 / w ** 2) * dx ** 2))


def compute_features(two_theta, intensity, low=24.0, high=30.0) -> dict:
    """Single-peak + automatic two-peak fits → the features the LLM reasons over."""
    tt = np.asarray(two_theta, float)
    inten = np.asarray(intensity, float)
    m = (tt >= low) & (tt <= high)
    x, y = tt[m], inten[m]
    if x.size < 12:
        raise ValueError("too few points in the (002) window")
    ph = float(y.max())
    y0 = float(np.median(np.r_[y[:8], y[-8:]]))

    f1 = lambda x, y0, A, xc, w, mu: y0 + _pv(x, A, xc, w, mu)
    p1, _ = curve_fit(f1, x, y, p0=[y0, ph * 0.9, 26.55, 0.2, 0.5],
                      bounds=([-np.inf, 0, 26.0, 0.02, 0], [np.inf, np.inf, 26.9, 3, 1]),
                      maxfev=40000)
    yh = f1(x, *p1)
    r2_1 = 1 - np.sum((y - yh) ** 2) / np.sum((y - np.mean(y)) ** 2)
    resid = y - yh
    lo = x < p1[2]
    si = int(np.argmax(np.where(lo, resid, -1e9)))

    f2 = lambda x, y0, Ag, xcg, wg, mug, At, xct, wt: \
        y0 + _pv(x, Ag, xcg, wg, mug) + _pv(x, At, xct, wt, 1.0)
    auto_turbo, r2_2 = None, r2_1
    try:
        p2, _ = curve_fit(f2, x, y,
                          p0=[y0, ph * .6, 26.55, .15, .5, ph * .3, 26.2, .6],
                          bounds=([-np.inf, 0, 26.3, .02, 0, 0, 25.1, .05],
                                  [np.inf, np.inf, 26.8, 3, 1, np.inf, 26.45, 3]),
                          maxfev=40000)
        yh2 = f2(x, *p2)
        r2_2 = 1 - np.sum((y - yh2) ** 2) / np.sum((y - np.mean(y)) ** 2)
        auto_turbo = round(float(p2[6]), 3)
    except Exception:  # noqa: BLE001
        pass
    noise = float(np.std(np.r_[y[:8], y[-8:]]))
    return {
        "single_peak_R2": round(float(r2_1), 5),
        "two_peak_R2": round(float(r2_2), 5),
        "dR2": round(float(r2_2 - r2_1), 5),
        "single_peak_center": round(float(p1[2]), 3),
        "single_peak_FWHM": round(float(p1[3]), 3),
        "low_angle_residual_2theta": round(float(x[si]), 3),
        "low_angle_residual_fraction": round(float(resid[si] / ph), 4),
        "automatic_two_peak_turbostratic_2theta": auto_turbo,
        "SNR": round(float((ph - y0) / max(noise, 1e-9)), 1),
    }


# --------------------------------------------------------------------------
# Providers (stdlib HTTP only)
# --------------------------------------------------------------------------

def _http_json(url, payload, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ask_claude(features, model, api_key):
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    body = {
        "model": model, "max_tokens": 2000,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user",
                      "content": "Features (JSON):\n" + json.dumps(features, indent=2)}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }
    resp = _http_json("https://api.anthropic.com/v1/messages", body,
                      {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    text = next(b["text"] for b in resp["content"] if b.get("type") == "text")
    return json.loads(text)


def _ask_ollama(features, model, host):
    body = {
        "model": model, "stream": False, "format": SCHEMA,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Features (JSON):\n" + json.dumps(features, indent=2)},
        ],
    }
    resp = _http_json(host.rstrip("/") + "/api/chat", body, {}, timeout=300)
    return json.loads(resp["message"]["content"])


def suggest(features: dict, provider: str | None = None, model: str | None = None,
            *, api_key: str | None = None, ollama_host: str | None = None) -> dict:
    """Return the deconvolution decision dict from the chosen provider."""
    provider = (provider or os.environ.get("AI_PROVIDER", "claude")).lower()
    if provider == "claude":
        return _ask_claude(features, model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_CLAUDE_MODEL),
                           api_key or os.environ.get("ANTHROPIC_API_KEY", ""))
    if provider == "ollama":
        return _ask_ollama(features, model or os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
                           ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    raise ValueError(f"unknown provider '{provider}' (use 'claude' or 'ollama')")
