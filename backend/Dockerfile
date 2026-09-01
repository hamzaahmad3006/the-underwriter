# One container, one volume, one process (§27, TD-01).
# OPS-020: exactly one instance. The scheduler runs in-process, and a second
# replica would double-submit every cycle.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY pyproject.toml README.md* ./
COPY underwriter/__init__.py ./underwriter/
RUN pip install --upgrade pip && pip install .

COPY underwriter ./underwriter

# DATABASE_URL points here; §27 mounts a persistent volume on it. NFR-005
# wants zero loss of committed records across a restart, which needs the
# volume to outlive the container.
RUN mkdir -p /app/data

# Run as a non-root user. Nothing here needs root, and a container that does
# not need it should not have it.
RUN useradd --create-home --uid 10001 underwriter && chown -R underwriter /app
USER underwriter

EXPOSE 8000

# OPS-021: liveness only. Readiness is /health/deep and must not restart the
# container — a restart mid-cycle aborts work that is already in flight.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

# One worker, deliberately. Two would mean two schedulers.
CMD ["uvicorn", "underwriter.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
