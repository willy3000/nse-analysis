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
COPY services/api/docker-entrypoint.sh ./
COPY services/api/healthcheck.py ./
RUN chmod +x ./docker-entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "healthcheck.py"]
# Default: API with auto-data-load. Worker: `python -m nse_platform.worker.main`. Migrations: `python -m alembic upgrade head`.
CMD ["./docker-entrypoint.sh"]
