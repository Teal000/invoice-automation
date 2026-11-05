# Decisions

Short records of choices that are not obvious from the code, with the reason
and what would change the decision. Newest first within each numbered entry.

## ADR-001 — Local PostgreSQL instead of Docker Compose for the first slice

**Date:** 5 November 2026
**Status:** accepted

The plan assumes Docker Compose for the database, n8n, the API and the review
interface, and gives reproducibility as the reason. Docker is not installed on
the development machine (`docker --version` → command not found), and neither
was PostgreSQL.

The options were: install Docker Desktop (WSL2, several GB, a restart), install
PostgreSQL natively, or use SQLite for the first week. SQLite was rejected
because the schema here leans on PostgreSQL features that carry the design —
partial unique indexes for the duplicate guard, `jsonb` for raw values,
`FILTER` in the reporting views — and porting them twice is wasted work on a
database that would be replaced. Native PostgreSQL 18 was chosen: it exercises
the real schema immediately, with no virtualisation layer.

**Consequence:** `compose.yaml` exists but has never been executed on this
machine, and says so in its header. The README does not claim a reproducible
container setup. When n8n arrives in the next increment it will need either
Docker or a Node installation (Node 24.14.0 is present), and that decision is
still open.

**What would change it:** installing Docker Desktop. The migrations and the
application are not tied to how the server is started, so moving to a
container means changing the connection settings in `.env` and nothing else.

## ADR-002 — A submission is not an invoice

**Date:** 5 November 2026
**Status:** accepted

Every arrival is recorded in `submissions` with its own document row. A
canonical business record lives in `invoices`. The same invoice can arrive
several times; each arrival is visible, and only the first creates a canonical
record.

This is what lets duplicate handling be honest. A duplicate arrival is an
event, not an error to be swallowed, and it is never counted as a processed
invoice in the reporting views.

## ADR-003 — Duplicate detection has two distinct meanings

**Date:** 5 November 2026
**Status:** accepted

`EXACT_FILE_DUPLICATE` means the identical bytes arrived again (same
SHA-256). The arrival is recorded and linked to the earlier submission, and
the remaining checks are deliberately **not** run: reprocessing the same file
can only produce the same answer, and running the checks again would double
the stored rule outcomes. This is the one short circuit in the engine.

`BUSINESS_DUPLICATE` means a *different* file claims a supplier and invoice
number that already exist. Both files are kept, the new values are recorded as
a new revision of the existing invoice, and the invoice goes to review —
including when it had already been validated, because two live claims on one
invoice number is exactly the case a person should decide. No second canonical
invoice is created.

Neither is described as fraud. Both are process events.

## ADR-004 — Conservative identifier normalisation

**Date:** 5 November 2026
**Status:** accepted

Supplier invoice numbers, supplier references and PO numbers are normalised by
trimming, collapsing internal whitespace runs to one space, and upper-casing.
No characters are removed.

Stripping hyphens, dots or leading zeros would merge genuinely different
documents: `FA-001` and `FA001` can be two different invoices. A false
duplicate is worse than a missed one here, because a false duplicate blocks a
real invoice from being recorded. The original string is always stored next to
the normalised one.

## ADR-005 — Duplicate identity when the supplier is unknown

**Date:** 5 November 2026
**Status:** accepted

`invoices.supplier_key` is the resolved supplier id when the supplier
resolves, and `unresolved:<normalised reference>` when it does not. Two
documents from the same unknown supplier with the same invoice number are
still one business invoice, and an unresolved key can never collide with a
real supplier id.

When no invoice number could be read at all, the invoice is keyed
`UNIDENTIFIED-SUBMISSION:<submission id>`. That keeps the record visible in
the register without merging it with every other unidentified document.

## ADR-006 — Uniqueness is enforced by the database

**Date:** 5 November 2026
**Status:** accepted

