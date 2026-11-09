# Invoice Processing and Exception Reporting

Receives supplier invoices, validates them against business rules and purchase
orders, records exceptions for review, and reports the outcome.

**All data in this repository is synthetic.** The distributor, its suppliers,
its purchase orders and every invoice are invented. The French labels and TND
amounts reflect a Tunisian business setting; the 19% tax rate is a fictional
test configuration, not tax guidance.

**Status: in progress.** A synthetic invoice PDF can be uploaded over HTTP
and comes out as a recorded, validated or flagged invoice with its rule
outcomes, revision history and event trail — and a technical failure part way
through leaves a retryable job rather than a mess. n8n triggers processing over a
webhook, a Streamlit review queue exists, and a three-page Power BI report
reads the database through a role that can see six views and no tables.
There is no OCR:
scanned documents are not handled. No time-saving or human-effort figure is
reported anywhere here, because none has been measured.

## What exists today

| Capability | State | How it was checked |
|---|---|---|
| HTTP API: upload, process, retry, register, reports | Implemented | 30 integration tests against a live database |
| Arrival and processing are separate steps, so a retry needs no re-upload | Implemented | A test hides the stored file mid-workflow, gets a retryable job, restores it and retries to success |
| Reviewer corrections, re-checked by the same rules, with revision history | Implemented | 25 integration tests, including that a correction cannot bypass a check |
| Rejection and accepted exceptions, each with a required reason | Implemented | Rejection releases the invoice number; an accepted exception is excluded from the straight-through rate |
| Recovery sweep: failed attempts, expired leases, jobs nobody triggered | Implemented | 19 integration tests, including that one poison document does not block the queue |
| De-duplicated notifications in an outbox, written with the work | Implemented | Reprocessing announces nothing new; a new revision does; the key is unique in the database |
| Documents stored under their own SHA-256, in one place per content | Implemented | Integration test: an identical file reuses the stored bytes |
| PDF text extraction across 3 layout families, with field evidence | Implemented | 64 unit tests on the committed documents; measured, see below |
| An unrecognised layout invents nothing and routes to review | Implemented | Held-out 4th layout the extractor was never taught |
| 13 deterministic validation rules | Implemented | 209 unit tests, no database needed |
| Each rule fires on its own case and nothing else | Implemented | One isolated case per code; a test asserts every code is covered |
| Decimal money handling, explicit precision and rounding | Implemented | Unit tests including tolerance boundaries |
| Normalisation that records missing and unparseable fields instead of guessing | Implemented | Unit tests |
| PostgreSQL schema, migrations with checksums, reporting views | Implemented | Applied to a live server; 12 tables and 4 views created |
| Submission/invoice separation, revisions, rule outcomes, append-only events | Implemented | Integration tests, including that `UPDATE` and `DELETE` on the event log are refused |
| Idempotency key, duplicate guards enforced by database constraints | Implemented | Integration tests, including two threads submitting the same invoice number at once |
| Reporting views reconcile with direct SQL | Implemented | Integration test compares every count and rate against direct queries |
| CLI: init-db, seed, ingest, serve, recover, notifications, both manifests, register, overview, exceptions, show | Implemented | All run against a live database |
| 16 labelled synthetic cases as payloads **and** as PDFs, with ground truth | Implemented | Every case produces its labelled state and codes three ways: in-memory replay, payloads against the database, PDFs against the database |
| Container setup: db, api and n8n in compose | Implemented | Built and run; all three containers healthy |
| The schema on PostgreSQL 18 | Verified | All five migrations applied and the **whole suite passes** against PostgreSQL 18.6 in the container |
| n8n orchestration: webhook triggers processing through the API | Runs | Observed end to end on both branches: a PDF uploaded unprocessed, its id posted to the webhook, n8n calls the API, the submission reaches `validated` or `needs_review`. The two scheduled workflows are published but have not been seen to fire |
| Reporting views for a dashboard, read-only role, reconciliation | Implemented | Six views; the role is granted SELECT on views only and the grant is tested by trying to read a table and write |
| A three-page Power BI report over the read-only role | Built | Processing overview, exception analysis, invoice register. Versioned as a `.pbip` project: the visuals are JSON, not a binary. Every figure checked against the base tables by `tools/reconcile_dashboard.py` |
| Notification *delivery*, OCR | Not built | — |

**335 tests pass, none skipped** — against **both** PostgreSQL 16.2 (the
local dev server) and PostgreSQL 18.6 (the container), on 9 November 2026.

## Measured so far

A benchmark over **200 synthetic documents** across five layout families,
split 120 development / 40 validation / 40 frozen test. Full method and
caveats in [evaluation/methodology.md](evaluation/methodology.md); every
figure below is reproducible from a documented command.

The frozen test split (40 documents), run once:

| Measure | Familiar layouts (32) | Unseen layout (8) |
|---|---|---|
| Layout identified | 32/32 | **0/8** |
| Critical-field document accuracy | 32/32 | **0/8** |
| **Values invented** | — | **0** |

