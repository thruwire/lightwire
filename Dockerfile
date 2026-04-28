FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY scripts ./scripts
COPY workspace ./workspace

RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
ENV WORKSPACE_PATH=/app/workspace
ENV SQLITE_PATH=/app/data/thruflow.db

RUN mkdir -p /app/data

VOLUME ["/app/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
