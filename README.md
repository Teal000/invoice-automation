# Invoice Processing and Exception Reporting

Receives supplier invoices, validates them against business rules and purchase
orders, records exceptions for review, and reports the outcome.

**All data in this repository is synthetic.** The distributor, its suppliers,
its purchase orders and every invoice are invented. The French labels and TND
amounts reflect a Tunisian business setting; the 19% tax rate is a fictional
test configuration, not tax guidance.

**Status: in progress.** A synthetic invoice PDF goes in and comes out as a
recorded, validated or flagged invoice with its rule outcomes, revision
history and event trail. The HTTP API, n8n orchestration, the review
interface and the dashboard are not built yet. There is no OCR: scanned
documents are not handled. No time-saving or human-effort figure is reported
anywhere here, because none has been measured.

## What exists today

| Capability | State | How it was checked |
|---|---|---|
| PDF text extraction across 3 layout families, with field evidence | Implemented | 64 unit tests on the committed documents; measured, see below |
| An unrecognised layout invents nothing and routes to review | Implemented | Held-out 4th layout the extractor was never taught |
| 13 deterministic validation rules | Implemented | 207 unit tests, no database needed |
| Each rule fires on its own case and nothing else | Implemented | One isolated case per code; a test asserts every code is covered |
| Decimal money handling, explicit precision and rounding | Implemented | Unit tests including tolerance boundaries |
| Normalisation that records missing and unparseable fields instead of guessing | Implemented | Unit tests |
| PostgreSQL schema, migrations with checksums, reporting views | Implemented | Applied to a live server; 12 tables and 4 views created |
| Submission/invoice separation, revisions, rule outcomes, append-only events | Implemented | Integration tests, including that `UPDATE` and `DELETE` on the event log are refused |
| Idempotency key, duplicate guards enforced by database constraints | Implemented | Integration tests, including two threads submitting the same invoice number at once |
| Reporting views reconcile with direct SQL | Implemented | Integration test compares every count and rate against direct queries |
| CLI: init-db, seed, ingest, ingest-pdf, both manifests, register, overview, exceptions, show | Implemented | All run against a live database |
| 16 labelled synthetic cases as payloads **and** as PDFs, with ground truth | Implemented | Every case produces its labelled state and codes three ways: in-memory replay, payloads against the database, PDFs against the database |
| OCR for scanned documents, API, n8n, review UI, Power BI | Not built | — |

**241 tests pass, none skipped**: 207 that need no database and 34 that do.

## Measured so far

Extraction, on the 16 synthetic documents it was built for
([full results](evaluation/results/extraction_baseline.md)):

| Measure | Result |
|---|---|
| Layout detection | 16/16 |
| Critical-field document accuracy | 16/16 |
| Field accuracy, fields present in ground truth | 160/160 |

On a **held-out fourth layout the extractor was never taught** (3 documents):

| Measure | Result |
|---|---|
| Layout recognised | 0/3 |
| Fields recovered | 0/30 |
| **Fields invented** | **0** |

Read those two tables together. The first is the extractor marking its own
homework: the layouts and its label sets were written side by side, on 16
documents, with nothing held out. It says the extractor reads the documents
this generator draws. It does not say anything about a real supplier invoice.

The second is the informative one. Faced with a layout it does not know, the
extractor recognises nothing, produces no values, and the rules route the
document to a person — rather than reading the wrong label into a total and
validating it. Failing by reading nothing is safe; failing by reading the
wrong thing is not, because a wrong total that agrees with a wrong subtotal
passes every arithmetic check.

The version actually tested against is **PostgreSQL 16.2**, run as a
project-local server (`tools/pg_dev_server.py`, see
[decisions.md](docs/decisions.md) ADR-012) because no system PostgreSQL was
installed on the development machine. The documented setup targets PostgreSQL
18 and has **not** been exercised. Every feature the schema uses — identity
columns, partial-index `ON CONFLICT` inference, `FILTER`, `jsonb`,
`gen_random_uuid()` — exists in both, but that is an argument, not a test
result.

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
.\.venv\Scripts\python.exe -m invoice_automation.cli ingest-manifest
.\.venv\Scripts\python.exe -m invoice_automation.cli overview
```

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
- [docs/setup.md](docs/setup.md) — installation and commands
- [PROGRESS.md](PROGRESS.md) — what is done, what is next

## Stack

Python 3.12, psycopg 3 with hand-written SQL, PostgreSQL 18, pydantic for the
data contract, typer for the CLI, pytest. Planned: FastAPI, n8n, Streamlit,
Power BI. Versions are pinned in `requirements.txt` and were checked against
the current releases on 5 November 2026.
