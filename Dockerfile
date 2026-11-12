# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci && ln -s /build/frontend/node_modules /build/node_modules
COPY frontend/ ./
COPY services/gpt-register/web/src/ /build/services/gpt-register/web/src/
RUN npm run build

FROM golang:1.25-bookworm AS postpayment
WORKDIR /build/postpayment
COPY go-post-payment/go.mod go-post-payment/go.sum ./
RUN go mod download
COPY go-post-payment/ ./
RUN go test -race -timeout 60s ./...
RUN CGO_ENABLED=0 go build -trimpath -o /post-payment .

FROM golang:1.25-bookworm AS cslive
WORKDIR /build/cslive
COPY cs-live-source/go.mod cs-live-source/go.sum ./
RUN go mod download
COPY cs-live-source/ ./
RUN CGO_ENABLED=0 go test ./...
RUN CGO_ENABLED=0 go build -trimpath -o /cs_live .

FROM node:22-bookworm-slim AS registration
WORKDIR /build/registration
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY services/gpt-register/package.json services/gpt-register/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY services/gpt-register/src/ ./src/
COPY services/gpt-register/docker/ ./docker/
COPY services/gpt-register/integration/ ./integration/
COPY services/gpt-register/gpt-re.config.example.json ./

FROM golang:1.25-bookworm AS refundcore
WORKDIR /build/refund-core
COPY services/refund-core/go.mod services/refund-core/go.sum ./
RUN go mod download
COPY services/refund-core/internal/ ./internal/
COPY services/refund-core/cmd/ ./cmd/
RUN go test ./... && CGO_ENABLED=0 go build -trimpath -o /refund-core ./cmd/bridge

FROM node:22-bookworm-slim AS refunddeps
WORKDIR /build/runtime/manager_oauth
COPY services/refund-core/runtime/manager_oauth/package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22-bookworm-slim AS engineui
WORKDIR /build/web
COPY services/gpt-register/web/package.json services/gpt-register/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY services/gpt-register/web/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=18970 LISTEN_HOST=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home app
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY services/refund-core/runtime/requirements.txt /tmp/refund-requirements.txt
RUN pip install --no-cache-dir -r /tmp/refund-requirements.txt
COPY --chown=app:app services/refund-core/runtime/ ./services/refund-core/runtime/
COPY --chown=app:app services/refund-core/integration/ ./services/refund-core/integration/
COPY --from=refunddeps --chown=app:app /build/runtime/manager_oauth/node_modules/ ./services/refund-core/runtime/manager_oauth/node_modules/
COPY --from=refundcore --chown=app:app /refund-core ./bin/refund-core
COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app generator.py ./
COPY --chown=app:app scripts/start_app.py scripts/serve.py scripts/recover_post_payment.py ./scripts/
COPY --from=frontend --chown=app:app /build/webui/ ./webui/
COPY --from=postpayment --chown=app:app /post-payment ./bin/post-payment
COPY --from=cslive --chown=app:app /cs_live ./cs-live-source/cs_live
COPY --from=registration /usr/local/bin/node /usr/local/bin/node
COPY --from=registration --chown=app:app /build/registration/ ./registration/
COPY --from=engineui --chown=app:app /build/web/dist/ ./registration/web/dist/
RUN mkdir -p /app/registration/.runtime && chown app:app /app/registration/.runtime
ARG VCS_REF=local
LABEL org.opencontainers.image.source="https://github.com/yigerende/StarBridge" \
      org.opencontainers.image.title="StarBridge" \
      org.opencontainers.image.description="Subscription, registration and refund workspace" \
      org.opencontainers.image.revision=$VCS_REF
USER app
EXPOSE 18970
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18970/health/ready', timeout=4)"
CMD ["python", "scripts/start_app.py"]
