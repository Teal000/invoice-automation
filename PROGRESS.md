# Progress

Running log so the work can continue in another session. Newest entry first.
Keep it factual: what was built, how it was checked, what is unverified.

---

## 2026-11-09 — The dashboard, and the three defects it exposed

### Built

- A three-page Power BI report over the read-only `invoice_reporting` role:
  processing overview, exception analysis, invoice register.
- Saved as a **.pbip project**, not a .pbix. The visuals are JSON with a
  published schema, so the report is diffable and reviewable like the rest of
  the repository. `dashboard/README.md` section 7 covers the layout, the
  ignore rules and the editing loop.
- `.gitignore` excludes `*.pbix`, `.pbi/cache.abf` (the imported data) and
  `.pbi/localSettings.json` (one machine's paths). The connection is stored
  as host, port and database only; no username, no password.
- Migration **0007** adds `age_band_order` to `v_review_queue`.

### What putting the data on screen exposed

Three defects, none of which any test caught, all found by looking at values
rather than at code:

1. **Money rendered at two decimals.** TND carries three and the pipeline
   rounds half-up at three, but Power BI defaulted to two: `97.96` for a
   value stored as `97.961`. Fixed with `formatString` on every money column
   and checked against the database for seven invoices, including `471.240`
   where the trailing zero matters.
2. **The register silently merged invoices.** A table visual groups rows
   identical across every displayed column, and two invoices whose number,
   supplier, date and total were all unreadable are identical on screen. The
   page claimed one row per invoice while showing 3 where there were 4.
   Fixed by displaying `invoice_id`.
3. **The age axis sorted alphabetically**, putting "under a day" after "over
   a week". Hence migration 0007.

A fourth was not a defect but a lesson: the report showed pre-reload figures
for some time, and the only visual capable of revealing it was the age chart,
because every other number is identical across reloads of a seeded corpus.

### The pattern worth keeping

Every piece of logic put in the report was wrong or fragile; everything put
in SQL held. The age-band order could have been faked by sorting the chart on
its own measure — band sizes happen to run in time order today and would stop
doing so the moment the backlog changed shape. It went into the view instead,
with a test that the two `CASE` expressions agree.

Two Power BI properties were discovered by probe rather than documentation,
because the published schema URL in the files 404s:

- Renaming a field needs `displayName`, not `nativeQueryRef`. Confirmed by
  renaming one card in the UI and diffing.
- `sortByColumn` must follow `sourceColumn` in TMDL. Placed after `dataType`
  it was silently dropped on save, with no error.

### Checked

- **335 tests pass against PostgreSQL 18.6** in the container, including a
  new one asserting each age band maps to exactly one order value.
- `tools/reconcile_dashboard.py`: all 13 checks agree.
- Report verified visually: chronological axis, 4 rows where it previously
  showed 3, three-decimal totals matching the database exactly.

### Still not done

- **Not re-run against PostgreSQL 16.2.** The local pgserver has not started
  since 8 November. The README says so rather than repeating the old claim.
- **No page states when the data was pulled.** Import mode means every page
  is a snapshot and none of them says so.
- Power BI auto-detected two relationships between the views, one of them
  bidirectional. They were inherited, not chosen.
- Screenshots not exported to `dashboard/screenshots/`.
- The n8n scheduled workflows have still not been observed firing.

---

## 2026-11-08 — A failed check a reviewer could not read

### What the dashboard exposed

Building the exception-analysis page put every failed check on screen for the
first time, and `PO_SUPPLIER_MISMATCH` was unreadable:

    expected: supplier 5c2ef23c-96ee-4e20-82a2-3dd0e17bf9b6
    observed: supplier 85e68e06-b86c-44ea-96ac-88420236fb39

The reason the engine stores `expected` and `observed` at all is so a person
can compare them. Two UUIDs serve nobody, and the long strings wrapped to
three lines and broke the table layout, which is how it was noticed. It was
the only rule of the thirteen doing this. Nothing failed, no test caught it,
and it would have survived indefinitely had the values never been displayed —
the argument for building the view before claiming the model is right.

### Fixed

- `PurchaseOrderRecord` carries `supplier_name` and `supplier_reference`,
  both optional so a registry holding ids alone still builds a record.
- `get_purchase_order` LEFT JOINs `suppliers`. Left, not inner: a missing
  supplier row should degrade the message, not the lookup.
- `supplier_text()` in `rules/base.py` renders `Name (SUP-00N)`, falling back
  to the id only when no name is known. The reference is included because two
  suppliers can share a name.
- Both supplier ids stay in `details`, so nothing reading by machine lost
  anything.
- The two in-memory registries — `evaluation/run_benchmark.py` and
  `tests/unit/test_fixture_manifest.py` — attach the order's supplier too, so
  the replay path produces the same text as the database path.
- `tests/factories.py` gained `other_supplier_order()`. Overriding
  `supplier_id` alone, as the old test did, left the baseline supplier's name
  on a different id: a fixture half-changed in a way that makes a message
  look right while describing nothing real.

### Checked

- 209 unit tests (+2: one asserting both sides are named and the UUID is
  absent, one asserting the fallback to the id).
- **334 tests pass against PostgreSQL 18.6 in the container.** Not re-run
  against 16.2 this session: the local pgserver would not start
  (`_postmaster_info.status == 'ready'` assertion). The claim of both
  versions is therefore stale until that is repeated.
- Corpus reloaded; all five `PO_SUPPLIER_MISMATCH` rows now name both
  suppliers. `tools/reconcile_dashboard.py` passes all 13 checks.

### Watch out

Reloading re-spread the arrival timestamps from the current date, so the
daily chart now runs **2026-10-09 to 2026-11-08** rather than 2026-10-07 to
2026-11-06. The dates move every time the corpus is reloaded, which is what
inventing them means. Power BI uses Import mode and must be refreshed before
it shows any of this.

---

## 2026-11-06 (later) — Everything a dashboard needs except the clicks

### Built

- Migration `0006_reporting_timeseries`: `v_daily_processing` (arrivals per
  day and their outcomes) and `v_review_queue` (what is waiting, for how
  long, and which checks failed). The summary view answers "where do things
  stand"; neither "what has been happening" nor "what is waiting" can be
  drawn from one row of totals.
- `tools/load_corpus.py`: pushes all 200 corpus documents through the real
  path so the views have volume to show. `--spread-days` invents arrival
  timestamps across a past window so the daily chart has more than one bar,
  and the guide says on the page that they are invented.
- `tools/create_reporting_role.py`: creates `invoice_reporting` with `SELECT`
  on the six reporting views and on **no table**. It then proves the grant by
  trying to read a table and to write, and reports both refusals rather than
  asserting the role is read-only.
- `tools/reconcile_dashboard.py`: recomputes thirteen headline figures from
  the base tables, independently of the views, compares them, and prints what
  a correct dashboard must display. Exits non-zero on any disagreement.
- `dashboard/README.md`: the build guide — connection, the three pages, which
  view feeds which visual, the DAX for a filtered rate, and what every page
  must carry.

### Checked

- 200 documents loaded: 94 validated, 90 needing review, 9 unsupported, 7
  duplicate arrivals; 186 unique invoices.
- All thirteen reconciliation measures agree between the views and direct SQL.
- The read-only role reads views, and is refused on both table reads and
  writes.
- `pytest -q` → **332 passed, 0 skipped**, including four new tests for the
  two new views.

### What the data exposed

The review queue initially reported every one of 90 invoices as waiting
"under a day", because age was measured from `invoices.created_at` rather
than from when the document arrived. Those differ whenever documents are
loaded in bulk, and the business waits from the arrival. Fixed before the
view was committed; a test now backdates an arrival and asserts the age band
follows it.

### Not done

**The Power BI report itself.** Power BI Desktop is not installed, so the
`.pbix` and its screenshots do not exist. Everything up to the clicks is
prepared and reconciled. The README status table says this plainly rather
than implying a dashboard exists.

### Next, in order

1. Install Power BI Desktop, build the three pages, reconcile, export
   screenshots.
2. Publish "Recover stalled work" in n8n and confirm the sweep fires.
3. Record the demonstration; write the one-page business case and resume
   bullets from measured results only.

Nothing pushed. Nothing on the resume.

---

## 2026-11-06 — A benchmark that can fail

### Built

- `data/generate_corpus.py`: 200 synthetic documents, deterministic from one
  seed, across five layout families. Splits 120 development / 40 validation /
  40 frozen test. Duplicate families are assigned together so no split is
  scored on a document whose twin sat in another.
- A fifth layout, `kairouan`, drawn by the generator and **never taught to
  the extractor**. It appears only in the frozen test split.
- The extractor now reads a fourth layout, `sousse`, which needed two new
  capabilities: a `>` label separator, and per-layout regex rules for totals,
  because that layout puts all three amounts on one line and "rightmost
  number after the label" would read the final total three times.
- 24 documents carry faults **no rule can catch** — a supplier name that
  disagrees with a correct code, an invoice dated next year, a zero-quantity
  line, a line entered twice — each arithmetically consistent and labelled
  `detectable: false`.
- `evaluation/run_benchmark.py`: extraction, pipeline conformance and fault
  detection measured separately, familiar and unseen layouts reported apart,
  and flags raised only because a document was unreadable counted separately
  so they cannot be mistaken for detection.

### Measured

Frozen test split, run once: familiar layouts 32/32 on both layout
identification and critical fields; **unseen layout 0/8 on both, with zero
values invented**. Conformance 32/40 — the eight unreadable documents do not
reach their labelled state, and that is reported rather than excluded. Fault
recall 20/24 overall, 19/19 on faults a rule targets, **1/5 on faults no rule
targets**, and that single flag came from an unreadable layout rather than
from noticing the fault.

Development 120/120 and validation 40/40 on everything familiar;
`sousse` was taught before validation was scored and after development, which
is what the validation split is for.

### Retired

The old three-document "unseen layout" probe and
`evaluation/run_extraction_baseline.py`. Teaching `sousse` made the probe
measure nothing, and keeping a 0/3 result that had become false would have
been worse than deleting it. The unseen-layout measurement now lives in the
frozen test split with eight documents instead of three. The unit tests that
guarded the property were repointed at `kairouan`, so teaching the extractor
that layout would break them — which is the intended pressure.

### What the work turned up

- **An invisible control character in my own source.** A heredoc turned `\\b`
  into a literal backspace, so the title-detection regex was
  `"\\x08facture\\x08"` and never matched. The file *looked* correct when
  displayed. Found by inspecting the compiled function's constants after
  source reading, `re` testing and cache clearing all said it should work.
  The repository was then swept for control characters; this was the only one.
- A comprehension inside a class body cannot see that class's own names, so
  `MANIFEST["cases"]` silently resolved to the wrong manifest at module level.
- The review screen used `st.dataframe`, which pulls in pandas, whose native
  libraries this machine's Application Control policy blocks intermittently.
  A review queue needs no dataframe; it now renders Markdown tables and the
  flakiness is gone.
- n8n's five-minute notification workflow was **observed firing** (a real
  `GET /notifications?status=pending` in the API log). The fifteen-minute
  recovery workflow has still not fired: only "Process a submission" was
  published in the editor.

