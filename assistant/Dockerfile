# The dashboard's built-in assistant: Claude Code plus what it needs to run the dashboard's tests.
# Build from the project folder:  docker build -t family-dashboard-assistant:latest -f assistant/Dockerfile .
FROM node:22-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates curl bash util-linux procps \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force
COPY server/requirements.txt /opt/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /opt/requirements.txt
COPY assistant/service.py /opt/assistant/service.py
# "node" is uid 1000. Its home is the volume: sign-in, memory, conversation and the code copy all live there.
USER node
ENV HOME=/home/node
WORKDIR /home/node
EXPOSE 8090
CMD ["python3", "/opt/assistant/service.py"]
