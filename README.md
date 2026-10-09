# WatchGrid

Web and application monitoring platform: scheduled uptime and latency checks for
websites, APIs and health endpoints; confirmed-outage incidents with automatic recovery;
email and webhook alerts; and error reporting from your own applications. Built with
Python, Django, Django REST Framework, PostgreSQL, Celery and Redis. The dashboard is
rendered server-side with Django templates (no JavaScript framework, no build step).

Monitoring is server-driven: checks keep running when every browser is closed.

## Repository layout

```
manage.py
config/              settings (base, development, staging, production, test), urls, celery, wsgi/asgi
api/v1/              REST API routing: router.py (resources) and urls.py (auth, ingestion, dashboard)
apps/                one Django app per domain; each owns models, services, serializers,
  accounts/          REST views (api.py), HTML views (views.py, urls.py) and templates/<app>/
  organizations/     tenants, memberships, invitations, team pages
  projects/
  monitors/          monitor configuration, state machine, response-time chart geometry
  checks/            probe execution (SSRF-safe), scheduler, workers, platform health
  incidents/         incident lifecycle and timeline
  errors/            error ingestion, fingerprinting, grouping
  notifications/     alert policies, maintenance windows, email/webhook delivery
  analytics/         uptime/latency statistics, rollups, retention, overview and reports
  api_keys/          project ingestion keys (hashed, shown once)
  audit/             append-only audit log
common/              cross-cutting code: permissions (roles, tenancy), security (network
                     policy, safe HTTP client, redaction, encryption), exceptions,
                     constants, utilities (web view plumbing, forms, template filters)
templates/           base.html, dashboard_base.html, includes/, error pages, form_page.html
static/              css/ (tokens, app, self-hosted fonts), js/ (progressive enhancement), images/
media/               user-uploaded files (none yet; kept out of git)
tests/               unit/, integration/, security/, performance/, e2e/ (+ conftest, factories)
fixtures/development/ demo workspace loaded by `manage.py seed_demo`
docker/              django/ (image + entrypoint), celery/ (worker, beat), nginx/ (edge proxy)
requirements/        base, development, staging, production
docs/                design notes and the specification text
```

## Run it locally (no Docker, no Redis)

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements/development.txt     # Windows; use .venv/bin on macOS/Linux

# Development defaults to an in-process cache and inline Celery tasks (no Redis).
python manage.py migrate
python manage.py seed_demo              # demo@watchgrid.local / watchgrid-demo-2026 (DEBUG only)
python manage.py runserver              # http://localhost:8000
python manage.py run_scheduler          # second terminal: dispatches due checks every 15 s
```

To use Redis and separate Celery workers locally, set `WATCHGRID_LOCAL_NO_REDIS=false` before starting Django (`$env:WATCHGRID_LOCAL_NO_REDIS="false"` in PowerShell).

`run_scheduler` stands in for Celery beat during development. The demo monitors point at
public IANA example domains, so the seeded "outage" recovers once real checks run.
Create a platform administrator with `python manage.py createsuperuser` (Django admin at
`/admin/`).

## Run the full stack (Docker)

```bash
cp .env.example .env        # set DJANGO_SECRET_KEY, FIELD_ENCRYPTION_KEY, email settings
docker compose up --build   # http://localhost:8080
docker compose run --rm web python manage.py createsuperuser
```

Services follow Section 25 of the specification: `nginx` → `web` (Gunicorn), PostgreSQL,
Redis, one Celery worker per queue (`monitoring`, `notifications`, `ingestion`,
`analytics`) and exactly one `celery_beat`. Only the monitoring and notification workers
can reach the internet. In production also block private ranges and cloud metadata
addresses at the network level, terminate TLS in front of Nginx, and set
`SECURE_SSL_REDIRECT=true` and `SECURE_COOKIES=true`.

## Deploy on Render

Use the Python runtime and the same repository for all three application services
below. Leave Root Directory blank. Create Render Postgres and Render Key Value in
the same region as the application services, and use their internal connection URLs.
Set Key Value's eviction policy to `noeviction` for the Celery task queue.

### Prepare static-file serving

The Docker setup uses Nginx for static files. The native Render setup below requires
WhiteNoise instead. Before deploying, add `whitenoise` to
`requirements/production.txt`, and add this entry to `MIDDLEWARE` in
`config/settings/base.py` immediately after `django.middleware.security.SecurityMiddleware`:

```python
"whitenoise.middleware.WhiteNoiseMiddleware",
```

Commit these changes before deployment. The existing production manifest storage can
remain in place. Without this preparation, the website's CSS and JavaScript will not
be served by Gunicorn.

### Shared environment variables

Set these on the web service and both background workers. Replace every placeholder;
do not commit actual secrets to the repository.

```dotenv
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<generated-secret>
FIELD_ENCRYPTION_KEY=<generated-encryption-key>
DATABASE_URL=<Render-Postgres-internal-URL>
REDIS_URL=<Render-Key-Value-internal-URL>
ALLOWED_HOSTS=your-app.onrender.com
CSRF_TRUSTED_ORIGINS=https://your-app.onrender.com
SITE_URL=https://your-app.onrender.com
SECURE_SSL_REDIRECT=true
SECURE_COOKIES=true
TRUSTED_PROXY_COUNT=1
```

Generate the secret and encryption key locally from the project folder (PowerShell):

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use the same generated values across all three services. Add your SMTP settings for
verification, password resets, invitations, and monitoring alerts:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=<SMTP-host>
EMAIL_PORT=587
EMAIL_HOST_USER=<SMTP-user>
EMAIL_HOST_PASSWORD=<SMTP-password>
EMAIL_USE_TLS=true
DEFAULT_FROM_EMAIL=WatchGrid <alerts@your-domain.com>
```

