# Progress

Running log so the work can continue in another session. Newest entry first.
Keep it factual: what was built, how it was checked, what is unverified.

---

## 2026-11-05 — Week 1 slice: deterministic core

### Built

- Project scaffolding: own venv from Python 3.12.13, pinned runtime and dev
  requirements, editable install, pytest configuration.
- `money.py`: Decimal parsing (including comma decimal separators and spaced
  thousands, as French-labelled documents use), quantisation to three decimal
  places with an explicit rounding mode, tolerance comparison. Refuses to
  guess: an unreadable amount raises rather than becoming zero.
- `models.py`: the data contract in three layers — `RawInvoice` (optional
  strings, nothing trusted), `NormalizedInvoice` (typed, `None` where a value
  was absent or unparseable), `SubmissionInput` (facts about the arrival).
  Conservative identifier normalisation that cannot merge distinct invoices.
- `rules/`: all 13 codes from the plan as pure functions over a prepared
  context. Three statuses (`pass`/`fail`/`skipped`); every result carries
  expected, observed, field and details.
- `rules/engine.py`: evaluation order, the single documented short circuit for
  an identical file, and the mapping from results to one business state
  (unsupported outranks review).
- `db/migrations/0001_initial.sql`: 13 tables. Submission/invoice separation,
  revisions and lines, validation results, processing jobs, append-only audit
  events with a trigger that refuses UPDATE and DELETE, partial unique index
  as the duplicate guard, unique idempotency key.
- `db/migrations/0002_reporting_views.sql`: register, rule failures, exception
  summary, processing overview with review rate and straight-through rate and
  their denominators written into the SQL.
- `db/migrations.py`: migration runner recording each file with its SHA-256
  and refusing to continue if an applied file has changed.
- `db/repository.py`: every SQL statement the pipeline uses.
- `pipeline.py`: one arrival from input to recorded outcome in a single
  transaction, with the idempotency replay path and the duplicate-link path.
- `cli.py`: `init-db`, `seed`, `ingest`, `ingest-manifest`, `register`,
  `overview`, `exceptions`, `show`, `reset`.
- `data/generate_structured_fixtures.py`: deterministic generator producing 16
  labelled payloads, the synthetic registry, and the ground-truth manifest.
  Two payloads are byte-identical by design for the file-duplicate case.
- `tools/check_sql.py`: parses the schema and every hand-written query with
  `libpg_query`, the PostgreSQL server's own parser.
- Docs: README, setup, business requirements, architecture, 11 decision
  records, limitations, metric definitions for the metrics that exist.

### Checked

- `pytest tests/unit -q` → 143 passed, no database required.
- Each of the 13 rule codes has an isolated case that fails that code **and no
  other**; a test asserts the case table covers every code the engine can emit.
- The whole labelled set is replayed in memory against the real fixture files
  and the real synthetic registry, in manifest order, with the duplicate
  lookups served by a stand-in that records what has been seen. All 16 cases
  produce their labelled state and codes. This found a defect in the generated
  reference data: one purchase order was written with an empty expected amount,
  taken from a case whose stated total is deliberately absent. `seed` would
  have failed against PostgreSQL. Fixed by falling back to the amount computed
  from the line items, since the buyer's order amount exists regardless of what
  the supplier document says.
- `tools/check_sql.py` → 66 statements parsed, 0 problems (syntax only).
- `data/generate_structured_fixtures.py` runs; the two duplicate fixtures hash
  identically (`353be437…`). The generator pins LF output and `.gitattributes`
  keeps it LF on checkout, because those bytes are what the exact-file
  duplicate check hashes.
- CLI loads and lists its commands.

### Not verified

- **No database run.** PostgreSQL was not installed on this machine during
  this session, so `init-db`, `seed`, `ingest-manifest` and the whole
  integration suite have never been executed. The schema is syntax-valid and
  nothing more. Column names, type behaviour, the `ON CONFLICT` inference on
  the partial index, the trigger, and all four views are unproven.
- Decisions taken for that reason: local PostgreSQL 18 over Docker
  (`docs/decisions.md` ADR-001). `compose.yaml` was written but never
  executed, and says so.

### Next, in order

1. Install PostgreSQL 18 (`winget install --id PostgreSQL.PostgreSQL.18`),
   create the `invoice_app` role, fill in `.env`, then run `init-db`, `seed`,
   `ingest-manifest` and `pytest -q`. Fix what the live database rejects.
   Update the README status table and this file with what actually ran.
2. Week 1 exit condition is met once that passes. Only then start week 2.
3. Week 2: PDF generation for the same 16 cases, text extraction mapped to
   `RawInvoice`, then the FastAPI endpoints (`POST /submissions`,
   `GET /submissions/{id}`, `GET /invoices`, `/health`) wrapping the existing
   pipeline function.
4. Then n8n: decide container versus npm (Node 24.14.0 is installed, Docker is
   not), implement bounded retries, the recovery workflow, and notification
   de-duplication through `notification_outbox` (not yet created — it needs
   migration 0003).
5. Week 3: reviewer corrections (`review_actions`, also migration 0003),
   revalidation of a corrected revision, Streamlit review queue, Power BI.
6. Week 4: freeze a test split, run the benchmark, write up measured results
   with their denominators and limits.

### Open questions for Nidhal

- n8n in Docker or via npm, once Docker is decided.
- Whether to keep the CLI as the demonstration surface or move straight to the
  API plus Streamlit after extraction lands.

### Rules for this project

- Synthetic data stays labelled synthetic, in the data and in the documents.
- No invented metrics, time savings, accuracy figures, client use or adoption.
  Establish a baseline, improve it, report what was measured and what it does
  not establish.
- Nothing goes on the résumé until it works and is measured.
- Nothing is pushed to GitHub without Nidhal's explicit go-ahead.
