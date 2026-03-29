FROM node:20-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        docker.io \
        git \
        openssh-client \
        python3 \
        python3-pip \
        python3-venv \
        python3-pytest \
    && (apt-get install -y --no-install-recommends docker-compose-plugin || apt-get install -y --no-install-recommends docker-compose) \
    && rm -rf /var/lib/apt/lists/*

RUN cat <<'EOF' > /usr/local/bin/docker
#!/bin/sh
if [ "$1" = "compose" ] && ! /usr/bin/docker compose version >/dev/null 2>&1; then
  shift
  exec docker-compose "$@"
fi
exec /usr/bin/docker "$@"
EOF

RUN chmod +x /usr/local/bin/docker

RUN npm install -g @openai/codex

CMD ["/bin/sh", "-lc", "sleep infinity"]
