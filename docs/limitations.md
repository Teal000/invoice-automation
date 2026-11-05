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

167 tests pass, none skipped: 143 that need no database and 24 that do. The
schema has been applied to a live server, the 16 labelled cases produce their
labelled outcomes through the full pipeline, and the reporting views reconcile
with direct SQL queries.

Two caveats on that, both real:

- **The version tested is PostgreSQL 16.2, not the documented 18.** The tests
  ran against a project-local server bundled as a Python wheel, because no
  system PostgreSQL was installed (see decisions.md ADR-012). Every feature
  the schema uses exists in both versions, so the risk is low, but PostgreSQL
  18 remains untested.
- **Testing happened on one machine, with one user, on synthetic data of a
  known shape.** The concurrency test runs two threads; it is not a load test
  and says nothing about behaviour under real contention or volume.

What the passing suite does *not* show: that values can be read off a real
invoice, that the workflow survives a service failure (no retry or recovery
path exists yet), or that any of this is faster than a person. Those need the
extractor, the orchestration and a measured benchmark.

## Not a product

This is a portfolio project. It executes no payments, connects to no bank, and
posts to no accounting system. "Accepted" means recorded in a demonstration
register, nothing more.
