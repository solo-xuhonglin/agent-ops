# Built from the repo root: shared/ rides along with the service sources.
FROM python:3.13-slim

WORKDIR /app

COPY manager/requirements.txt manager/requirements.txt
RUN pip install --no-cache-dir -r manager/requirements.txt

ENV PYTHONPATH=/app/shared/src:/app/manager/src
COPY shared/ shared/
COPY manager/src/ manager/src/

CMD ["python", "-m", "manager"]