| Measure | Result |
|---|---|
| Pipeline conformance | 32/40 |
| Fault recall, all seeded faults | 20/24 |
| …faults a rule targets | 19/19 |
| …**faults no rule targets** | **1/5** |

Read together, those say something useful. On layouts it was taught the
extractor recovers every critical field; on a layout it has never seen it
identifies nothing, recovers nothing and **invents nothing**, so those eight
documents go to a person instead of being validated on values read with the
wrong labels. Conformance is 32/40 *because* of those eight — they do not
reach the state their labels describe, and reporting that is the point of
measuring it.

The corpus deliberately contains faults no rule can catch — a supplier name
that disagrees with a correct code, an invoice dated next year, a
zero-quantity line, a line entered twice. The rules catch every fault they
were built for and none of those. The one that was flagged was flagged only
because its layout was unreadable, not because the fault was noticed.

What none of this establishes: anything about real supplier invoices, about
scanned documents (there is no OCR), or about time saved (nothing has been
timed).

## The idea in one pass

A structured payload arrives. Its bytes are hashed and stored, the arrival is
recorded as a submission, and a processing job is opened. The values are
normalised — anything absent or unreadable is recorded as a problem rather
than replaced with a zero. The supplier, the purchase order and the duplicate
lookups happen once. Thirteen rules then run as pure functions and return
`pass`, `fail` or `skipped`, each failure carrying the expected value, the
observed value and the field. The engine maps the results to one business
state, the whole thing is written in a single transaction, and the reporting
views read from it.

A submission is not an invoice. The same invoice can arrive repeatedly; each
arrival stays visible and only the first creates a canonical record. An
identical file is linked rather than reprocessed. A different file claiming an
invoice number that already exists becomes a new revision of that invoice and
goes to review, with both files kept.

## Quick start

See [docs/setup.md](docs/setup.md) for the full sequence, including the
no-install option if you have no PostgreSQL service. In short:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli init-db
.\.venv\Scripts\python.exe -m invoice_automation.cli seed
.\.venv\Scripts\python.exe -m invoice_automation.cli ingest data\fixtures\pdf\valid_001.pdf
.\.venv\Scripts\python.exe -m invoice_automation.cli overview
```

Or over HTTP, with interactive documentation at `/docs`:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli serve
```

| Endpoint | Purpose |
|---|---|
| `POST /submissions` | Upload a PDF or payload; honours `Idempotency-Key` |
| `POST /submissions/{id}/process` | Extract, validate, record; safe to repeat |
| `GET /submissions/{id}` | Arrival status, stored document, job state |
| `POST /jobs/{id}/retry` | Retry a failed job without re-uploading |
| `POST /jobs/recover` | Sweep: finish work that stalled |
| `GET /notifications` | The outbox. Nothing is delivered by this service |
| `POST /invoices/{id}/revisions` | A reviewer correction, re-checked by the same rules |
| `POST /invoices/{id}/review-actions` | Reject, or accept despite a failed check |
| `GET /invoices` | Register, filtered by state, supplier, exception code, dates |
| `GET /invoices/{id}` | One invoice with checks, revisions and events |
| `GET /reports/summary` | Documented aggregates from the reporting views |
| `GET /health` | Liveness and whether the database answers |

Every endpoint except `/health` needs the `X-API-Key` header, and the service
refuses to start without a credential configured.

`ingest-manifest` processes the 16 labelled cases in order and compares every
outcome with its ground truth, exiting non-zero on any mismatch.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q   # no database
.\.venv\Scripts\python.exe -m pytest -q              # adds integration tests
.\.venv\Scripts\python.exe tools\check_sql.py        # SQL syntax, no server
```

## Documentation

- [docs/business_requirements.md](docs/business_requirements.md) — the process,
  the rules, the configuration, what is out of scope
- [docs/architecture.md](docs/architecture.md) — layout, flow, and the two
  seams where extraction and orchestration attach
- [docs/decisions.md](docs/decisions.md) — eleven decisions with their reasons,
  including why this runs on local PostgreSQL rather than Docker
- [docs/limitations.md](docs/limitations.md) — what this does not establish
- [docs/business_case.md](docs/business_case.md) — the case in one page, from
  measured results only
- [docs/resume_bullets.md](docs/resume_bullets.md) — drafts, each with the
  evidence behind it, and the claims that are **not** supportable
- [docs/demo_script.md](docs/demo_script.md) — the demonstration, with the
  steps that do not exist yet marked as such
- [docs/setup.md](docs/setup.md) — installation and commands
- [PROGRESS.md](PROGRESS.md) — what is done, what is next

## Stack

Python 3.12, psycopg 3 with hand-written SQL, PostgreSQL 18, pydantic for the
data contract, typer for the CLI, pytest. Planned: FastAPI, n8n, Streamlit,
Power BI. Versions are pinned in `requirements.txt` and were checked against
the current releases on 5 November 2026.