### Next, in order

1. Publish "Recover stalled work" in the n8n editor and confirm the sweep
   fires on its schedule.
2. Power BI over the reporting views, with the metric definitions, and
   screenshots for readers without Power BI.
3. Record the demonstration (`docs/demo_script.md` is written and marks the
   steps that do not exist yet).
4. The one-page business case and resume bullets, from measured results only.

Nothing pushed. Nothing on the resume.

---

## 2026-11-05 (week 3, part 1) — Reviewer corrections, rejection, accepted exceptions

### Built

- Migration `0005_review_actions`. A stated reason is `NOT NULL` and a check
  rejects whitespace; a check also ties the action to its revisions (a
  correction must produce one, the other actions must not).
- `review.py`: `apply_correction`, `reject_invoice`, `accept_exception`.
- A correction is submitted as a **complete set of values** in the same shape
  extraction produces, so it runs through the same normalisation and the same
  rules. Tested directly: a correction that fixes the total but mistypes a
  line amount comes back failing, rather than being trusted.
- Revalidation excludes the invoice from its own duplicate lookup — without
  that, every correction would report `BUSINESS_DUPLICATE` against itself.
- Optimistic concurrency: every action names the revision it was made
  against, and a stale edit is refused with a conflict naming the current one.
- Rejection releases the supplier and invoice number (the partial unique index
  excludes rejected invoices), so a reissued document can be accepted later.
  Tested.
