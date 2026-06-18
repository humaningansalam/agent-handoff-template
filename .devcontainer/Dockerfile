FROM mcr.microsoft.com/devcontainers/base:ubuntu


RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ripgrep fd-find jq \
    && ln -sf "$(command -v fdfind)" /usr/local/bin/fd || true \
    && rm -rf /var/lib/apt/lists/*
