# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY manage.py ./
COPY config/ ./config/
COPY fitness/ ./fitness/
# Example graph; the chart's init container seeds the writable data volume from it.
COPY data/ ./data/

RUN DJANGO_DEBUG=0 python manage.py collectstatic --noinput

# Numeric UID so Kubernetes can enforce runAsNonRoot.
USER 1000

EXPOSE 8080

ENV DJANGO_DEBUG=0

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "60"]
