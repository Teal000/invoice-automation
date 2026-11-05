# Architecture

## Where things are

```text
src/invoice_automation/
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
  pipeline.py        One arrival from input to recorded outcome
  cli.py             The visible end of the slice

db/migrations/       0001 schema, 0002 reporting views, 0003 field evidence
data/                Generators, payloads, PDFs, synthetic registry, ground truth
evaluation/          Extraction baseline, methodology, results
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

- every rule is unit tested without a database (143 unit tests, no server);
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

OCR for scanned documents, the FastAPI service, n8n workflows, the Streamlit
review interface, and the Power BI dashboard. The README lists what exists
today and how it was checked; `evaluation/methodology.md` says what has been
measured and what has not.
