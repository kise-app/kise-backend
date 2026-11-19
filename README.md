# kise-backend

FastAPI backend for **Kise**, a personal expense tracker for Ethiopian users. Domain-driven,
PostgreSQL-backed, with the Ethiopian calendar (Amharic month names) as a first-class citizen
alongside the Gregorian one.

The Flutter mobile client lives in a separate repository: **kise-app**.

## Running

Requires Python 3.11+ ([uv](https://docs.astral.sh/uv/) recommended). Developed on 3.13.

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"          # or: uv pip install -r requirements-dev.txt

cp .env.example .env                # then change SECRET_KEY

.venv/bin/alembic upgrade head      # create the schema
.venv/bin/uvicorn kise.platform.api.app:app --reload
```

- `http://127.0.0.1:8000/`        landing page
- `http://127.0.0.1:8000/docs`    interactive API (Swagger)
- `http://127.0.0.1:8000/health`  health check

Configuration is read from the environment by plain names (no prefix): `DATABASE_URL`,
`SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `DEFAULT_CURRENCY`, `CORS_ORIGINS`.
A platform-provided `DATABASE_URL` (e.g. from Heroku) is therefore used as it is, and a bare
`postgres://` scheme is normalised to `postgresql+psycopg://`.

### Assistant (voice notes, receipts, questions)

`/api/assistant/*` turns typed text, a voice note or a receipt photo into *draft* expenses, and
answers questions about spending from server-computed figures. It never records anything itself:
the app shows the drafts and records the confirmed ones through `/api/expenses`.

It is off until a key is set — then `GET /api/assistant/status` reports `enabled: true` and the app
shows the assistant button:

- `GEMINI_API_KEY` — a Google Gemini API key (the only provider today; `ASSISTANT_PROVIDER=gemini`).
- `GEMINI_MODEL` — default `gemini-3.8-flash`.
- `ASSISTANT_HOURLY_LIMIT` — requests per user per rolling hour, per web process (default 30).

Audio and images are sent to Google for reading and are not stored by Kise. Every figure the model
returns is checked server-side (`assistant/domain/drafts.py`): categories must be the user's own,
dates are converted with Kise's calendar code rather than by the model, and unusable lines are
dropped.

## Docker

```bash
docker compose up --build           # Postgres + backend (migrates, then serves)
```

## Testing

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests migrations
```

## Deployment (Heroku via GitHub)

Pushes to `main` deploy automatically: `.github/workflows/deploy-heroku.yml` runs lint and the test
suite, then pushes to Heroku's git remote. The `Procfile`'s `release` phase applies migrations
(`alembic upgrade head`) before the new dynos take traffic, and the workflow polls `/health`
afterwards so a broken release fails the run. `.github/workflows/ci.yml` covers pull requests.

One-time setup:

```bash
heroku create <app>
heroku addons:create heroku-postgresql:essential-0 --app <app>

# A root package.json makes Heroku detect Node first — pin the Python buildpack explicitly.
heroku buildpacks:set heroku/python --app <app>

heroku config:set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" \
                  DEFAULT_CURRENCY=ETB \
                  CORS_ORIGINS='["https://your-frontend"]' --app <app>
```

Then, in GitHub under *Settings → Secrets and variables → Actions*:

| kind     | name              | value                                             |
| -------- | ----------------- | ------------------------------------------------- |
| secret   | `HEROKU_API_KEY`  | output of `heroku authorizations:create`          |
| variable | `HEROKU_APP_NAME` | the Heroku app name                               |

`DATABASE_URL` needs no configuration: the Postgres add-on exports it and `platform/config.py`
adopts it, rewriting `postgres://` to `postgresql+psycopg://`. Nothing else is required — but do
narrow `CORS_ORIGINS` from the `["*"]` default, and note that `SECRET_KEY` must be set or
the app boots with the insecure placeholder.

`make deploy` pushes manually (bypassing CI) if you need an escape hatch, and `heroku.yml` is
included for a container deploy (`heroku stack:set container`) using the repository `Dockerfile`,
which is the more reliable path if buildpack detection ever gets in the way.
