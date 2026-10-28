FROM python:3.12-slim

# Set ENABLE_RECORDING=true in .env to build with voice recording support. That
# swaps stock discord.py for an experimental fork that can receive voice.
ARG ENABLE_RECORDING=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENABLE_RECORDING=${ENABLE_RECORDING}

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg nodejs \
    && if [ "$ENABLE_RECORDING" = "true" ]; then apt-get install --no-install-recommends -y git; fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-recording.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$ENABLE_RECORDING" = "true" ]; then pip install --no-cache-dir -r requirements-recording.txt; fi

COPY app ./app
RUN useradd --create-home --uid 10001 soundboard \
    && mkdir -p /data/clips \
    && chown -R soundboard:soundboard /app /data
USER soundboard

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
