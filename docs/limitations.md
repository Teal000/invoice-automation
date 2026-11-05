# Limitations

What this project does not establish. Kept current as the work proceeds; if a
line here becomes untrue, it is because the capability was built and measured,
and the change will say so.

## What has been measured, and what it is worth

One thing has been measured: extraction from the 16 synthetic PDFs, with the
method, denominators and date in `evaluation/methodology.md` and the figures
in `evaluation/results/`.

It scores 16/16. That number is close to uninformative and should not be
quoted on its own: the three layouts and the extractor's label sets were
written side by side, the set is 16 documents, and nothing is held out. It
says the extractor reads the documents this generator draws.

The figure worth quoting is the held-out layout: faced with a fourth layout it
was never taught, the extractor recognised none of the three documents,
recovered none of their 30 populated fields, and **invented no values** — the
documents went to review instead. That is a real property, and it is the one
that would matter on a supplier's unfamiliar template.

No latency, cost, human-handling-time, time-saving or adoption figure is
reported anywhere in this repository, because none has been measured.

## The data is synthetic

Every supplier, purchase order, invoice and amount is invented. The French
labels and TND amounts reflect a Tunisian business setting; the 19% tax rate
is a fictional test configuration, not Tunisian tax guidance, and no rule here
asserts tax compliance. Each generated PDF carries a visible notice saying it
is synthetic.

Synthetic documents are generated from assumptions about how invoices look,
and here the same person wrote those assumptions and the code that reads
them. Results on this data reflect that circularity. It is the reason the
held-out layout exists, and the reason the headline accuracy figure is
reported next to it rather than on its own.

## What is not implemented

- **No OCR.** Extraction reads the text layer of a digitally generated PDF. A
  scanned document has no text layer, so it yields nothing and goes to
  review. Tesseract is not installed on the development machine and no OCR
  path has been written.
- **Extraction is tied to known layouts.** Three label sets are supported. An
  unfamiliar template is not read at all, which is safe but is not the same
  as handling it. Real suppliers would each need their labels added, or a
  different approach.
- No orchestration. The API exposes arrival, processing and retry, but
  nothing drives them on a schedule and no sweep finds overdue jobs: a
  `retry_pending` job waits for someone to retry it. Notifications do not
  exist, so notification de-duplication is untested.
- No review interface and no dashboard.
- **The API has one service credential, not users.** There are no roles,
  sessions or per-person identity, so "who overrode this check" cannot yet be
  answered, and reviewer actions will need that before they are built.
- No reviewer corrections. The revision table and the audit trail support
  them; the action that creates a correction does not exist yet, so the
  "reviewer can correct and revalidate" requirement is unmet.
- No recovery sweep. A failed job is retryable through the API and that path
  is tested, but nothing finds stalled work by itself, and the lease column
  is recorded and not yet used.

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

271 tests pass, none skipped: 207 that need no database and 64 that do. The
schema has been applied to a live server, the 16 labelled cases produce their
labelled outcomes through the full pipeline as payloads **and** as PDFs, and
the reporting views reconcile with direct SQL queries.

Two caveats on that, both real:

- **The version tested is PostgreSQL 16.2, not the documented 18.** The tests
  ran against a project-local server bundled as a Python wheel, because no
  system PostgreSQL was installed (see decisions.md ADR-012). Every feature
  the schema uses exists in both versions, so the risk is low, but PostgreSQL
  18 remains untested.
- **Testing happened on one machine, with one user, on synthetic data of a
  known shape.** The concurrency test runs two threads; it is not a load test
  and says nothing about behaviour under real contention or volume.

What the passing suite does *not* show: that values can be read off a **real**
invoice, or that any of this is faster than a person. The first needs real
documents; the second needs a timed comparison that has not been attempted.

One failure mode *is* now shown: processing that dies part way through leaves
the stored document, the event history and a retryable job, and the retry
reaches a correct outcome without the document being uploaded again. That is
one tested failure, not a general claim of robustness.

## Not a product

This is a portfolio project. It executes no payments, connects to no bank, and
posts to no accounting system. "Accepted" means recorded in a demonstration
register, nothing more.
