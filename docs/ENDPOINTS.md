# ShiftSync Endpoints Architecture

## Accounts & Authentication

- GET /accounts/login/ → login page
- POST /accounts/login/ → authenticate user
- POST /accounts/logout/ → end session
- GET /accounts/register/ → registration form
- POST /accounts/register/ → create new user
- GET /accounts/profile/ → view profile
- POST /accounts/profile/update/ → update profile info
- POST /accounts/password/change/ → change password
- POST /accounts/password/reset/ → reset password

## Locations

- GET /locations/ → list all locations
- GET /locations/<id>/ → location detail
- POST /locations/<id>/certify/ → certify user for location
- GET /locations/<id>/staff/ → staff roster

## Scheduling

- GET /shifts/ → list shifts
- GET /shifts/<id>/ → shift detail
- POST /shifts/<id>/assign/ → assign staff
- POST /shifts/<id>/publish/ → publish schedule
- POST /assignments/<id>/swap/ → create swap request
- POST /assignments/<id>/drop/ → drop shift
- POST /overrides/<id>/create/ → manager override
- GET /availability/ → staff availability
- POST /availability/update/ → update availability

## Notifications

- GET /notifications/ → list notifications
- POST /notifications/<id>/read/ → mark as read
- POST /notifications/read-all/ → mark all as read

## Audit

- GET /audit/ → list audit logs
- GET /audit/<id>/ → audit detail

## HTMX + Alpine Integration

- Login form: hx-post="/accounts/login/"
- Shift dashboard: hx-get="/shifts/" hx-trigger="load"
- Swap request modal: Alpine modal + HTMX post
- Notifications dropdown: hx-get="/notifications/" hx-trigger="every 10s"
