FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUTF8=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/

CMD ["python", "-m", "src.worker"]
