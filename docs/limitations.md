# Limitations

What this project does not establish. Kept current as the work proceeds; if a
line here becomes untrue, it is because the capability was built and measured,
and the change will say so.

## No measured performance

No accuracy, latency, cost, review-rate, time-saving or adoption figures are
reported anywhere in this repository, because none have been measured. The
test suite shows that the implemented rules behave as specified on cases
written for them. That is a correctness statement about the code, not a
performance claim about invoice processing.

When a benchmark is run it will state the dataset, the configuration, the
denominators and the date, and the figures will live in
`evaluation/results/`.

## The data is synthetic

Every supplier, purchase order, invoice and amount is invented. The French
labels and TND amounts reflect a Tunisian business setting; the 19% tax rate
is a fictional test configuration, not Tunisian tax guidance, and no rule here
asserts tax compliance.

Synthetic documents are generated from assumptions about how invoices look.
Results on them reflect those assumptions. A high score on this data would say
nothing about real supplier documents, which vary in ways a generator does not
anticipate.

## What is not implemented

- No extraction. The current input is structured values, not documents. There
  is no PDF parsing and no OCR, so nothing here shows that values can be read
  off a real invoice.
- No HTTP API, no orchestration, no review interface, no dashboard.
- No reviewer corrections. The revision table and the audit trail support
  them; the action that creates a correction does not exist yet, so the
  "reviewer can correct and revalidate" requirement is unmet.
- No retry or recovery path. The job table records attempts and leases; the
  retry and recovery workflows are not written, so interruption recovery is
  untested.
- No notifications, so notification de-duplication is untested.

## Scope the rules deliberately refuse

Partial deliveries, split purchase orders, invoices spanning several orders,
credit notes and currency conversion are out of scope. Such a document gets an
explicit exception code rather than an approximate match. This is a correct
refusal, not a detection capability: the system recognises the document kinds
and currencies it was told about, and an unfamiliar one is reported as
unrecognised.

## What the duplicate checks do and do not mean

A duplicate here is a process event. `EXACT_FILE_DUPLICATE` means identical
bytes arrived twice. `BUSINESS_DUPLICATE` means two different documents claim
one supplier invoice number. Neither detects fraud, and neither is evidence of
intent. Near-duplicates that differ in both file content and invoice number —
the same goods invoiced twice under different numbers — are not detected at
all.

## Verification status

Unit tests run on every change and pass. They include a replay of the whole
labelled fixture set in memory, so the rules, the fixtures and the ground
truth are known to agree with each other without a database.

The integration tests, which cover persistence, the duplicate guards,
idempotency, the append-only event log and reconciliation between the
reporting views and direct SQL, were written before a PostgreSQL server was
available on the development machine. Until they have been run, the schema is
syntax-checked (with the PostgreSQL parser, via `tools/check_sql.py`) but
never executed, and nothing about the database behaviour should be taken as
demonstrated: not the column names, not the `ON CONFLICT` inference on the
partial index, not the trigger, not the four views. The README records which
state the project is in.

## Not a product

This is a portfolio project. It executes no payments, connects to no bank, and
posts to no accounting system. "Accepted" means recorded in a demonstration
register, nothing more.
