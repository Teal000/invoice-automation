# Architecture

## Where things are

```text
src/invoice_automation/
  config.py          Settings (environment) and RuleConfig (plain dataclass)
  money.py           Decimal parsing, quantisation, tolerance
  models.py          RawInvoice -> NormalizedInvoice, field problems
  rules/
    base.py          RuleResult, ValidationContext, reference-data views
    identity.py      REQUIRED_FIELD_MISSING, DATE_INCONSISTENT,
                     CURRENCY_UNSUPPORTED, UNSUPPORTED_DOCUMENT
    arithmetic.py    LINE_TOTAL_MISMATCH, SUBTOTAL_MISMATCH, TOTAL_MISMATCH
    supplier.py      UNKNOWN_SUPPLIER
    duplicates.py    EXACT_FILE_DUPLICATE, BUSINESS_DUPLICATE
    purchase_order.py PO_NOT_FOUND, PO_SUPPLIER_MISMATCH, PO_AMOUNT_MISMATCH
    engine.py        Evaluation order, short circuit, business-state mapping
  db/
    connection.py    psycopg connections, database creation
    migrations.py    Migration runner with checksums
    repository.py    Every SQL statement the pipeline uses
  pipeline.py        One arrival from input to recorded outcome
  cli.py             The visible end of the slice

db/migrations/       0001 schema, 0002 reporting views
data/                Fixture generator, payloads, synthetic registry, ground truth
tools/check_sql.py   Parses all SQL with the PostgreSQL grammar
tests/unit/          No database required
tests/integration/   Skipped when no server is reachable
```

## The flow, as implemented

```text
structured payload (JSON file)
        |
        v
CLI reads the bytes, hashes them (SHA-256)
        |
        v
documents row (raw payload kept) + submissions row
        |
        v
processing_jobs row: queued -> running
        |
        v
normalize_invoice()  raw strings -> typed values + field problems
        |
        v
lookups, once: supplier, purchase order, file duplicate, business duplicate
        |
        v
run_rules()  13 codes, pass / fail / skipped
        |
        +-- identical file -----> link to earlier submission, stop
        |
        +-- otherwise ----------> invoice (insert or get) + revision + lines
                                  validation_results, events, job succeeded
        |
        v
business state: validated | needs_review | duplicate | unsupported
        |
        v
reporting views -> CLI tables (Power BI later)
```

Everything after the idempotency check happens inside one database
transaction. Either the submission, its revision, its rule outcomes and its
events are all recorded, or none of them are.

## Two seams, deliberately placed

**Extraction.** The rules consume a `RawInvoice`: header fields and lines as
optional strings. The structured fixtures produce one directly; a PDF
extractor will produce the same type. That is why the validation logic could
be built and tested before any extraction exists, and why adding extraction
does not touch the rules.

**Orchestration.** `process_structured_submission(conn, raw, submission)` is
one call that is safe to repeat: an idempotency key short-circuits a replay,
and the job row is unique per submission, so a retry increments an attempt
count instead of creating a second invoice. n8n will call an API endpoint that
wraps this same function; the authoritative state stays in PostgreSQL rather
than in n8n's execution history.

## Why the rules are pure functions

Each rule takes a `ValidationContext` holding data that has already been
fetched, and returns results. No rule opens a connection or queries anything.
Consequences:

- every rule is unit tested without a database (138 unit tests, no server);
- the lookups happen once per submission rather than once per rule;
- a rule cannot accidentally depend on the order the others ran in.

The one ordering dependency that does exist — the file-duplicate check running
first so it can stop the rest — lives in `engine.py` and is tested.

## Why expected and observed are stored on every result

A failure that only says "mismatch" cannot be reviewed. Each
`validation_results` row carries the code, the field, what the rule expected,
what the document said, and a `details` object (differences, tolerances
applied, linked record ids). That is what makes the review queue possible in
the next increment, and what lets a borderline tolerance case be explained
afterwards.

## Not built yet

PDF and OCR extraction, the FastAPI service, n8n workflows, the Streamlit
review interface, the Power BI dashboard, and the evaluation harness. The
README lists what exists today and how it was checked.
