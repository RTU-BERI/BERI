# S1-P4 session authentication — local demonstration

Implements BERI-106-A/B on the existing Account/AccountBackend after PR #7.
This is a local Desktop demo, not production-ready SMS authentication. Canonical
models, managers, validators, permission scopes and all 11 project migrations
remain unchanged. The source contract/specifications are kept outside Git.

## HTTP contract

| Method | Path | Success |
|---|---|---|
| GET | `/api/auth/csrf/` | 200 `{"detail":"csrf_ready"}` and csrftoken cookie |
| POST | `/api/auth/register/` | 201 `{"user":...}`, no automatic login |
| POST | `/api/auth/login/` | 200 `{"authenticated":true,"user":...}` |
| POST | `/api/auth/logout/` | 204, empty body, including repeated anonymous logout |
| GET | `/api/auth/me/` | 200; anonymous is `{"authenticated":false,"user":null}` |

Trailing slashes are required. Other methods receive 405, unknown auth paths
receive 404. Auth responses are JSON and no-store. Existing category/Admin error
policies are not globally replaced. Invalid Host is rejected by Django.

User JSON includes only id, phone, account_type and phone_verified. The last field
is derived from phone_verified_at and does not add a database column.

Registration accepts exactly phone/password/password_confirm. Login accepts
exactly phone/password. Unknown/service keys are rejected, not silently ignored.
Registration uses the canonical normalizer, Django password validation and
AccountManager. It forces customer, status='registered', is_active=True, no staff,
no superuser, no groups/permissions and phone_verified_at=NULL. Passwords are
hashed; no SMS is sent. Concurrent duplicate phones return 409, not 500.

Status is opaque: 'registered' is an explicit initial value for this operation,
not a model default or an enum. Only current is_active controls ordinary access.
The existing employee login is supported; login does not itself grant PV rights.
Outside the approved local demo, a customer with a correct password but no phone
verification receives 403 phone_unverified. A wrong password, unknown phone or
inactive account always receives the same 401 invalid_credentials.

Errors use `{"error":{"code":"...","message":"...","fields":{...}}}`;
fields is optional. Codes are validation_error (400), invalid_credentials (401),
phone_unverified/csrf_failed (403), phone_taken (409), rate_limited (429),
registration_unavailable (503), not_found (404) and method_not_allowed (405).

## Local registration safety gate

Registration is OFF by default. It requires all of:

- BERI_DEMO_REGISTRATION_ENABLED=True;
- DEBUG=True;
- a nonempty ALLOWED_HOSTS limited to localhost/127.0.0.1/[::1];
- REMOTE_ADDR is loopback (X-Forwarded-For is not trusted);
- the running server actually bound a loopback socket through run_local_demo.

The last condition is a process-local confirmation set by the server after
checking its socket, not an env flag, header or client-provided Host. Standard
runserver or a WSGI deployment does not set it: registration remains unavailable.
Tests use an explicit override of that internal confirmation. The dedicated
launcher refuses 0.0.0.0 and nonlocal settings. Do not tunnel or expose the demo
through a public reverse proxy. Production release remains blocked pending SMS,
all-channel verification, shared atomic limiting and a separate security audit.

Use an isolated PostgreSQL environment for development/testing. Do not run the
commands below against the owner's main beri database or existing shared volumes.
Supply private DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT and DJANGO_SECRET_KEY
for your own disposable PostgreSQL server, with canonical migrations applied.

Local HTTP values (for this disposable environment only):

```dotenv
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_COOKIE_SECURE=False
BERI_DEMO_REGISTRATION_ENABLED=True
```

From backend:

```bash
python manage.py run_local_demo 127.0.0.1:8000 --noreload
```

No existing .env is overwritten by this change. The example file contains no
credentials. Secure cookies default to true and are always true when DEBUG=False;
the local HTTP override does not disable HTTPS enforcement in production settings.

## Cookies, CSRF and limits

Sessionid is HttpOnly/SameSite=Lax. CSRF is a separate JS-readable cookie.
All three POST endpoints explicitly enforce Django CSRF even for anonymous users
and disabled registration, before invoking the operation. Logout also checks JSON.
Login uses authenticate() and login(), rotates the session/CSRF using Django,
and logout() invalidates only the current session. Me resolves the current session;
old passwords and inactive accounts cannot retain access through a stale session.
SessionAuthentication is the sole auth mechanism on these new endpoints. Basic
and Bearer/JWT credentials are not accepted. Existing guards/Admin are retained.

Local one-process limits count successful and failed processed attempts:
login 10/minute per REMOTE_ADDR and 10/hour per canonical phone; registration
3/hour per REMOTE_ADDR. Keys use HMAC, not plaintext phones. A thread lock makes
the demo counter update consistent inside this one process. Expired sliding-window
entries are discarded. 429 includes Retry-After. CSRF-rejected requests do not
reach the operation limiter. Missing/invalid phone requests still count toward
the IP limit; requests with a usable phone also count toward its phone limit.

The auth_demo LocMemCache is not a distributed security mechanism. Multiple
workers/processes/restarts do not share counters, and production brute-force/DoS
protection is explicitly deferred. No new database table is used for throttling.

## Frontend and ownership

The only frontend is frontend/: React + TypeScript + Vite, npm lockfile,
react-router-dom, Context API and native fetch. Node >=22.12 is required by this
bootstrap's pinned toolchain. Install on a supported Node, without changing
global configuration just to suppress engine checks:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173 (do not mix localhost and 127.0.0.1 in browser URLs).
Vite binds loopback, proxies relative /api to 127.0.0.1:8000 with changeOrigin:false,
and keeps the browser's original Host so standard Origin/CSRF checks work.
Do not disable CSRF/CORS or add arbitrary trusted origins to mask proxy problems.

Routes: /login and /register. AuthProvider/useAuth restores me, exposes global
state and refreshes on focus. The shared client uses same-origin cookies and reads
the current CSRF cookie before every mutation, including after login rotation.
No sessionid/JWT/password is stored in localStorage/sessionStorage or URLs.
Registration returns to login with a success notice; it does not log in. The
account card honestly reports the unverified phone, and employee login displays
the employee account type without offering client-side role escalation.

The UI covers loading, field validation, duplicate phone, invalid credentials,
disabled registration, rate limit, CSRF, network errors and expired sessions.
Auth state ignores stale pending refreshes during login/logout. Requests are not
automatically retried after an unsafe failure, avoiding duplicate mutations.

S1-P4 owns features/auth/* and shared/api/client.ts. App/main/router are the
minimum bootstrap to demonstrate auth, not the catalogue/general product shell.
S1-P5 takes ownership of the shared shell/layout/router after integration and
reuses this bootstrap/AuthProvider; S1-P6 adds product details, not another app.

## Verification

With a disposable PostgreSQL configured, from backend:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py test --noinput
```

Fresh migration replay is required on that disposable server, never the main DB.
Tests include Client(enforce_csrf_checks=True), independent sessions, exact JSON,
host/flag gates, deterministic rate windows and a real PostgreSQL UNIQUE race.
All previous tests remain unchanged. No SQLite-only success claim is sufficient.

With the local disposable backend on 8000 and Vite on 5173:

```bash
cd frontend
npm run build
npm run test:e2e
```

E2E uses installed Google Chrome at Desktop size. The first two scenarios use the
real backend; others mock failures to verify UI states deterministically. Use a
fresh demo DB/cache for each complete browser run; alternatively set a fresh
synthetic BERI_E2E_PHONE and restart the demo server to clear process-local limits.
Artifacts are ignored by Git. Actual counts/results belong to the task report.