- `POST /invoices/{id}/revisions`, `POST /invoices/{id}/review-actions`, and
  review history in the invoice detail.
- The register exposes `accepted_with_exception`, derived from
  `review_actions`, and `straight_through_rate` now excludes both corrected
  invoices and accepted exceptions.

### Checked

- `pytest -q` → **315 passed, 0 skipped** (207 without a database, 108 with).
- 25 new tests, including: a correction validating the invoice; a correction
  that cannot bypass a check; still-failing corrections announcing again; not
  flagging itself as a duplicate while still catching a real one; a stale
  revision refused; an empty reason refused by the database; rejection
  releasing the number for a reissue; accepting an exception leaving the
  failed check visible and excluded from straight-through; accepting an
  already-validated invoice refused.

### A mistake worth recording

Two earlier edits to `docs/business_requirements.md` **silently did nothing**.
The replacement text used ASCII hyphens (`Items 1-3`) where the file has
en-dashes (`Items 1–3`), and those particular calls did not assert that the
text was found, so the script reported success while changing nothing. The
file still carried its week-1 status line claiming requirements 4 and 8 were
unimplemented, two sessions after that stopped being true.

Found by accident, while a later edit to the same paragraph failed loudly
because that one did assert. `docs/architecture.md` also still claimed 143
unit tests. Both fixed, and the requirement status is now a table rather than
a sentence, so a stale claim is more visible.

