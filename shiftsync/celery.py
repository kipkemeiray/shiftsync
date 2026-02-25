"""
Celery application configuration for ShiftSync.

Tasks are auto-discovered from each Django app's tasks.py module.
Two queues are defined:
  - default: general background work
  - notifications: user-facing async notifications (isolated for monitoring)
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shiftsync.settings.local")

app = Celery("shiftsync")

# Read configuration from Django settings, namespaced under CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()