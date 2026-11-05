# Progress

Running log so the work can continue in another session. Newest entry first.
Keep it factual: what was built, how it was checked, what is unverified.

---

## 2026-11-05 (week 2, part 1) — PDF extraction; a measured baseline

### Built

- `data/generate_pdf_fixtures.py`: renders every structured payload as a
  synthetic invoice PDF across three layout families (`atlas`, `medina`,
  `carthage`) with French labels, French number formatting and DD/MM/YYYY
  dates. Deterministic via reportlab `invariant=1`, so regenerating gives
  byte-identical files and the duplicate case is a genuine resend.
- A fourth layout, `sousse`, drawn by the generator and **deliberately absent
  from the extractor**, with three documents as a held-out probe.
- `src/invoice_automation/extraction/`: geometric extraction over pdfplumber.
  `words.py` groups words into lines and table columns; `layouts.py` holds the
  three label sets; `pdf.py` produces a `RawInvoice` plus the raw text and
  per-field evidence (page, source line, bounding box).
- Migration `0003_field_evidence`: `invoice_revisions.field_evidence jsonb`.
- The pipeline carries `raw_text`, `field_evidence`, `media_type` and
  `byte_size`. **No rule changed** to accommodate documents — that was the
  point of the extraction seam.
- CLI: `ingest-pdf` and `ingest-pdf-manifest` (the latter also checks that each
  document was read in the layout it was drawn in).
- `evaluation/run_extraction_baseline.py` + `evaluation/methodology.md`.

### Measured

Extraction on the 16 documents it was built for: layout detection 16/16,
critical-field document accuracy 16/16, 160/160 on fields present in the
ground truth.

Held-out `sousse` layout, 3 documents: layout recognised **0/3**, fields
recovered **0/30**, fields invented **0**.

The first set is self-referential — the layouts and the label sets were
written together, 16 documents, nothing held out — and is reported that way in
the README, the methodology and the limitations. The second is the informative
one: an unknown layout yields nothing and routes to review rather than being
read with the wrong labels.

### Checked

- `pytest -q` → **241 passed, 0 skipped** (207 without a database, 34 with).
- 64 new extraction unit tests, including the label-ambiguity case
  ("Fournisseur :" vs "Code fournisseur :"), the tax label carrying its rate,
  the currency fallback, and an ordinary non-invoice PDF.
- An integration test asserts a document and its payload reach the **same**
  business state, so extraction losing information would fail the suite.
- CLI run from empty: 16 PDFs produce the same aggregate overview as the
  structured run (14 unique invoices, 1 duplicate arrival, review rate
  0.9167).

### What the work turned up

- The generator first used U+202F as a thousands separator; the base-14
  Helvetica encoding has no glyph for it and reportlab rendered it as "n", so
  amounts came out as `1n200,500`. Now a plain space, which also forces the
  extractor to rejoin numeric tokens the way real invoices require.
- Dates were being written in ISO rather than the DD/MM/YYYY the docs claimed.
- **A design wart the probe exposed:** an unreadable document was recorded as
  `unsupported`, the same bucket as a credit note. Those are different —
  "excluded kind" versus "we failed to read it" — and merging them would have
  moved failures out of the review queue and flattered the review rate. An
  unclassifiable document is now a missing `document_type`, so it fails
  `REQUIRED_FIELD_MISSING` and goes to review (ADR-016).
- The probe initially counted the explicit "unknown" marker as an invented
  value. A stated refusal is not a fabrication; the metric now excludes it and
  says so.

### Still not done

No OCR (Tesseract is not installed, and a scanned page has no text layer).
No API, no n8n, no review interface, no dashboard. Reviewer corrections are
still unimplemented, so the plan's requirement 4 remains unmet.

### Next, in order

1. FastAPI endpoints wrapping `process_structured_submission`:
   `POST /submissions` with an idempotency key, `GET /submissions/{id}`,
   `GET /invoices`, `GET /invoices/{id}`, `/health`.
2. Persistent jobs, bounded retries and a recovery path — the job table
   already carries `attempt_count`, `max_attempts` and `lease_expires_at`.
   Needs `notification_outbox` (migration 0004) for de-duplicated
   notifications.
3. n8n: container versus npm still open (Node 24.14.0 present, Docker not).
   Check the current n8n docs for supported Node versions before choosing.
4. Week 3: reviewer corrections and revalidation, Streamlit review queue,
   Power BI.

Nothing pushed. Nothing on the résumé.

---

## 2026-11-05 (later) — Database verified; week 1 exit condition met

### What ran

Against a live PostgreSQL **16.2**, started as a project-local server from the
`pgserver` wheel because no system PostgreSQL was installed (ADR-012). The
documented target is still 18 and is still untested.

- `init-db` → database created, both migrations applied: 12 tables, 4 views.
- `seed` → 3 synthetic suppliers, 13 purchase orders.
- `ingest-manifest` → **all 16 labelled cases matched their ground truth**,
  state and failed codes, through the full pipeline.
- `pytest -q` → **167 passed, 0 skipped** (143 without a database, 24 with).
- `overview` reconciles by hand: 16 submissions, 1 duplicate arrival, 14
  unique invoices, 12 eligible (2 unsupported excluded), review rate 11/12 =
  0.9167. These are counts on a synthetic fixture set, not a performance claim.

### What the live run found

**A silent data-loss bug, and it affected everything.** `connect()` returned
non-autocommit connections. Both the migration runner and the pipeline read
before writing, and in psycopg that first read opens an implicit transaction —
so the following `with conn.transaction():` block was no longer the outermost
transaction, psycopg implemented it as a savepoint, and leaving the block
committed nothing. Both reported success and persisted nothing; the work was
rolled back when the connection closed, with no error raised anywhere.

Symptom: `init-db` printed "applied migrations" and the next command said
`relation "suppliers" does not exist`. Inside the connection the schema
existed; from a second connection it did not.

Fixed by making `connect()` autocommit by default, so `conn.transaction()` is
always a real transaction, plus a defensive rollback in both places for a
caller that passes a non-autocommit connection (ADR-013). Two regression tests
assert durability **from a second connection** — an assertion on the same
connection sees uncommitted work and passes either way, which is exactly why
143 green unit tests said nothing about this.

Smaller things the run surfaced:

- `seed` was not atomic; it is now one transaction, so a failure cannot leave
  a half-loaded registry.
- `show` listed checks from every revision with no indication of which
  revision each belonged to, making a historical failure look current. The
  detail query now labels each result with its revision number, marks the
  current one, and marks arrival-level results (`EXACT_FILE_DUPLICATE`, which
  has no revision because the file was not reprocessed).
- Documented that `EXACT_FILE_DUPLICATE` never appears in
  `v_exception_summary`, since counting it there and as a duplicate arrival
  would let one arrival inflate two figures.

### Next, in order

1. Week 2: PDF generation for the same 16 cases, then text extraction mapped
   to `RawInvoice`. The rules and persistence do not change.
2. The FastAPI endpoints wrapping `process_structured_submission`.
3. n8n: container versus npm is still open (Node 24.14.0 present, Docker not).
   Needs migration 0003 for `notification_outbox`.
4. Week 3: reviewer corrections and revalidation (`review_actions`, also 0003),
   Streamlit review queue, Power BI.
5. Week 4: freeze a test split, run a benchmark, report measured results.

Still nothing on the résumé, and nothing pushed to GitHub.

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

### Not verified at the time of this entry

(Superseded by the entry above, which records the live database run and what
it found. Kept as written, because what was unverified at the time is part of
the record.)

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
