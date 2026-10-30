FROM node:22-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv git bubblewrap libseccomp2 ca-certificates && ln -sf /usr/bin/python3 /usr/local/bin/python && npm install -g @openai/codex@0.165.0 && rm -rf /var/lib/apt/lists/*
COPY bananloop /opt/bananloop
COPY frozen_portfolio.json /opt/frozen_portfolio.json
COPY agent-profiles.toml /opt/agent-profiles.toml
COPY tests /opt/verification_tests
RUN python3 -m pip install --break-system-packages --no-cache-dir -e /opt/bananloop pytest && PYTHONPATH=/opt/bananloop/src python3 -m pytest -q /opt/verification_tests/test_process.py /opt/verification_tests/test_workspace.py
RUN mkdir -p /home/node/.codex && chown -R node:node /home/node
COPY run_campaign.sh /usr/local/bin/run-campaign
RUN chmod 755 /usr/local/bin/run-campaign
ENV HOME=/home/node CODEX_HOME=/home/node/.codex
USER node
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/run-campaign"]