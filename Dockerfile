FROM golang:1.25 AS ant-builder

RUN go install github.com/anthropics/anthropic-cli/cmd/ant@latest

FROM python:3.12-slim

WORKDIR /app

COPY --from=ant-builder /go/bin/ant /usr/local/bin/ant

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY scripts ./scripts
COPY workspace ./workspace

RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
ENV WORKSPACE_PATH=/app/workspace
ENV SQLITE_PATH=/app/data/lightwire.db

RUN mkdir -p /app/data

VOLUME ["/app/data"]

CMD ["python", "-m", "app.server"]
