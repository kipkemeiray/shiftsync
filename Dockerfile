ARG PYTHON_VERSION=3.13-slim

FROM python:${PYTHON_VERSION}


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 

# install psycopg2 dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /code

WORKDIR /code

COPY requirements.txt /tmp/requirements.txt
RUN set -ex && \
    pip install --upgrade pip && \
    pip install -r /tmp/requirements.txt && \
    rm -rf /root/.cache/

COPY . /code

ENV SECRET_KEY "LI8pplZCUNzP6Imuz1VgESuhB1gdRRQz5b1vxnoLnO3pQYEeTG" 

ENV DJANGO_SETTINGS_MODULE "shiftsync.settings.production"

RUN python manage.py collectstatic --noinput

# entrypoint handles migrations + process dispatch
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]