The duplicate guard is a partial unique index on
`(supplier_key, invoice_number_normalized) WHERE business_state <> 'rejected'`,
and the invoice insert uses `ON CONFLICT ... DO NOTHING` followed by a read.
Two simultaneous submissions therefore cannot both create an accepted invoice,
whatever the application code does. The predicate excludes rejected invoices
so a corrected document can be accepted after a rejection.

Idempotency is enforced the same way: `submissions.idempotency_key` is unique
in the database, not merely checked before inserting.

## ADR-007 — A skipped check is not a passed check

**Date:** 5 November 2026
**Status:** accepted

Rule results carry three statuses: `pass`, `fail` and `skipped`. A check that
cannot be evaluated — a purchase-order comparison when no PO number was read —
is stored as skipped with the reason. An invoice is only `validated` when
nothing failed, and the stored statuses let a report tell a genuine pass from
an absent one. The alternative, treating "nothing to check" as a pass, would
inflate every quality figure this project later reports.

## ADR-008 — `TOTAL_MISMATCH` checks internal consistency, not tax correctness

**Date:** 5 November 2026
**Status:** accepted

The rule compares `subtotal + tax_total` with `total`. It does not check the
tax amount against the configured 19% rate. That rate drives the synthetic
document generator so the fixtures are internally consistent; using it as a
validation rule would amount to a tax-compliance claim, which this project
explicitly does not make. If a tax-rate conformance check is added later it
gets its own code, so the two cannot be confused in reports.

## ADR-009 — Purchase-order amount tolerance has two parts

**Date:** 5 November 2026
**Status:** accepted

A difference passes if it is within **either** an absolute allowance (1.000
TND) or a share of the order amount (2%). The allowance is computed from the
*order* amount, not the invoice total, because the order is the reference
value. Both numbers and the applied allowance are stored on every result, so a
borderline case can be explained afterwards.

## ADR-010 — No ORM

**Date:** 5 November 2026
**Status:** accepted

psycopg 3 with hand-written SQL and `dict_row`. The schema, the constraints
and the reporting views are the substance of this project; an ORM would hide
exactly the part a reviewer should be able to read. The cost is that column
and type errors surface only against a live database, which is why the
integration tests exist and why `tools/check_sql.py` catches syntax errors
earlier.

## ADR-011 — Core package outside `apps/`

**Date:** 5 November 2026
**Status:** accepted

The plan's repository layout puts the API under `apps/api/`. The rules,
models, pipeline and database access live in `src/invoice_automation/` instead,
because the API, the CLI, the review interface and the benchmark all need them.
`apps/api/` and `apps/review_ui/` will hold the service entry points that
import this package.

## ADR-012 — A project-local PostgreSQL for development, alongside the documented install

**Date:** 5 November 2026
**Status:** accepted

ADR-001 chose a local PostgreSQL 18 service. That install did not happen, and
the schema sat unexecuted. Rather than leave the database layer unverified,
`tools/pg_dev_server.py` starts a server from the `pgserver` wheel, which
bundles PostgreSQL binaries: `initdb` into the git-ignored `pgdata/`, no
administrator rights, no Windows service, removed by deleting one folder.

On Windows that server binds a free port chosen at start time, so the tool
writes the discovered host and port into `.env`. The application and the tests
read `.env` and neither knows which kind of server is behind it.

**Consequence:** the version tested against is the bundled one, PostgreSQL
16.2, not the documented 18. The README says so. Everything the schema uses
exists in 13 and later, so the gap is small, but it is a real gap and is
reported rather than glossed over. When PostgreSQL 18 is installed, stopping
this server and correcting the port in `.env` is the whole migration.

## ADR-013 — Connections are autocommit; transactions are explicit blocks

**Date:** 5 November 2026
**Status:** accepted

`connect()` returns connections in autocommit mode, and atomic work is wrapped
in `with conn.transaction():`.

