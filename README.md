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

### Plans and the admin portal

Every account is on a plan. Plans are records an admin manages under **Plans** in the portal: each
has a name, a price (free text), whether it includes typed requests to the assistant, how many
receipt scans and voice notes it allows a month (empty for no limit, 0 for none), its longest voice
note, and whether the app offers it. Admins add plans, hide them from the app, and remove ones
nobody is on. Two are built in and can be changed but not removed: **Trial**, where new accounts
start for a length an admin sets, and **Basic**, which any plan becomes when it ends. **Pro** is
created at first but is an ordinary plan.

The assistant checks the plan in force before any model call: `403 upgrade_required` when the plan
has none of that kind, `403 allowance_used_up` when this month's scans or voice notes are used
(counts start again on the 1st, UTC), `422 voice_note_too_long` past the plan's length. Only
successful requests count. `GET /api/assistant/status` reports usage, `resets_on` and
`max_voice_seconds`; the app stops recording there and sends what was said.

The admin portal at **`/kise-office`** (server-rendered, Tailwind) shows accounts by plan, lets an
admin search accounts, put one on any plan (for N days or open-ended), and edit the plans.

Payment is manual for now. Under **Payments** an admin enters the accounts to pay into (bank or
telebirr), and a Telegram username, phone and email. The app's upgrade sheet shows them with the
plans on offer (`GET /api/plans/payment`): the person pays, sends the screenshot on Telegram quoting
their email, and an admin moves them to the plan under **Accounts**.

Admins are their own accounts, not app accounts. Create the first one from the command line; it
asks for the email and password (at least 8 characters):

```sh
make create-admin                 # locally: python -m kise.platform.admin create
make heroku-create-admin          # on Heroku
```

After that, admins add and remove each other under **Admins** in the portal, and change their own
password by clicking their email. `python -m kise.platform.admin reset-password` resets a forgotten
one. Removing an admin or changing a password signs the old sessions out at once.
`ADMIN_SESSION_HOURS` sets how long a portal session lasts (default 8).

Sessions are HttpOnly, SameSite=Strict cookies separate from app tokens; forms are CSRF-checked;
sign-in is throttled to 10 attempts per 15 minutes per address. After changing templates, rebuild
the stylesheet with `make css` (it is committed, so deployments need no Node).

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
