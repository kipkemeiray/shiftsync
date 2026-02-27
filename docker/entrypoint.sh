#!/bin/sh
# docker/entrypoint.sh
#
# Dispatches to the correct process based on the FLY_PROCESS_GROUP env var
# set by Fly.io's [processes] configuration in fly.toml.
#
# Process groups:
#   web       → run migrations then start daphne (ASGI + WebSockets)
#   worker    → start Celery worker
#   beat      → start Celery beat scheduler
#   (empty)   → local dev default: start web

set -e

PROCESS="${FLY_PROCESS_GROUP:-web}"

echo "==> ShiftSync starting as: $PROCESS"

case "$PROCESS" in
  web)
    echo "==> Running migrations..."
    python manage.py migrate --noinput

    echo "==> Starting daphne (ASGI)..."
    exec daphne \
      -b 0.0.0.0 \
      -p 8000 \
      --access-log - \
      shiftsync.asgi:application
    ;;

  worker)
    echo "==> Starting Celery worker..."
    exec celery \
      -A shiftsync \
      worker \
      --loglevel=info \
      -Q default,notifications \
      --concurrency=2
    ;;

  beat)
    echo "==> Starting Celery beat..."
    exec celery \
      -A shiftsync \
      beat \
      --loglevel=info \
      --scheduler django_celery_beat.schedulers:DatabaseScheduler
    ;;

   fly_app_release_command)
    echo "==> Running release command: python manage.py migrate --noinput"
    exec python manage.py migrate --noinput
    ;;

  *)
    echo "ERROR: Unknown FLY_PROCESS_GROUP: $PROCESS"
    echo "Valid values: web, worker, beat"
    exit 1
    ;;
esac