This is not a style preference. With autocommit off, psycopg opens an implicit
transaction on the first statement, so a read performed before a
`conn.transaction()` block means the block is no longer the outermost
transaction; psycopg then implements it as a savepoint, and leaving the block
commits nothing. Both the migration runner and the pipeline read before
writing, so on the first run against a real server both reported success and
persisted nothing — no error anywhere, and the work vanished when the
connection closed.

The regression tests for it assert from a *second* connection, because an
assertion on the same connection sees uncommitted work and passes either way.

## ADR-014 — pdfplumber for extraction, not PyMuPDF

**Date:** 5 November 2026
**Status:** accepted

Extraction uses pdfplumber (MIT, over pdfminer.six) and generation uses
reportlab. PyMuPDF is faster and was the obvious alternative, but it is
AGPL-3.0: linking it would place its terms on this repository, which is meant
to be shown to employers without a licence conversation.

pdfplumber also gives every word with its coordinates, which is what the
"field evidence" part of the data contract needs: the page, the source line
and its bounding box are stored for each extracted value. A text-only reader
such as pypdf would not support that.

## ADR-015 — Extraction is geometric, not regex over a text dump

**Date:** 5 November 2026
**Status:** accepted

Words are grouped into lines by vertical position, and table cells are
assigned to columns using boundaries derived from the header row. Labels are
matched **start-anchored** against a line.

Start-anchoring is not a detail. A document carrying both "Fournisseur :" and
"Code fournisseur :" would hand a contains-match the wrong line, silently
putting the supplier's name where its code belongs. Likewise, amounts are read
by taking the rightmost run of numeric tokens, which is why "Taxe (19%)
291,574" yields 291,574 rather than 19, and why "1 337,750" is rejoined into
one amount instead of read as 1.

## ADR-016 — An unreadable document is a review case, not an excluded kind

**Date:** 5 November 2026
**Status:** accepted

"This is a credit note and we do not handle credit notes" and "we could not
read this document" are different outcomes and now have different paths. The
extractor reports an unclassifiable document as `unknown`; normalisation
records that as a missing required field, so `REQUIRED_FIELD_MISSING` fires
and the invoice goes to **review**. `UNSUPPORTED_DOCUMENT` is skipped, and
fires only for a kind that was read and is excluded.

Found while building the held-out layout probe: an unreadable document was
being recorded as `unsupported`, which would have moved documents the system
simply failed on into a bucket intended for cases a person cannot fix either,
and quietly flattered the review rate.

## ADR-017 — A held-out layout the extractor is never taught

**Date:** 5 November 2026
**Status:** accepted

The generator draws a fourth layout, `sousse`, whose labels are deliberately
absent from the extractor. Three documents are rendered in it and measured
separately.

Without it the evaluation is the extractor marking its own homework: the three
supported layouts and its label sets were written side by side, so a high
score on them is close to uninformative. The probe measures the only thing
that generalises — what happens when the layout is unknown — and the number
that matters is how many values were invented, not how many were read.

Adding `sousse` labels to `extraction/layouts.py` would destroy that
measurement. The manifest and the methodology both say so.

## ADR-018 — Arrival and processing are separate steps

**Date:** 5 November 2026
**Status:** accepted

`record_arrival` stores the file, records the submission and opens a **queued**
job. `process_submission` reads the stored file, extracts, validates and
persists. The API exposes both: `POST /submissions` and
`POST /submissions/{id}/process`.

Three things follow, none of which work if the two are one call:

- An orchestrator has something to orchestrate. It can be told that a
  submission arrived, decide when to process it, and retry processing without
  the document passing through it again.
- A retry needs no re-upload. The bytes are already stored and the job row
  carries the attempt count, so a failed attempt is resumable.
- An unreadable upload still gets a receipt. Deciding what a document is
  belongs to processing, not to the door.

`process_submission` is safe to call repeatedly: a submission that already
reached an outcome is reported rather than processed again, so a duplicated
trigger cannot produce a second invoice.

## ADR-019 — Documents are stored under their own hash

**Date:** 5 November 2026
**Status:** accepted

