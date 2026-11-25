#!/bin/sh

# Run database migrations (must succeed)
echo "Running migrations..."
if ! python -m alembic upgrade head; then
    echo "ERROR: Migrations failed"
    exit 1
fi

# Load real market data if MyStocks API key is configured
if [ -n "$MYSTOCKS_API_KEY" ]; then
    echo "Loading market data from MyStocks..."

    # Run each step independently so failures don't block later steps
    echo "1. Syncing instruments..."
    python -m nse_platform.cli sync-instruments --provider mystocks || echo "Warning: sync-instruments failed"

    echo "2. Backfilling price data..."
    python -m nse_platform.cli backfill --provider mystocks || echo "Warning: backfill failed"

    echo "3. Ingesting corporate actions..."
    python -m nse_platform.cli ingest-corporate --provider mystocks || echo "Warning: ingest-corporate failed"

    echo "4. Calculating analytics and signals..."
    python -m nse_platform.cli analytics --replay || echo "Warning: analytics failed"

    echo "Market data loading complete (some steps may have failed, but API will start)"
else
    echo "MYSTOCKS_API_KEY not set; skipping market data load"
fi

# Start the API
echo "Starting API..."
exec python -m uvicorn nse_platform.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips=*
