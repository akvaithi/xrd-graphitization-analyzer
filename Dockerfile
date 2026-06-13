# XRD Graphitization Analyzer — container image
FROM python:3.12-slim

# libgomp1: OpenMP runtime required by the numpy/scipy manylinux wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# MPLCONFIGDIR: persistent matplotlib cache baked into the image. PORT triggers
# 0.0.0.0 binding and disables the browser auto-open in xrd_webgui.main().
ENV MPLCONFIGDIR=/app/.mplconfig \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Pre-build the font cache + Agg renderer at build time so container startup is
# instant (otherwise matplotlib rebuilds the cache on the first request).
RUN mkdir -p "$MPLCONFIGDIR" \
    && python -c "import matplotlib; matplotlib.use('Agg'); \
from matplotlib.figure import Figure; import io; \
f=Figure(); f.add_subplot(111).plot([0,1],[1,0]); f.savefig(io.BytesIO(), format='png')"

# Application code (last, so edits don't bust the dependency/cache layers).
COPY xrd_analyzer.py xrd_webgui.py run_parser.py ./

EXPOSE 8000

# Liveness check (no curl in slim — use stdlib urllib).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/' % os.environ.get('PORT','8000')).read()" || exit 1

CMD ["python3", "xrd_webgui.py"]
