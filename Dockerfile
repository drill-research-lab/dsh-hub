FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash git ripgrep curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*
# Official GitHub release and npm package verified on 2026-09-11.
ARG DSH_VERSION=0.1.5-rc.2
RUN npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" && npm cache clean --force
# dsh ships its browser UI's Simplified Chinese only (LOCALE_IDS = ["zh",
# "en"], no zh-TW variant) -- rewrite every bundled zh string/regex/template
# literal to Traditional (Taiwan wording) via OpenCC, in place, then discard
# the conversion tooling so it never ships in the final image.
COPY ops/i18n/patch-zh-locale.mjs /tmp/i18n-tools/patch-zh-locale.mjs
RUN cd /tmp/i18n-tools \
    && npm install --no-save opencc-js acorn \
    && node patch-zh-locale.mjs "$(npm root -g)" \
    && cd / && rm -rf /tmp/i18n-tools

# dsh's Settings/Credentials pages only work when the browser's own URL is
# 127.0.0.1/localhost (see STATUS.md) -- reasonable for dsh's default
# single-user-on-your-own-machine use case, but it means those pages are
# permanently broken behind a real reverse-proxied domain like ours. Verified
# this check is client-side only: dsh-api-settings-controller (the server
# side of these RPCs) enforces nothing based on hostname/origin, so trusting
# our own domain here doesn't unlock anything an authenticated session
# couldn't already reach from the browser's own JS console. Leave
# DSH_PUBLIC_HOSTNAME unset to skip this (Settings pages just stay disabled,
# same as unpatched dsh).
ARG DSH_PUBLIC_HOSTNAME=
COPY ops/dsh-trusted-origin/patch-trusted-hostname.mjs /tmp/trusted-origin-tools/patch-trusted-hostname.mjs
RUN cd /tmp/trusted-origin-tools \
    && npm install --no-save acorn \
    && node patch-trusted-hostname.mjs "$(npm root -g)" "${DSH_PUBLIC_HOSTNAME}" \
    && cd / && rm -rf /tmp/trusted-origin-tools
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    'jupyter-server>=2,<3' 'jupyter-server-proxy==4.5.0' \
    && useradd --create-home --uid 1000 demo
COPY singleuser/harness_proxy.py singleuser/start_harness.py singleuser/harness_bridge.js singleuser/dispatcher-provider.yaml singleuser/queue_status_proxy.py /opt/demo/
COPY singleuser/jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
ENV PYTHONPATH=/opt/demo
ENV DSH_HOME=/home/demo/.dsh
USER demo
WORKDIR /home/demo
EXPOSE 8888
CMD ["jupyterhub-singleuser", "--ip=0.0.0.0", "--port=8888"]
