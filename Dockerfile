FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /service
COPY requirements.txt .
RUN --mount=type=secret,id=pip_ca \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi && \
    python -m pip install -r requirements.txt
COPY app ./app

EXPOSE 8000
CMD ["sh", "-c", "python -m app.database.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