The lesson is narrow and worth keeping: a scripted documentation edit must
assert that it matched, or it will quietly leave the old text in place and
report success.

### Still not done

No review interface — corrections work through the API and the CLI only. No
n8n: Docker Desktop is installed (CLI 29.9.2, Compose v5.6.1) but the engine
is not running, so the compose stack and the workflows are both unverified.
No OCR, no dashboard, no notification delivery.

**Actor identity is a known gap.** Reviewer actions record an actor from an
`X-Actor` header, which is a label the caller picks, not an authenticated
identity. The trail answers "what was done and why" but not "who really did
it". That needs fixing before these records are relied on.

### Next, in order

1. Start Docker Desktop (first run needs its terms accepted), then verify the
   compose stack and build the three n8n workflows.
2. Streamlit review queue over the review endpoints.
3. Power BI over the reporting views, with the metric definitions.
4. Week 4: expand the dataset, freeze a split with an unseen layout, run the
   benchmark, write the business case from measured results only.

Nothing pushed. Nothing on the résumé.

---

## 2026-11-05 (week 2, part 3) — Recovery sweep and the notification outbox

### Built

- Migration `0004_notification_outbox`. `dedupe_key` is `UNIQUE`; a check
  constraint keeps `status = 'sent'` and `sent_at` consistent, so the schema
  refuses an inconsistent pair rather than trusting the code.
- `db/jobs.py`: `claim_job` (sets a lease), `find_recoverable_jobs`,
  `enqueue_notification`, `list_notifications`, `mark_notification_sent`.
- `recovery.py`: a sweep over three cases, each reported with its reason —
  `retry_pending`, `lease_expired`, `queued_too_long`. Jobs whose attempts are
  spent are excluded; one job failing never stops the sweep.
- Notifications are enqueued **inside the work transaction**: a review case, a
  repeat arrival, and each failed attempt. Keys are derived from the subject,
  never the clock.
- `POST /jobs/recover`, `GET /notifications`,
  `POST /notifications/{id}/sent`; CLI `recover` and `notifications`.
- `INVAUT_JOB_LEASE_SECONDS` and `INVAUT_QUEUED_JOB_GRACE_SECONDS`.

### Checked

- `pytest -q` → **290 passed, 0 skipped** (207 without a database, 83 with).
- 19 new tests, including: a failed attempt recovered once its cause clears; a
  job whose worker vanished (lease backdated) picked up as `lease_expired`; a
  job nobody triggered picked up as `queued_too_long`; a *fresh* queued job
  left alone; attempts-spent left for a person; a settled submission never
  swept up however its job row looks; two sweeps producing one invoice; and
  **one poison document not blocking a healthy one behind it**.
- Notifications: one per review case, none for a validated invoice, one per
  repeat arrival, one per *failed attempt* (a retry failing again is news),
  and the unique key enforced by the database.
- Live CLI check: a review case queues one pending notification; a validated
  invoice queues none.

### Decided from the docs, not from memory

Checked the current n8n npm install page before choosing how to run it. Two
facts that change the decision: n8n supports **Node 20.19–24.x inclusive**, so
the installed Node 24.14.0 qualifies — but **npm installs are deprecated from
n8n 3.0**, and the npm method has no n8n Assistant. So the npm route works
today and is on the way out, while the supported route needs Docker. That is a
decision for Nidhal, not a default to pick quietly.

### Still not done

No orchestration driving any of this on a schedule — `recover` exists and is
tested, but something has to call it. No delivery: notifications are queued
and de-duplicated and nobody is actually told. No OCR, no review interface, no
reviewer corrections (requirement 4), no dashboard.

### Next, in order