A stored file's path is its SHA-256: `data/storage/40/4044...c252.pdf`.
Identical bytes therefore occupy one path however many times they arrive, and
two arrivals of the same invoice produce two submissions and two document rows
pointing at one blob — which is exactly the relationship the duplicate
handling describes.

It also means re-storing is a no-op that cannot corrupt anything: the name is
derived from the content, so writing the same content again produces the same
file. The size is still verified rather than assumed, so a truncated earlier
write is repaired instead of trusted.

`storage.resolve` refuses a path that escapes the storage root, because the
path is read from the database and a database value is not automatically a
safe filename.

## ADR-020 — One service credential, and no start without it

**Date:** 5 November 2026
**Status:** accepted

The API authenticates with a single internal credential in the `X-API-Key`
header, compared with `secrets.compare_digest`, required on every endpoint
except `/health`. The plan allows exactly this for n8n-to-API calls in a local
deployment.

It is **not** a user authentication system, and the API documentation says so
rather than letting a reader assume otherwise. There are no users, roles or
sessions; when reviewer actions arrive they will need a real identity, since
"who overrode this check" is part of the audit trail.

The service **refuses to start** when the credential is empty. A service that
quietly runs open to anything that can reach the port is a worse failure than
one that will not boot, and the failure is silent while the other is loud.

## ADR-021 — `/health` is the only endpoint that reports rather than fails

**Date:** 5 November 2026
**Status:** accepted

`/health` catches a database error and returns `status: degraded` with the
exception class, rather than raising. Everywhere else a database error is a
500 with the standard error envelope.

A health endpoint that returns 500 when the database is down tells a monitor
only that something is wrong; one that answers with *what* is wrong is the
point of having it. It deliberately does not report the connection string,
the credential, or the error text, which can carry both.

## ADR-022 — Notifications go to an outbox, not straight out

**Date:** 5 November 2026
**Status:** accepted

A notification is written into `notification_outbox` inside the same
transaction as the work that caused it. Delivery is a separate step.

Sending cannot be rolled back. If processing announced a review case and then
failed, the reviewer would have been told about an invoice that does not
exist. Writing the intent with the work and delivering afterwards makes the
announcement exactly as durable as the thing it announces.

Nothing in this version delivers anything: there is no mail transport, no
webhook, no chat integration. `status = 'sent'` records what a caller
reported, which is why the channel is stored next to it, and the API and CLI
both say so where a reader would otherwise assume messages are going out.

## ADR-023 — The deduplication key is derived from the subject, never the time

**Date:** 5 November 2026
**Status:** accepted

`review_required:<invoice id>:<revision id>`,
`duplicate_received:<submission id>`,
`processing_failed:<submission id>:attempt-<n>`. The column is `UNIQUE`, so
the database refuses the second one.

Processing can legitimately run more than once for the same submission — a
repeated trigger, a retry after a timeout, a recovery sweep — and a reviewer
must not hear about it three times. Keying on the subject rather than the
moment means a repeat is a collision, while something genuinely new gets
through: a second document claiming an invoice number creates a new revision,
so it notifies, and a second failed attempt notifies because a retry failing
again is news.

## ADR-024 — Leases, and what makes taking a job safe

**Date:** 5 November 2026
**Status:** accepted

Claiming a job sets `lease_expires_at = now() + INVAUT_JOB_LEASE_SECONDS`. A
job still marked `running` after that is treated as stalled and may be taken
by a recovery sweep.

A lease is a timeout, not a promise: a worker that is merely slow can have its
job taken. That is only safe because processing is idempotent — a submission
that already reached an outcome is reported rather than processed again, so
two workers on one job cannot produce two invoices. The safety comes from
that property, not from the timeout being generous.

A recovery sweep picks up three cases and reports which: `retry_pending` (an
attempt failed, attempts remain), `lease_expired` (claimed and abandoned), and
`queued_too_long` (recorded but never triggered — without this an arrival
nobody processed would sit unnoticed for ever). A job whose attempts are spent
is excluded: it is `failed` and wants a person, not another automatic attempt.

