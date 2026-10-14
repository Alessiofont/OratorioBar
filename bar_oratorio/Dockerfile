# Build fatta direttamente da Home Assistant (nessuna immagine da scaricare da registri esterni).
ARG BUILD_FROM
FROM python:3.12-alpine3.21

ARG BUILD_VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BAR_VERSION=${BUILD_VERSION} \
    BAR_DATA_DIR=/data \
    BAR_OPTIONS=/data/options.json

RUN apk add --no-cache tailscale tzdata ca-certificates

WORKDIR /opt/bar
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app

CMD ["python", "-m", "app.launcher"]
