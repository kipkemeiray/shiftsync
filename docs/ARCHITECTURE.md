# ShiftSync — Architecture Document

## 1. System Overview

ShiftSync is a multi-location staff scheduling platform built for **Coastal Eats**, a restaurant group
operating 4 locations across 2 time zones (Pacific and Eastern). The system handles workforce scheduling,
real-time notifications, overtime compliance, and shift-swap workflows.

---

## 2. Technology Stack

| Layer             | Technology                                      | Rationale                                                   |
| ----------------- | ----------------------------------------------- | ----------------------------------------------------------- |
| Backend Framework | Django 5.x                                      | Battle-tested, ORM excellence, built-in admin               |
| Real-Time         | Django Channels 4.x + Redis                     | WebSocket support for live schedule updates                 |
| Task Queue        | Celery + Redis                                  | Async notifications, expiry jobs, email simulation          |
| Database          | PostgreSQL 15+                                  | JSONB for audit payloads, row-level locking for concurrency |
| Frontend          | HTMX 2.x + Alpine.js                            | Server-driven UI without a full SPA build pipeline          |
| CSS               | Tailwind CSS (CDN)                              | Rapid, consistent UI                                        |
| Cache             | Redis                                           | Channel layer + Celery broker + Django cache                |
| Deployment        | Docker Compose (local) / Railway (cloud)        | Reproducible environments                                   |
| Timezone Library  | `zoneinfo` (stdlib) + `pytz` for DST edge cases | Correct DST handling                                        |

---

## 3. Application Modules

```
shiftsync/
├── apps/
│   ├── accounts/       # User model, roles, skills, availability
│   ├── locations/       # Location, timezone, staff certifications
│   ├── scheduling/      # Shifts, assignments, constraint engine, swaps
│   ├── notifications/   # Notification model, preference, delivery
│   ├── analytics/       # Fairness reports, overtime projections
│   └── audit/           # Immutable change log
├── core/
│   ├── consumers.py     # WebSocket consumers (Channels)
│   ├── middleware.py     # Timezone middleware
│   ├── permissions.py   # Role-based permission mixins
│   └── validators.py    # Cross-cutting business rule validators
└── templates/
    ├── base.html
    ├── accounts/
    ├── scheduling/
    ├── analytics/
    └── notifications/
```

---

## 4. Data Model Overview

### 4.1 Users & Roles

```
User (AbstractBaseUser)
  ├── role: Enum[ADMIN, MANAGER, STAFF]
  ├── skills: M2M → Skill
  └── managed_locations: M2M → Location  (Managers only)

StaffAvailability
  ├── user: FK → User
  ├── recurrence: Enum[WEEKLY, ONE_OFF]
  ├── day_of_week: 0-6 (for weekly)
  ├── date: Date (for one-off)
  ├── start_time / end_time: Time (in user's preferred timezone)
  └── timezone: CharField  ← The TZ the user entered their availability in
```

### 4.2 Locations

```
Location
  ├── name: str
  ├── timezone: str  (e.g., "America/Los_Angeles")
  └── address: str

LocationCertification
  ├── user: FK → User
  ├── location: FK → Location
  ├── certified_at: DateTime
  └── is_active: bool
```

### 4.3 Scheduling

```
Shift
  ├── location: FK → Location
  ├── required_skill: FK → Skill
  ├── headcount_needed: int
  ├── start_utc / end_utc: DateTime (always UTC in DB)
  ├── is_published: bool
  ├── published_at: DateTime
  └── edit_cutoff_hours: int (default 48)

ShiftAssignment
  ├── shift: FK → Shift
  ├── user: FK → User
  ├── status: Enum[ASSIGNED, SWAP_PENDING, DROPPED, COVERED]
  └── assigned_by: FK → User

SwapRequest
  ├── requester: FK → User (Staff A)
  ├── target: FK → User (Staff B, nullable for drops)
  ├── assignment: FK → ShiftAssignment
  ├── type: Enum[SWAP, DROP]
  ├── status: Enum[PENDING_ACCEPTANCE, PENDING_MANAGER, APPROVED, REJECTED, CANCELLED, EXPIRED]
  ├── target_accepted_at: DateTime
  └── expires_at: DateTime
```

### 4.4 Audit

```
AuditLog
  ├── actor: FK → User
  ├── action: str  (e.g., "shift.assignment.created")
  ├── content_type: FK → ContentType
  ├── object_id: int
  ├── before: JSONB
  ├── after: JSONB
  └── created_at: DateTime  (indexed)
```

