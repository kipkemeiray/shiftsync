# ShiftSync 🗓️

> Multi-Location Staff Scheduling Platform for Coastal Eats Restaurant Group

[![CI](https://github.com/your-org/shiftsync/actions/workflows/ci-deploy.yml/badge.svg)](https://github.com/your-org/shiftsync/actions)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://python.org)
[![Django](https://img.shields.io/badge/django-5.x-green)](https://djangoproject.com)
[![Deployed on Fly.io](https://img.shields.io/badge/deployed%20on-fly.io-8b5cf6)](https://fly.io)

---

## Table of Contents

1. [Quick Start (Local)](#quick-start-local)
2. [Deploy to Fly.io](#deploy-to-flyio)
3. [Architecture](#architecture)
4. [Test Credentials](#test-credentials)
5. [Evaluation Scenarios](#evaluation-scenarios)
6. [Design Decisions](#design-decisions)
7. [Known Limitations](#known-limitations)

---

## Quick Start (Local)

### With Docker (Recommended)

```bash
git clone https://github.com/your-org/shiftsync.git
cd shiftsync

cp .env.example .env          # review and adjust if needed

docker compose up --build     # starts web, postgres, redis, celery, celery-beat

# In another terminal:
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_data
```

Visit **http://localhost:8000**

### Without Docker

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

redis-server &   # Redis must be running

export DATABASE_URL=postgres://shiftsync:shiftsync@localhost:5432/shiftsync
export REDIS_URL=redis://localhost:6379/0
export SECRET_KEY=local-dev-key
export DJANGO_SETTINGS_MODULE=shiftsync.settings.local

python manage.py migrate
python manage.py seed_data
python manage.py runserver

# Separate terminals:
celery -A shiftsync worker -l info -Q default,notifications
celery -A shiftsync beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

---

## Deploy to Fly.io

See [docs/DEPLOY_FLY.md](docs/DEPLOY_FLY.md) for the full walkthrough. TL;DR:

```bash
# One-time setup
fly launch --no-deploy --name shiftsync --region iad
fly postgres create --name shiftsync-db --region iad
fly postgres attach shiftsync-db
fly redis create --name shiftsync-redis --region iad

fly secrets set SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(50))')"
fly secrets set DJANGO_SETTINGS_MODULE="shiftsync.settings.production"
fly secrets set CSRF_TRUSTED_ORIGINS="https://shiftsync.fly.dev"
fly secrets set ALLOWED_HOSTS="shiftsync.fly.dev"

# Deploy (runs migrations automatically via release_command)
fly deploy

# Seed demo data
fly ssh console -C "python manage.py seed_data"
```

**CI/CD:** Push to `main` → GitHub Actions runs tests → deploys to Fly on green.
Requires `FLY_API_TOKEN` set in GitHub repo secrets (`fly tokens create deploy`).

**Architecture on Fly:**
```
Internet → Fly Proxy (TLS) ─┬─ [web] daphne ASGI (HTTP + WebSockets)
                             ├─ [worker] Celery worker
                             └─ [beat] Celery beat
           Fly Postgres ─────── DATABASE_URL
           Upstash Redis ─────── REDIS_URL (Channels + Celery broker)
```

---

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full breakdown.

**Stack:**
- **Django 5** + **Django Channels 4** (WebSockets via daphne ASGI)
- **PostgreSQL** (primary store; JSONB audit log)
- **Redis** (Channels layer + Celery broker)
- **HTMX 2** + **Alpine.js** (reactive UI, no SPA build pipeline)
- **Celery** (async notifications, swap/drop expiry)
- **Tailwind CSS** (utility-first styling via CDN)

---

## Test Credentials

After `python manage.py seed_data`:

### Admin
| Email | Password |
|---|---|
| `admin@coastaleats.com` | `ShiftSync2026!` |

### Managers
| Email | Password | Locations |
|---|---|---|
| `mgr.westside@coastaleats.com` | `ShiftSync2026!` | Westside (PT), Marina (PT) |
| `mgr.eastcoast@coastaleats.com` | `ShiftSync2026!` | Downtown (ET), Harbor (ET) |

### Staff
| Email | Password | Skills | Locations |
|---|---|---|---|
| `alice@coastaleats.com` | `ShiftSync2026!` | bartender, server | Westside, Marina |
| `bob@coastaleats.com` | `ShiftSync2026!` | line cook | Westside |
| `carol@coastaleats.com` | `ShiftSync2026!` | server, host | Downtown, Harbor |
| `david@coastaleats.com` | `ShiftSync2026!` | bartender | Downtown |
| `eve@coastaleats.com` | `ShiftSync2026!` | line cook, server | All 4 |
| `frank@coastaleats.com` | `ShiftSync2026!` | host, busser | Marina |
| `grace@coastaleats.com` | `ShiftSync2026!` | server, expo | Harbor, Downtown |
| `henry@coastaleats.com` | `ShiftSync2026!` | bartender, expo | Westside, Downtown |

> Seed data includes pre-built overtime trap (Bob at 38h before weekend),
> fairness imbalance (Alice gets all premium shifts), and a pending swap request.

---

## Evaluation Scenarios

### 1. The Sunday Night Chaos
Dashboard → "On-Duty Now" → locate the uncovered 7pm shift → **"Find Coverage"**
→ system shows qualified, non-overtime staff sorted by current weekly hours →
one-click assign → staff notified via WebSocket instantly.

### 2. The Overtime Trap
The week grid shows Bob's hours column updating live as shifts are added.
At 38h, attempting to add another shift triggers a **hard block modal** showing
current hours + proposed hours + which specific assignments contribute.
The **"What-If"** button simulates without committing.

### 3. The Timezone Tangle
Carol enters "9am–5pm" availability in PT. That stored as `09:00 America/Los_Angeles`.
A Downtown (ET) 9am shift = 14:00 UTC; Carol's 9am PT = 17:00 UTC.
The constraint engine compares UTC → Carol is correctly blocked from the ET morning shift.

### 4. The Simultaneous Assignment
Both managers open the same shift's assignment modal → both join `shift_editing_{id}`
WebSocket group → second manager sees: *"⚠ Jennifer Park is also editing this shift."*
If both submit simultaneously, `SELECT FOR UPDATE` ensures one gets a `409 Conflict`.

### 5. The Fairness Complaint
Analytics → Fairness Report → select staff → date range → **Premium Shift Distribution**
chart shows Friday/Saturday evening shift counts vs. location average, with a fairness
score showing deviation from equal share. Exportable as CSV.

### 6. The Regret Swap
Staff A cancels their swap at any point before manager approval:
status → `CANCELLED`, Staff B notified, manager approval request withdrawn,
original assignment reverts to `ASSIGNED`. No manager action required.

---

## Design Decisions

| Ambiguity | Decision |
|---|---|
| De-certified staff history | Preserve all past assignments; block future only |
| Desired hours vs. availability | Availability = hard constraint; desired hours = analytics soft target |
| Consecutive days (short shifts) | Any shift counts as a worked day (conservative, legally safe) |
| Shift edited after swap approval | Material edit cancels the swap; all parties notified |
| Border-spanning location timezone | Single canonical timezone per location; documented limitation |

---

## Known Limitations

- Email delivery is **simulated** (console backend — check Docker logs)
- "On-duty now" is **time-based** (no biometric clock-in)
- Audit log exports as **CSV only** (no PDF in v1)
- Locations spanning timezone boundaries not supported (single canonical TZ per location)

---

## Running Tests

```bash
python manage.py test                                    # all tests
python manage.py test apps.scheduling.tests              # scheduling suite
coverage run manage.py test && coverage report           # with coverage
```
