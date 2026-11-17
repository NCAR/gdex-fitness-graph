# syntax=docker/dockerfile:1

FROM python:3.12-slim

# HOME: UID 1000 has no home directory; git needs a writable one.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    GIT_ASKPASS=/usr/local/bin/git-askpass

# git: per-user graphs are committed and pushed to the data repo (see FITNESS_GIT_SYNC).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=755 docker/git-askpass.sh /usr/local/bin/git-askpass

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY manage.py ./
COPY config/ ./config/
COPY fitness/ ./fitness/
# Example graph (not used at runtime; new user graphs start blank).
COPY --chmod=a+rX data/ ./data/

RUN DJANGO_DEBUG=0 python manage.py collectstatic --noinput

# Numeric UID so Kubernetes can enforce runAsNonRoot.
USER 1000

EXPOSE 8080

ENV DJANGO_DEBUG=0

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "60"]
