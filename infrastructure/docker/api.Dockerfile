# One image for the API, the worker and the CLI (ADR-0001). Select the process with the container command.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 app
COPY services/api/pyproject.toml ./
COPY services/api/nse_platform ./nse_platform
RUN pip install --upgrade pip && pip install .
COPY services/api/alembic.ini ./
COPY services/api/alembic ./alembic
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live').status==200 else 1)"
# Default: API. Worker: `python -m nse_platform.worker.main`. Migrations: `python -m alembic upgrade head`.
CMD ["python", "-m", "uvicorn", "nse_platform.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
