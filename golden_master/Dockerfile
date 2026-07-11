# Build context is ./golden_master (see docker-compose.yml) — independently
# buildable from the main nse-signal image, no shared code between the two.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . ./golden_master

# Own SQLite DB, own volume — true isolation from nse-signal's data
VOLUME ["/data"]

CMD ["python", "-m", "golden_master.main", "run"]