---

## 5. Constraint Engine

All scheduling constraint checks live in `scheduling/constraints.py`. Each check returns a
`ConstraintResult(ok: bool, reason: str, suggestions: list[User])`.

**Enforced constraints (in order of check):**

1. Skill match — staff must have the required skill
2. Location certification — staff must be certified (active) at that location
3. Availability — shift window must fall within staff's availability for that day
4. No double-booking — no overlapping shift assignments (even cross-location)
5. Rest period — ≥10 hours between end of last shift and start of this one
6. Daily hours — warn >8h, hard block >12h on a calendar day
7. Weekly hours — warn at 35h, require override at 40h+
8. Consecutive days — warn on day 6, require documented override on day 7

**Concurrency:** Constraint checks use `SELECT FOR UPDATE` on the `ShiftAssignment` table
to prevent TOCTOU races when two managers assign the same staff member simultaneously.

---

## 6. Timezone Handling Strategy

- **Storage:** All datetimes stored as UTC in the database.
- **Display:** Times rendered in the **location's timezone** for shift-related views.
- **Availability input:** Staff enter availability in their local timezone; stored with the timezone label.
- **Overnight shifts:** `end_utc > start_utc` always; a 11pm–3am shift is stored as a single record spanning the date boundary.
- **DST transitions:** When expanding recurring weekly availability, we use `zoneinfo` to correctly handle spring-forward/fall-back. Ambiguous times are flagged to the user.

---

## 7. Real-Time Architecture

```
Browser ←── WebSocket ──→ Django Channels ←──→ Redis Channel Layer
                                  ↑
                            Celery Workers
                         (notifications, expiry)
```

**Channel Groups:**

- `schedule_{location_id}` — broadcast schedule publishes/edits to all staff at a location
- `user_{user_id}` — private channel for swap requests, personal notifications
- `dashboard_admin` — live "on-duty now" updates for admins

**Conflict detection:** When a manager opens a shift assignment modal, they join a `shift_lock_{shift_id}` ephemeral group. If a second manager joins, both receive a `concurrent_edit` warning event.

---

## 8. Deliberate Ambiguity Resolutions

| Ambiguity                           | Decision                                                                                                    | Rationale                            |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| De-certified staff historical data  | Preserve all past assignments; mark certification inactive; block future assignments only                   | Audit integrity                      |
| "Desired hours" vs availability     | Desired hours is a soft target for analytics/fairness scoring; availability is a hard scheduling constraint | Separate concerns                    |
| Consecutive days — short shifts     | Any shift, regardless of duration, counts as a worked day                                                   | Simplest legally-safe interpretation |
| Shift edited after swap approval    | Edit cancels any pending/approved-but-unstarted swap with notification to all parties                       | Consistency over complexity          |
| Location spanning timezone boundary | Location has a single canonical timezone (the primary one); documented limitation                           | Edge case outside scope              |

---

## 9. API Endpoints (HTMX-Driven)

Since the frontend is HTMX, most endpoints return HTML partials. Key patterns:

- `GET  /schedule/{location_id}/week/{iso_week}/` — week grid partial
- `POST /shifts/{id}/assign/` — assign staff, returns inline validation result
- `POST /shifts/{id}/publish/` — publish week
- `POST /swaps/request/` — initiate swap/drop
- `WS   /ws/schedule/{location_id}/` — schedule room
- `WS   /ws/user/` — personal notification stream

---

## 10. Security Considerations

- Role-based permission mixins on every view (`AdminRequired`, `ManagerRequired`, `StaffRequired`)
- Managers are queryset-scoped to their assigned locations (no global querysets exposed)
- Audit log writes are atomic with the operation they log (same DB transaction)
- CSRF on all POST endpoints; WS connections authenticated via session cookie
- No sensitive data in WebSocket messages beyond what the session permits

---

## 11. Testing Strategy

- **Unit tests:** Constraint engine (every rule, every edge case)
- **Integration tests:** Swap workflow state machine, concurrent assignment race conditions
- **Scenario tests:** The 6 evaluation scenarios from the brief, implemented as named test cases
- **Factory Boy** for realistic seed data generation

---

## 12. Known Limitations

- Email delivery is simulated (logged to console/DB, not sent via SMTP)
- Biometric clock-in is not implemented; "on-duty now" is based on shift start/end times
- Mobile push notifications are not implemented (in-app + email simulation only)
- Location timezone-boundary edge case is documented but not handled
