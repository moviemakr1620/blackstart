FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
LABEL org.opencontainers.image.source="https://github.com/moviemakr1620/blackstart" \
      org.opencontainers.image.url="https://github.com/moviemakr1620/blackstart" \
      org.opencontainers.image.title="Blackstart" \
      org.opencontainers.image.description="UPS-driven shutdown and restore controller for Proxmox, Synology, and Docker." \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BLACKSTART_DATA_DIR=/data \
    BLACKSTART_HOST=0.0.0.0 \
    BLACKSTART_PORT=8088
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* \
    && groupadd --system blackstart && useradd --system --gid blackstart --home-dir /data --shell /usr/sbin/nologin blackstart
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend /app
COPY --from=ui /ui/dist /app/static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh && mkdir -p /data && chown blackstart:blackstart /data
VOLUME ["/data"]
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/api/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
