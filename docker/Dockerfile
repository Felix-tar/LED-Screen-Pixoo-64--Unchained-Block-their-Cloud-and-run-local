# Pixoo-local runtime image (arm64 / Raspberry Pi 4).
# Contains the dependencies + process manager + broker; the application code,
# config and secrets are bind-mounted at runtime (see docker-compose.yml), so
# code changes need only a container restart, not a rebuild.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/pixoo-local

RUN apt-get update && apt-get install -y --no-install-recommends \
        mosquitto mosquitto-clients \
        supervisor tini \
        iproute2 iputils-ping curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# unprivileged user for the app services (web/heartbeat/supervisor loop)
RUN useradd --system --uid 987 --user-group --home /opt/pixoo-local --shell /usr/sbin/nologin pixoo

# python deps (cached layer)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir pytest==8.3.2   # for offline `run_tests.sh`

# entrypoint + supervisord config live in the image; app code is bind-mounted
COPY docker/entrypoint.sh /usr/local/bin/pixoo-entrypoint
COPY docker/supervisord.conf /etc/supervisor/pixoo-supervisord.conf
RUN chmod +x /usr/local/bin/pixoo-entrypoint

WORKDIR /opt/pixoo-local

# tini as PID 1 for correct signal handling / zombie reaping
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/pixoo-entrypoint"]