One job failing never stops the sweep. Each is handled on its own and its
error reported, because one poison document must not block everything queued
behind it.

## ADR-025 — Docker, and n8n in a container rather than via npm

**Date:** 5 November 2026
**Status:** accepted, superseding ADR-001 for the orchestrator

ADR-001 chose a local PostgreSQL over Docker because Docker was not installed.
That decision is now reversed for the orchestrator, after checking the current
n8n documentation rather than assuming:

- n8n supports Node 20.19 to 24.x inclusive, so the installed Node 24.14.0
  would qualify for an npm install.
- **But npm installs are deprecated from n8n 3.0**, and 3.1.0 already exists
  as a pre-release. Building a portfolio project on a method the vendor is
  retiring would mean the setup instructions aged out before anyone read them.

So n8n runs in a container, which also gives `compose.yaml` a reason to exist
after being written-but-unexecuted since week 1, and makes the reproducible
setup the plan asked for real rather than aspirational.

The n8n image is pinned to the stable release current on this date. The docs
do not require pinning, but an orchestrator that changes under its own
workflows is not reproducible, which is the whole point of the exercise.

n8n keeps its own data, credentials included, in its own volume rather than
sharing this project's database. Mixing an orchestrator's tables into the
invoice schema would blur what is authoritative about an invoice, and the
answer must stay "PostgreSQL, not the execution history".

**Consequence:** the Dockerfile and compose file are written but have never
been built or run, and both say so in their headers. Everything the project
does today still runs without Docker, so the container path is an addition
rather than a dependency.

## ADR-026 — A correction is submitted as values, and re-checked like any other

**Date:** 5 November 2026
**Status:** accepted

A reviewer submits a complete set of corrected values in the same shape
extraction produces, and those values go through the same normalisation and
the same rules.

The alternative — patching individual fields and trusting them because a
person typed them — would let a reviewer produce an invoice that was never
checked. It is tested directly: a correction that fixes the stated total but
mistypes a line amount comes back as `LINE_TOTAL_MISMATCH` and
`SUBTOTAL_MISMATCH` rather than validating. A rule added later applies to
corrections without anybody remembering to wire it up.

Revalidation excludes the invoice being revalidated from the business-duplicate
lookup. Without that, an invoice finds its own supplier and number in the
register and reports itself as a duplicate, which would make every correction
fail.

## ADR-027 — Edits name the revision they were made against

**Date:** 5 November 2026
**Status:** accepted

Every reviewer action carries `expected_revision_number`. If the invoice has
moved on, the action is refused with a conflict naming the current revision
rather than applied.

Two reviewers looking at the same exception is the normal case for a review
queue, and the second one's edit would otherwise silently discard the first
one's. Refusing is noisy and recoverable; overwriting is quiet and is not.

A stated reason is required for every action and the database enforces it,
including rejecting whitespace. An override with no reason is the one thing an
audit trail cannot reconstruct later: "someone approved it" does not answer
"why was this approved".

## ADR-028 — An accepted exception is not a passed check

**Date:** 5 November 2026
**Status:** accepted

A reviewer can accept an invoice into the register despite a failed check. It
ends in the same business state as one the rules validated, because that is
what acceptance means — but the reporting keeps them apart:

- `v_invoice_register.accepted_with_exception` marks it, derived from
  `review_actions` so there is no second copy of the fact to drift.
- The failed check stays on the current revision. Nothing is rewritten to
  make the invoice look clean.
- `straight_through_rate` counts only invoices validated with **no** reviewer
  correction and **no** accepted exception.

This is the easiest number in the project to get quietly wrong. A
straight-through rate that counted invoices a person had to decide about would
overstate how much the rules actually handle, and that overstatement is
exactly what a portfolio project is tempted into. The API response says so in
words too, so a reader of the JSON cannot miss it.

Acceptance is refused for an invoice that is not in review: accepting one that
passed everything would record a decision nobody made.