Use your provider's actual SMTP settings. When adding a custom domain, update
`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and `SITE_URL` accordingly.

### Website: Web Service

Build Command:

```bash
pip install -r requirements/production.txt && python manage.py collectstatic --noinput
```

Pre-Deploy Command (available on paid web services):

```bash
python manage.py migrate --noinput
```

Start Command:

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2 --access-logfile -
```

Set Health Check Path to `/health/live/`. If the service has no pre-deploy command
field, use this start command instead so migrations complete before serving requests:

```bash
python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2 --access-logfile -
```

### Monitoring: Background Worker

Build Command:

```bash
pip install -r requirements/production.txt
```

Start Command:

```bash
celery -A config worker --loglevel info --concurrency 2 --queues monitoring,notifications,ingestion,analytics,default
```

This initial setup uses one worker service for all queues. Split the queues into
separate worker services when more capacity or workload isolation is needed.

### Scheduler: Background Worker

Build Command:

```bash
pip install -r requirements/production.txt
```

Start Command:

```bash
celery -A config beat --loglevel info --schedule /tmp/celerybeat-schedule
```

Keep exactly one active scheduler. Do not also run `manage.py run_scheduler` on
Render; Celery Beat replaces it. The schedule file in `/tmp` is recreated after
restarts; monitor configuration and results remain in PostgreSQL.

### First deployment and verification

Deploy the website and complete the initial database migrations before starting the
worker and scheduler. In the website's Render Shell, create an administrator:

```bash
python manage.py createsuperuser
```

Check deployment settings and worker connectivity from the Render Shell:

```bash
python manage.py check --deploy
celery -A config inspect ping --timeout 10
```

Open the website, create a monitor, and confirm that its Last checked timestamp
updates after a scheduled check. If it remains Never, inspect the scheduler and
worker logs and confirm that all services use the same database and Key Value URLs.
Render runs the services continuously; your local computer does not need to stay on.

Reference: [Render's Django deployment guide](https://render.com/docs/deploy-django)
and [Celery worker guide](https://render.com/docs/deploy-celery).

## Tests

```bash
pytest                          # all levels, in-memory SQLite (about 10 s)
pytest tests/security           # one level
TEST_DATABASE_URL=postgres://user:pass@localhost:5432/watchgrid pytest   # as CI does
ruff check . && ruff format --check .
```

| Level | Covers |
|---|---|
| `unit/` | state machine, scheduling, fingerprinting, chart geometry |
| `integration/` | REST API, check pipeline (MON-001…005), incidents, notifications (NOT-001), error ingestion (ERR-001/002), analytics, emailed links |
| `security/` | SSRF at configuration and execution time incl. redirects and DNS rebinding (SEC-001/002), tenant isolation (TEN-001), role matrix, CSP compliance, proxy headers |
| `performance/` | bounded scheduler batches (PERF-001), per-page query budgets; `locustfile.py` for load tests against staging |
| `e2e/` | dashboard workflows through the HTML pages, and a sign-up → outage → incident → recovery journey |

## Interfaces

- Dashboard: `/` (overview), `/monitors/`, `/incidents/`, `/errors/`, `/reports/`, `/team/`, `/integrations/`, `/settings/`
- REST API: `/api/v1/` with interactive documentation at `/api/v1/docs/`
- Error ingestion: `POST /api/v1/events/errors/` with `X-Project-Key` or `Authorization: Bearer <key>`
- Health: `/health/live/`, `/health/ready/`; operator status (staff only): `/api/v1/platform/status/`

## Architecture decisions

These record the choices the specification leaves to implementation (Section 6.3).

1. **Modular monolith + Celery execution plane.** Django serves the dashboard and API;
   probes, notifications, ingestion and analytics run on separate Celery queues so a slow
   email provider never delays checks.
2. **Session authentication, server-rendered UI.** The dashboard and the API share
   Django sessions with CSRF protection; there is no token store to secure. HTML forms
   validate with the same serializers and call the same service functions as the API.
3. **Exactly-once intent.** The scheduler advances `next_check_at` while holding row locks
   (`SKIP LOCKED`); each check carries a deterministic execution key with a unique
   constraint, so redelivered tasks are no-ops. One active incident per monitor and cause
   is enforced by a partial unique index. Notifications use a deduplication ledger.
4. **SSRF defence in depth.** URLs are validated at configuration time, and the HTTP
   client resolves, validates and connects to the same address on every request and
   redirect hop, defeating DNS rebinding.
5. **Tenant isolation by queryset.** Every lookup goes through `for_user()` scopes, so
   other tenants' objects return 404 rather than 403. Role capabilities follow Section
   10.2. "Limited" developer rights mean: edit but not create or archive projects; create
   and edit but not delete alert policies; project-level audit entries only.
6. **Uptime is check-based.** Uptime is the share of available (healthy or slow) checks
   among scheduled checks. Manual tests and platform errors are excluded, and incident
   downtime is reported alongside. Ranges over 7 days read hourly rollups.
7. **Strict Content-Security-Policy.** Templates use no inline styles or scripts (enforced
   by `tests/security/test_content_security_policy.py`).

Operational procedures (daily checks, stuck queues, recovery) follow Section 30 of the
specification; `/api/v1/platform/status/` reports scheduler lag, per-queue heartbeats,
overdue monitors and notification failures for those checks.
