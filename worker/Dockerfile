# Built from the repo root: shared/ rides along with the service sources.
FROM python:3.13-slim

WORKDIR /app

COPY worker/requirements.txt worker/requirements.txt
RUN pip install --no-cache-dir -r worker/requirements.txt

ENV PYTHONPATH=/app/shared/src:/app/worker/src
COPY shared/ shared/
COPY worker/src/ worker/src/

CMD ["python", "-m", "worker"]
