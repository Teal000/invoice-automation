# Résumé and interview material — drafts

*Drafts, 6 November 2026. Nothing here should go on a CV until you have
checked each line against the evidence named beside it. Some of these will be
wrong again the moment the project changes.*

The project is **not finished**: there is no dashboard yet, no OCR, nothing is
actually delivered by the notification outbox, and no work has been timed. The
bullets below are written to be true today.

## Bullets you can defend

Each is followed by what backs it, so you can answer "how do you know?"

> Built an invoice-processing service in Python and PostgreSQL that ingests
> supplier PDFs, extracts header and line-item values, applies thirteen
> deterministic validation rules, and routes exceptions to a review queue with
> full revision history.

*Backed by:* the code, 332 automated tests, and `docs/architecture.md`. Each
rule has an isolated test case that fails that rule and no other.

> Evaluated extraction on a frozen 40-document test split across five synthetic
> layout families, including one layout the extractor was never trained on:
> 32/32 critical-field accuracy on familiar layouts and 0/8 on the unseen
> layout, with zero incorrect values produced.

*Backed by:* `evaluation/results/benchmark_test.json`, method in
`evaluation/methodology.md`. Say both halves of that sentence — the 0/8 is
the part that shows you understand what the number is worth.

> Seeded faults no rule was designed to catch — a supplier name disagreeing
> with a correct code, a future-dated invoice, a zero-quantity line — so that
> exception recall measured a real blind spot (19/19 on targeted faults,
> 0/4 on untargeted ones) rather than restating the rule table.

*Backed by:* the corpus generator and the same results file. This is the
strongest bullet here: it shows evaluation design, not just implementation.

> Designed the data model so a submission is distinct from a canonical
> invoice, with duplicate detection enforced by database constraints
> (idempotency keys, a partial unique index) rather than application checks,
> and verified it with a concurrency test.

*Backed by:* `db/migrations/0001_initial.sql`, ADR-002/003/006 in
`docs/decisions.md`, and the two-thread test in `tests/integration/`.

> Split arrival from processing so an orchestrator can retry without the
> document being re-uploaded; a failed attempt leaves a retryable job and the
> stored document, and a recovery sweep finishes work that stalled.

*Backed by:* ADR-018, and the test that hides a stored file mid-workflow,
gets a retryable job, restores it and retries to a correct outcome.

> Connected n8n to the service over a webhook so an uploaded document is
> processed end to end by the orchestrator, with bounded retries and a
> de-duplicated notification outbox.

*Backed by:* `workflows/n8n/`, and observed runs reaching `validated` and
`needs_review`. **Caveat:** the scheduled recovery workflow has not been seen
to fire. Say "webhook-triggered" rather than implying the schedules run.

## Bullets you cannot defend yet

Do not use these. They are listed so you recognise them if they tempt you.

| Tempting claim | Why it is not supportable |
|---|---|
| "Reduced invoice processing time by X%" | Nothing has been timed, by hand or by machine. There is no measurement to quote, and an invented one is the easiest thing in this project to be caught on. |
| "99% extraction accuracy" | True only for layouts the extractor was built against. On an unfamiliar layout it is 0/8. Quoting the first without the second is the misleading version. |
| "Processes invoices for a client / in production" | It has never run outside your machine, and every document is synthetic. |
| "Detects fraudulent or duplicate invoices" | It detects repeats. It says nothing about intent, and the code deliberately avoids the word fraud. |
| "Handles scanned invoices" | There is no OCR. A scan has no text layer and yields nothing. |
| "Built a Power BI dashboard" | Not yet built. The views, the read-only role and the reconciliation exist; the report does not. |
| "Ensures tax compliance" | The 19% rate is a fictional test configuration. No rule asserts tax correctness. |

## Questions to expect, and the honest answer

**"Your accuracy is 100% — isn't that suspicious?"**
Yes, and that is why the held-out layout is in the test split. On documents
the extractor was built against it recovers every critical field; on a layout
it had never seen it recovered nothing. The second number is the one that
generalises.

**"Why rules rather than a model?"**
Because the checks are arithmetic and registry lookups, where a deterministic
rule is auditable and a model is not. The data contract is shaped so a model
could replace the extraction step later and be measured against the same
benchmark — it would have to beat 19/19 on targeted faults and improve on
0/8 for unseen layouts.

**"What happens when it gets something wrong?"**
The failed check stores what the rule expected and what the document said, so
a reviewer sees both. A correction creates a new revision and re-runs every
check, so an edit cannot bypass a rule. If a reviewer accepts an exception,
the invoice enters the register marked as accepted, the failed check stays
visible, and the straight-through rate excludes it.

**"What would you do next?"**
Score a small set of real, anonymised documents — and expect the extractor to
fail often. Then time a manual comparison, because without it there is no
claim about effort to make.

## Where it is not ready to be shown

- No dashboard, so requirement 7 of the plan is half met.
- The n8n scheduled workflows have not been observed firing.
- Reviewer actions record an actor from a header, not an authenticated
  identity: the trail answers *what* and *why* but not *who*.
