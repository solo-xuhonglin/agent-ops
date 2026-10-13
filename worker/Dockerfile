FROM python:3.13-slim

WORKDIR /app

ENV PYTHONPATH=/app/src
COPY src/ src/

CMD ["python", "-m", "worker"]
