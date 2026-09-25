FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY beets_mvp ./beets_mvp

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/inbox /data/library /data/state \
    && chown -R app:app /data /app
USER app

ENV INBOX_PATH=/data/inbox \
    LIBRARY_PATH=/data/library \
    STATE_PATH=/data/state
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=1", "--threads=4", "beets_mvp:create_app()"]
