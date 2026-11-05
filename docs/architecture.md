# Architecture

## Where things are

```text
src/invoice_automation/
  api/
    main.py          FastAPI app: routes, auth, error envelope
    schemas.py       Typed request and response models
  storage.py         Content-addressed document storage
  recovery.py        The sweep that finishes stalled work
  config.py          Settings (environment) and RuleConfig (plain dataclass)
  money.py           Decimal parsing, quantisation, tolerance
  models.py          RawInvoice -> NormalizedInvoice, field problems
  extraction/
    words.py         Words, lines, label lookup, table columns
    layouts.py       The three supported layout families
    pdf.py           PDF -> RawInvoice + raw text + field evidence
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
    jobs.py          Job leases, recovery candidates, notification outbox
  pipeline.py        One arrival from input to recorded outcome
  cli.py             The visible end of the slice

db/migrations/       0001 schema, 0002 views, 0003 evidence, 0004 outbox
data/                Generators, payloads, PDFs, synthetic registry, ground truth
evaluation/          Extraction baseline, methodology, results
tools/check_sql.py   Parses all SQL with the PostgreSQL grammar
tests/unit/          No database required
tests/integration/   Skipped when no server is reachable
```

## The flow, as implemented

Two steps, which is what makes orchestration and retries possible:

```text
STEP 1 - arrival (POST /submissions, or `cli ingest`)
        |
        v
bytes hashed (SHA-256) and stored at storage/<2 chars>/<hash>.<ext>
        |
        v
documents row + submissions row (outcome 'pending')
        |
        v
processing_jobs row: 'queued'            -> a receipt is returned here
        |
        |   an orchestrator decides when to continue, and can retry
        v
STEP 2 - processing (POST /submissions/{id}/process)
        |
        v
already settled? -> report the recorded outcome, do nothing else
        |
        v
job claimed: 'running', attempt_count + 1   (outside the work transaction,
                                             so a failed attempt survives)
        |
        v
read the stored file -> extract (PDF) or parse (JSON)
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
optional strings. The structured fixtures produce one directly; the PDF
extractor produces the same type from a document. That is why the validation
logic could be built and tested before any extraction existed — and when
extraction arrived, **not one rule changed**. The integration suite asserts
that a document and its payload reach the same business state, so a future
divergence means either extraction lost something or a rule started depending
on formatting.

### How extraction works

```text
PDF
 |
 v  pdfplumber: every word with its coordinates
words -> lines (grouped by vertical position)
 |
 v  detect the document kind from its title (FACTURE / AVOIR / ...)
 v  score all layouts, pick the best; below half marks, give up
 |
 +-- header fields: start-anchored label, value is the rest of the line
 +-- totals: rightmost run of numeric tokens on the label's line
 +-- line items: columns from the header row's x-spans
 |
 v
RawInvoice  +  raw text  +  field evidence (page, source line, bounding box)
```

Three label sets live in `extraction/layouts.py`; the engine in `words.py` is
layout-agnostic, so a fourth family is a specification rather than new code.
Two details carry more weight than their size suggests:

- Labels match **start-anchored**. A document with both "Fournisseur :" and
  "Code fournisseur :" would give a contains-match the wrong line.
- Amounts are read as the **rightmost run of numeric tokens**, so
  "Taxe (19%) 291,574" yields 291,574 and not 19, and the French thousands
  group in "1 337,750" is rejoined rather than read as 1.

When no layout scores well enough, the extractor returns almost nothing: the
document kind, if it could tell, and no field values. The rules then fail
`REQUIRED_FIELD_MISSING` and the document goes to review. Failing by reading
nothing is recoverable; failing by reading the wrong label into a total is
not, because a wrong total that agrees with a wrong subtotal passes every
arithmetic check.

There is no OCR. A scanned document has no text layer, so this path yields
nothing and the document goes to review.

**Orchestration.** The arrival/processing split *is* the seam. An orchestrator
holds a submission id and nothing else: it decides when to call
`/submissions/{id}/process`, and `/jobs/{id}/retry` to try again. Both are
safe to repeat — a settled submission is reported rather than reprocessed, and
the job row is unique per submission, so a retry increments an attempt count
instead of creating a second invoice.

The authoritative state is in PostgreSQL, not in the orchestrator's run
history: `submissions.outcome`, `processing_jobs.status` and `audit_events`
say what happened, and they are what a recovery sweep will read.

When a technical failure happens mid-processing, the work transaction rolls
back but the job does not: `fail_job` marks it `retry_pending` while attempts
remain and `failed` once they are spent, decided in SQL against the row's own
counters so two workers cannot disagree. The stored document and the event
history survive, which is what lets the retry skip the upload entirely.

### The HTTP service

`src/invoice_automation/api/` — FastAPI, typed request and response schemas,
one error envelope, interactive documentation at `/docs`. Endpoints contain no
business rules: they record an arrival, trigger processing, or read a
reporting view. Authentication is a single service credential in `X-API-Key`,
required everywhere except `/health`, and the service refuses to start without
one.

## Why the rules are pure functions

Each rule takes a `ValidationContext` holding data that has already been
fetched, and returns results. No rule opens a connection or queries anything.
Consequences:

- every rule is unit tested without a database (207 unit tests, no server);
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

## Recovery, and why taking a job is safe

Claiming a job sets a lease. A job still marked `running` after the lease has
elapsed is treated as abandoned and a sweep may take it. That is safe only
because processing is idempotent: a settled submission is reported rather
than reprocessed, so two workers on one job cannot produce two invoices. The
safety is in that property, not in the timeout being generous.

The sweep picks up three cases and names which: an attempt that failed with
attempts remaining, a lease that expired, and a job that was queued and never
triggered. Attempts-spent jobs are left for a person. One failing job never
stops the others.

Notifications are written into an outbox **in the same transaction as the
work**, because sending cannot be rolled back: announcing a review case and
then failing would tell a reviewer about an invoice that does not exist. The
deduplication key is derived from the subject — the invoice and revision, the
arrival, the attempt number — so a repeat is a collision while genuinely new
information still gets through. Nothing in this version delivers anything.

## Not built yet

OCR for scanned documents, n8n workflows, the Streamlit review interface, and
the Power BI dashboard. Nothing drives the recovery sweep on a schedule, and
no notification is actually delivered. The README lists what exists
today and how it was checked; `evaluation/methodology.md` says what has been
measured and what has not.
