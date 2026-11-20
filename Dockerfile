# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS pi-runtime
RUN npm install --prefix /opt/pi --no-audit --no-fund @earendil-works/pi-coding-agent@0.87.1

FROM python:3.12-slim-bookworm AS app

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    bash ca-certificates git procps ripgrep \
 && rm -rf /var/lib/apt/lists/*

COPY --from=pi-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=pi-runtime /opt/pi /opt/pi

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/rsi \
    PATH="/opt/pi/node_modules/.bin:${PATH}"

WORKDIR /app
COPY . /app

RUN mkdir -p /app/private /data && chmod 0700 /app/private

EXPOSE 18766

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18766/api/health', timeout=3)"

CMD ["python3", "-u", "/app/server.py", "--host", "0.0.0.0", "--port", "18766", "--data", "/data"]