1. n8n, once the container-versus-npm question is answered: receive a
   submission id, fetch state, skip settled work, call `/process`, route the
   outcome, create one notification when review is needed, retry with backoff,
   and a scheduled workflow that calls `/jobs/recover`.
2. Week 3: `review_actions` (migration 0005), reviewer corrections with
   revalidation of the new revision, Streamlit review queue, Power BI.

Nothing pushed. Nothing on the résumé.

---

## 2026-11-05 (week 2, part 2) — HTTP API; arrival split from processing

### Built

- **The pipeline split in two.** `record_arrival` stores the file, records the
  submission and opens a *queued* job; `process_submission` reads the stored
  file, extracts, validates and persists. `process_structured_submission` is
  now a wrapper over the shared core for callers that hold values rather than
  a file, so all 241 earlier tests kept passing unchanged.
- `storage.py`: content-addressed storage. A file lives at
  `storage/<2 chars>/<sha256>.<ext>`, so identical bytes occupy one path and
  two arrivals of the same invoice reference one blob. `resolve` refuses a
  path that escapes the root.
- `src/invoice_automation/api/`: FastAPI service. `POST /submissions`
  (multipart upload, `Idempotency-Key`), `POST /submissions/{id}/process`,
  `GET /submissions/{id}`, `POST /jobs/{id}/retry`, `GET /invoices` with
  filters and paging, `GET /invoices/{id}`, `GET /reports/summary`,
  `GET /health`. Typed schemas, one error envelope, `/docs` published.
- Auth: one service credential in `X-API-Key`, compared with
  `secrets.compare_digest`, required everywhere except `/health`. **The
  service refuses to start without one.**
- `fail_job` decides `retry_pending` versus `failed` in SQL against the row's
  own counters, so concurrent workers cannot disagree about attempts.
- CLI: `ingest` now handles PDFs *and* payloads through the real path
  (storage + split processing), replacing `ingest-pdf`. Added `serve`.

### Checked

- `pytest -q` → **271 passed, 0 skipped** (207 without a database, 64 with).
- 30 new API integration tests, including:
  - **a recoverable technical failure**: the stored file is hidden
    mid-workflow, processing returns 500 with `retryable: true`, the job goes
    `retry_pending`, the arrival and event history survive and no half-written
    invoice exists; the file is restored and `POST /jobs/{id}/retry` reaches
    `validated` on attempt 2, with one invoice from one submission.
  - processing twice does not process twice; the same idempotency key returns
    200 rather than 201 and records nothing new.
  - an identical file reuses the stored bytes and is linked when processed.
  - 401 without a credential, 415 for an unaccepted file type, 400 for an
    empty one, one error shape throughout.
  - the summary endpoint reconciles with the register.
- CLI end to end: a PDF stored once, two submissions, one invoice, duplicate
  linked.

### What the work turned up

- Response models built from view rows had `extra="forbid"`, so a view column
  the API does not publish (`duplicate_invoices`, internal ids) raised a
  validation error at response time. View-backed models now ignore extras
  while request schemas still forbid them — a new view column should not
  break the service, but a typo in a caller's payload should still be an
  error.
- `list_invoices` had a nonsense `WHERE` clause from an edit
  (`v.revision_id = r.current_revision_number IS NOT NULL`). Replaced by an
  `EXISTS` over `v_rule_failures`, which already restricts to current
  revisions, so a corrected invoice stops matching its old code.
- `tools/check_sql.py` only understood positional `%s` placeholders, so it
  could not parse the named-parameter query that contained that bug. It now
  handles `%(name)s` too — the checker had a blind spot exactly where the
  defect was.

### Still not done

No n8n. No OCR. No review interface, so reviewer corrections (requirement 4)
remain unimplemented. No recovery *sweep*: a job can be retried through the
API, but nothing yet finds overdue jobs on its own, and
`notification_outbox` does not exist.

### Next, in order

1. Migration 0004: `notification_outbox` with a deduplication key, plus
   `review_actions` for week 3.
2. A recovery command/endpoint that finds `retry_pending` jobs and stalled
   leases and resumes them, with a bounded attempt count.
3. n8n: decide container versus npm (Node 24.14.0 present, Docker not), check
   the current n8n docs for supported Node versions first. Workflow: receive a
   submission id, fetch state, skip settled work, call `/process`, route the
   outcome, create one notification when review is needed, retry with backoff
   on a transient failure.
4. Week 3: reviewer corrections and revalidation, Streamlit review queue,
   Power BI.

Nothing pushed. Nothing on the résumé.

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
