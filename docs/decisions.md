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
