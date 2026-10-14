# Backend

Python 3.12+ FastAPI, async SQLAlchemy, PostgreSQL/pgvector, Argon2id, JWT, Authlib OIDC,
OpenAI strict structured outputs. No frontend/root files are owned here.

## Run (PowerShell, from this directory)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
# Generate JWT_SECRET and set it in .env; configure PostgreSQL.
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe app.main:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log
```

Reproducible alternative (uv 0.11.30): `uv sync --frozen --extra dev`.
Docker installs runtime dependencies using `uv sync --frozen --no-dev --no-editable`.
The checked-in `uv.lock` uses public PyPI and its artifact hosts. Corporate mirrors
or proxies must be configured locally/per-command, not committed into the starter.
Review dependency changes deliberately; do not silently resolve on each deployment.

PostgreSQL must have pgvector installed (e.g. `pgvector/pgvector:pg16`); migration
credentials need permission to `CREATE EXTENSION vector` or a DBA must preinstall it.
Run `alembic upgrade head` as a deployment step, **before** starting API replicas.
The Docker image uses Python 3.12, a non-root user, and does not run migrations per replica.
Docker build from this directory: `docker build -t ai-starter-backend .`.

## Browser API contract

Prefix `/api/v1`. Swagger `/docs`; schema `/openapi.json`.

| Method/path | Request | Successful response |
|---|---|---|
| POST `/auth/register` | `{email,password}` (12–128 char password) | 201 token response |
| POST `/auth/login` | same | 200 token response |
| POST `/auth/refresh` | empty body | 200 token response |
| POST `/auth/logout` | empty body | 204 |
| GET `/auth/me` | bearer authorization | `{id,email}` |
| GET `/auth/providers` | none | `{local:true,oidc:boolean}` |
| GET `/auth/oidc/login` | browser navigation | IdP redirect, or 503 if disabled |
| GET `/auth/oidc/callback` | IdP authorization response | 303 to FRONTEND_URL, no token in URL |
| POST `/agent/runs` | bearer, `{prompt}` | 201 `{id,status,answer,error,steps}` |
| GET `/agent/runs/{id}` | bearer | persisted run; 404 for another user's run |
| POST `/documents` | bearer, `{content}` | stored content/embedding metadata (Postgres only) |
| POST `/documents/search` | bearer, `{query,limit?:1..20}` | own documents by cosine distance |
| GET `/health/live` | none | 200 process live |
| GET `/health/ready` | none | 200 migrated DB ready, otherwise 503 |

Token response: `{access_token:string,token_type:"bearer",user:{id:string,email:string}}`.
Store access tokens in memory, not localStorage. Cookies are host-only:
`refresh_token` is HttpOnly at `/api/v1/auth`; `csrf_token` is readable at `/`.
Use `credentials:"include"` for auth requests. For refresh/logout, send
`X-CSRF-Token` equal to the URL-decoded `csrf_token` cookie; allowed `Origin` is required.
Register/login reject disallowed origins but permit origin-less nonbrowser clients.
With an allowed Origin, refresh without a refresh cookie returns 401 (including initial
visits and after logout). If a refresh cookie exists, missing/mismatching CSRF returns 403.
After OIDC redirect, the frontend calls refresh with this CSRF header.
Cookies use SameSite=Lax; deploy frontend/API same-site (e.g. sibling HTTPS subdomains),
not on unrelated sites. Cross-origin **same-site** clients need a same-origin frontend
proxy or an explicit CSRF acquisition design because JS cannot read API-host cookies.
Localhost frontend/backend ports share the cookie host and work directly.
Refresh failures should clear in-memory authentication; serialize refresh calls in each
browser session because concurrent use of the same refresh token is treated as reuse.

Refresh tokens are opaque random values, only SHA256 hashes are stored, with atomic
single-use rotation and family revocation on reuse/logout. Families expire absolutely.
Logout/reuse revoke refresh capability; issued access tokens remain valid for their
short TTL (default ten minutes). OIDC discovery/JWKS signature, state, nonce and S256
PKCE are handled by Authlib. Only verified emails accepted; email equality never links
local/external identities. Account linking must be a separate authenticated flow.

## Configuration

`DATABASE_URL` uses `postgresql+asyncpg://...`; `JWT_SECRET` is required, >=32 chars.
`OPENAI_API_KEY` is optional: absent/empty means agent endpoints return 503.
`OPENAI_MODEL=gpt-4o-mini`. `ENVIRONMENT=development|test|production`.
`FRONTEND_URL=http://localhost:3000`, `CORS_ORIGINS` is a JSON list of explicit origins.
`COOKIE_SECURE=false` locally; production enforces secure cookies and HTTPS browser URLs.
OIDC requires all of `OIDC_ISSUER` (HTTPS), `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and
registered `OIDC_REDIRECT_URI` (default frontend-proxied callback on :3000). Blank OIDC values disable it.
The frontend must proxy `/api/v1/*` to the backend without dropping Cookie/Set-Cookie headers.
Agent controls and token lifetimes are listed in `.env.example`.

The agent executes **inline**, with per-process bounded concurrency, a bounded queue wait,
maximum LLM calls, overall timeout, strict output validation, allowlisted Decimal arithmetic,
and persisted prompt/step observations/status. It never executes generated code or arbitrary
URLs. Repeated prompts create independent runs. Failed executions return persisted `failed`
runs with stable error codes. Database/provider failures cannot expose provider messages.
This is **not a durable background worker**: a hard process crash can leave a `running` run.
Deploy a durable job queue/leases before promising resume/retries across process death.
Only GET is available for an existing run; no concurrent "resume" mutation can double-execute.
Concurrency slots are per API process, not a distributed tenant quota.

Optional retrieval persists `text-embedding-3-small` 1536-dimensional vectors with a cosine
HNSW index, user-isolated search and explicit model tracking. It is not automatically included
in prompts/tools. User prompts/documents are sent to OpenAI when those endpoints are used;
review provider data policies before using sensitive data.

Production needs a TLS reverse proxy with request body caps and per-IP/user auth/agent rate
limits, trusted forwarding headers, secret management, encrypted backups and retention/cleanup
for expired refresh families and run content. JSON logs omit passwords, tokens, prompts and
callback query strings. Uvicorn access logs are disabled to avoid callback-code disclosure.
Use observability/alerts for failed runs, DB saturation and provider rate limits.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not postgres"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy app
# Only on a DISPOSABLE PostgreSQL database (test migrates DOWN to base at the end):
$env:TEST_POSTGRES_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/backend_test"
.\.venv\Scripts\python.exe -m pytest -m postgres
```

SQLite tests override session and LLM dependencies and never call OpenAI. PostgreSQL tests
are opt-in and exercise actual extension/migrations, vector ordering and refresh concurrency.
