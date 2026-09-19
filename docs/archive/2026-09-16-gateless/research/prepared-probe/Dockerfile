FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates
RUN npm install -g @openai/codex@0.154.0
USER node
WORKDIR /workspace
