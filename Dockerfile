FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_PATCHTRACE_PUBLIC_MODE=true \
    PATCHTRACE_PUBLIC_MODE=true \
    PATH="/opt/patchtrace-venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git ca-certificates curl bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python3 -m venv /opt/patchtrace-venv \
    && /opt/patchtrace-venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /opt/patchtrace-venv/bin/pip install --no-cache-dir -r /app/backend/requirements.txt

COPY frontend/package.json /app/frontend/package.json
WORKDIR /app/frontend
RUN npm install

WORKDIR /app
COPY . /app
RUN cd /app/frontend && npm run build

EXPOSE 10000
CMD ["/bin/bash", "/app/scripts/start_container.sh"]
