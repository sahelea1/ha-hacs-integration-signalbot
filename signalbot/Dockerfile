ARG BUILD_FROM
# hadolint ignore=DL3007
FROM ${BUILD_FROM}

USER root

# Install python3 (stdlib-only manager), curl and jq (used by entrypoint health checks).
# Noble pip is externally-managed; we use stdlib only so no pip install is needed.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        curl \
        jq \
    && rm -rf /var/lib/apt/lists/*

# Runtime environment
ENV MODE=native \
    SIGNAL_CLI_CONFIG_DIR=/data/signal-cli \
    SIGNAL_API_URL=http://127.0.0.1:8080 \
    SIGNALBOT_MANAGER_PORT=8099

# Overlay rootfs: copies our entrypoint, supervisord fragment, and (from the other
# agent) /opt/signalbot-manager/server.py into the image.
COPY rootfs /

RUN chmod +x /usr/bin/signalbot-entrypoint.sh

ENTRYPOINT ["/usr/bin/signalbot-entrypoint.sh"]
