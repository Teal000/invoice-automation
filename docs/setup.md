# Setup

Windows 11, PowerShell. Adapt the paths for another machine.

## 1. Python environment

The project needs Python 3.12 or newer. On the development machine `python` is
not on `PATH`; the interpreter used to create the environment was
`C:\Users\BestPc\Desktop\job-radar\.venv\Scripts\python.exe` (3.12.13).

```powershell
C:\Users\BestPc\Desktop\job-radar\.venv\Scripts\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

`requirements.txt` holds the runtime pins; `requirements-dev.txt` adds the
verification tools. Versions were pinned on 5 November 2026 against the
then-current releases. `psycopg[binary]` bundles libpq (18.0.4 in this pin),
so no local libpq is required; the psycopg 3 install documentation lists the
binary package as the recommended option for Python 3.10–3.15.

## 2. PostgreSQL

This version runs against a local PostgreSQL server. Docker is not used on the
development machine because it is not installed there — see
[decisions.md](decisions.md), ADR-001.

```powershell
winget install --id PostgreSQL.PostgreSQL.18 --accept-package-agreements --accept-source-agreements
```

The installer asks for a superuser password and installs the server as a
Windows service on port 5432.

## 3. Configuration

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set `INVAUT_DB_PASSWORD` to a password of your choosing
for the application role — it does not need to match the superuser password,
and it should not. Every setting is prefixed `INVAUT_`. The money, tolerance
and tax values in that file are explicit on purpose: a tolerance that is not
written down is not reproducible. The tax rate is a fictional test
configuration for synthetic documents, not tax guidance.

`.env` is git-ignored. Do not commit it.

### Alternative: no install, no administrator rights

If you have no PostgreSQL service and do not want one, a project-local server
can be started instead. It uses the `pgserver` package, which bundles
PostgreSQL binaries, puts its data in the git-ignored `pgdata/`, and is
removed by deleting that folder.

```powershell
.\.venv\Scripts\python.exe tools\pg_dev_server.py start
.\.venv\Scripts\python.exe tools\pg_dev_server.py status
.\.venv\Scripts\python.exe tools\pg_dev_server.py stop
```

`start` also creates the application role from `.env` and writes the server's
host and port back into `.env`, because on Windows the port is chosen at start
time. Then skip to step 5; the role step below is not needed.

**The bundled PostgreSQL version is not necessarily the one named above.**
`status` prints the version actually running. Anything verified this way
should be reported with that version — see [decisions.md](decisions.md)
ADR-012.

## 4. The application role

```powershell
powershell -NoProfile -File tools\setup_db_role.ps1
```

The script reads the application user and password from `.env`, prompts once
for the superuser password, and creates the role with `CREATEDB` so the
application can create its own database and the separate test database.
Nothing in this project needs superuser rights, and the superuser password is
not stored anywhere. Running it again updates the existing role instead of
failing.

## 5. Create the schema and load the synthetic registry

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli init-db
.\.venv\Scripts\python.exe -m invoice_automation.cli seed
```

`init-db` creates the database if it is absent and applies every migration in
`db/migrations` in filename order, recording each one with its SHA-256. If an
already-applied migration file changes on disk, the runner refuses to continue
rather than leaving two databases silently different.

## 6. Run the slice

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli ingest-manifest
.\.venv\Scripts\python.exe -m invoice_automation.cli overview
.\.venv\Scripts\python.exe -m invoice_automation.cli exceptions
.\.venv\Scripts\python.exe -m invoice_automation.cli register
```

`ingest-manifest` processes the 16 labelled cases in order and compares each
outcome with its ground truth, exiting non-zero on any mismatch.

To reprocess from an empty state, keeping the supplier and purchase-order
registry:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli reset --yes
```

## 7. Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\check_sql.py
```

The unit tests need no database. The integration tests create and migrate the
database named in `INVAUT_TEST_DB_NAME`, truncate it between tests, and are
skipped with a message when no server is reachable. `tools/check_sql.py`
parses the schema and every hand-written query with the PostgreSQL grammar
itself (via `libpg_query`), which catches syntax errors without a server but
proves nothing about column names or types.

## 8. The HTTP service

The service needs a credential. Put a long random value in `.env`:

```
INVAUT_API_KEY=<a long random value>
```

It refuses to start without one, on purpose: a service running open to
anything that can reach the port is a worse failure than one that will not
boot. Then:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli serve
```

Interactive documentation at http://127.0.0.1:8000/docs. Every endpoint except
`/health` requires the `X-API-Key` header.

A full pass with `curl` — upload, process, read:

```powershell
curl -s -H "X-API-Key: $env:INVAUT_API_KEY" -F "file=@data/fixtures/pdf/valid_001.pdf" http://127.0.0.1:8000/submissions
```

```powershell
curl -s -X POST -H "X-API-Key: $env:INVAUT_API_KEY" http://127.0.0.1:8000/submissions/<submission-id>/process
```

```powershell
curl -s -H "X-API-Key: $env:INVAUT_API_KEY" "http://127.0.0.1:8000/invoices?state=needs_review"
```

Uploading is separate from processing so that an orchestrator can decide when
to process and can retry without the document passing through it again. Pass
`?process=true` on the upload to do both in one call, which is convenient for
a demonstration.

Uploaded files are stored under `INVAUT_STORAGE_ROOT` (default `data/storage`,
git-ignored) at a path derived from the file's SHA-256, so identical bytes are
stored once however many times they arrive.

## 9. Everything in containers (not yet run)

`compose.yaml` and `Dockerfile` describe the database, the API and n8n.
**Neither has been built or executed**: Docker is not installed on the
development machine, so expect the first run to need corrections. Everything
above works without Docker.

```powershell
winget install --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
```

Then, with `INVAUT_DB_PASSWORD` and `INVAUT_API_KEY` set in `.env`:

```powershell
docker compose up -d
```

```powershell
docker compose run --rm api python -m invoice_automation.cli init-db
```

```powershell
docker compose run --rm api python -m invoice_automation.cli seed
```

The API is then on http://localhost:8000/docs and n8n on
http://localhost:5678.

Notes worth knowing before the first run:

- Inside the network, services reach each other by **service name**: the API
  connects to `db`, and n8n calls `http://api:8000`. Inside a container,
  `localhost` is that container.
- n8n keeps its own data, credentials included, in its own volume (SQLite by
  default). It deliberately does not share this project's database: mixing an
  orchestrator's tables into the invoice schema would blur what is
  authoritative about an invoice.
- The n8n image is pinned to the version that was current in the n8n
  documentation on 5 November 2026, for reproducibility. Check the current
  release before raising it.
- Uploaded documents live in a named volume, so they survive the API
  container being replaced.

## Regenerating the fixtures

```powershell
.\.venv\Scripts\python.exe data\generate_structured_fixtures.py
```

The generator is deterministic and writes the payloads, the synthetic
reference data and the ground-truth manifest. Two cases are byte-identical by
design, which is what the exact-file duplicate check relies on.
