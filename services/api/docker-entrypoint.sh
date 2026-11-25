#!/bin/sh
set -e

# Run database migrations
echo "Running migrations..."
python -m alembic upgrade head

# Load real market data if MyStocks API key is configured
if [ -n "$MYSTOCKS_API_KEY" ]; then
    echo "Loading market data from MyStocks..."
    if python -m nse_platform.cli sync-instruments --provider mystocks && \
       python -m nse_platform.cli backfill --provider mystocks && \
       python -m nse_platform.cli ingest-corporate --provider mystocks && \
       python -m nse_platform.cli analytics --replay; then
        echo "Market data loaded successfully"
    else
        echo "Warning: Market data loading failed, but API will start anyway"
    fi
else
    echo "MYSTOCKS_API_KEY not set; skipping market data load"
fi

# Start the API
echo "Starting API..."
exec python -m uvicorn nse_platform.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips=*
