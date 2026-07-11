# Multi-arch base image: works on Pi (arm64) and x86 alike
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# SQLite lives on a mounted volume at /data
VOLUME ["/data"]

# Dashboard (Phase 2 §2.1) — same container, same process as the scheduler
EXPOSE 8000

CMD ["python", "-m", "app.main", "run"]
