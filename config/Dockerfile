# Built from the repo root: shared/ rides along with the service sources.
FROM python:3.13-slim

WORKDIR /app

COPY config/requirements.txt config/requirements.txt
RUN pip install --no-cache-dir -r config/requirements.txt

ENV PYTHONPATH=/app/shared/src:/app/config/src
COPY shared/ shared/
COPY config/src/ config/src/

CMD ["python", "-m", "config"]
