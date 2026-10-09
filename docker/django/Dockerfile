# One image for the Django web process, the Celery workers and Celery beat; the
# compose file chooses the process per service. Build from the repository root:
#
#   docker build -f docker/django/Dockerfile -t watchgrid-django .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system watchgrid \
    && useradd --system --gid watchgrid --home-dir /app --shell /usr/sbin/nologin watchgrid

WORKDIR /app
COPY requirements/ requirements/
RUN pip install -r requirements/production.txt

COPY --chown=watchgrid:watchgrid . .
COPY --chmod=0755 docker/django/entrypoint.sh /usr/local/bin/watchgrid-entrypoint
COPY --chmod=0755 docker/celery/worker.sh /usr/local/bin/watchgrid-worker
COPY --chmod=0755 docker/celery/beat.sh /usr/local/bin/watchgrid-beat
RUN mkdir -p /app/staticfiles /app/media && chown watchgrid:watchgrid /app/staticfiles /app/media

USER watchgrid
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live/', timeout=4).status == 200 else 1)"

ENTRYPOINT ["watchgrid-entrypoint"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "30", \
     "--access-logfile", "-", \
     "--forwarded-allow-ips", "*"]
