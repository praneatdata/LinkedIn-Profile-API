FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime deps only (tests live in requirements-dev.txt). curl_cffi ships manylinux
# wheels, so no compiler toolchain is needed in the image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Drop privileges. Nothing here writes to disk.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Platforms (Railway/Render/Fly) inject $PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000

# urlopen raises (non-zero exit) on any non-2xx, which is exactly the semantics we want.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8000') + '/health', timeout=4)"

# Single worker on purpose: the TTL cache is in-process, and more workers would mean
# more cache misses and therefore more requests against a rate-limited LinkedIn account.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
