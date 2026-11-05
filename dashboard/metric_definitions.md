# Metric definitions

Only the measures that exist today. Each one names its denominator, because a
rate without a stated denominator cannot be reconciled or compared. The SQL
is in `db/migrations/0002_reporting_views.sql`; the integration tests check
that these figures reconcile with direct SQL counts.

All figures describe synthetic portfolio data. Amounts are submitted or
recorded invoice values; this project executes no payments, so nothing here is
cash.

## Counts

| Measure | Definition | Source |
|---|---|---|
| Submissions received | Rows in `submissions`. Every arrival, including repeats. | `v_processing_overview` |
| Duplicate arrivals | Submissions whose outcome is `duplicate`. Reported separately and never counted as processed invoices. | `v_processing_overview` |
| Failed submissions | Submissions whose outcome is `failed` (a technical failure, not a business outcome). | `v_processing_overview` |
| Pending submissions | Submissions recorded but not yet carried to an outcome. | `v_processing_overview` |
| Unique invoices | Rows in `invoices`. One per canonical supplier invoice number, however many times it arrived. | `v_processing_overview` |
| Eligible invoices | Unique invoices whose state is not `duplicate` or `unsupported`. The denominator for both rates below. | `v_processing_overview` |
| Validated / needs review / unsupported / rejected invoices | Unique invoices in each business state. | `v_processing_overview` |
| Validated without edit | Validated invoices with no reviewer correction in their revision history. | `v_processing_overview` |

## Rates

| Measure | Definition | Note |
|---|---|---|
| Review rate | Invoices in `needs_review` ÷ eligible invoices | `NULL` when there are no eligible invoices, not zero |
| Straight-through rate | Invoices validated with no reviewer edit ÷ eligible invoices | An invoice a reviewer corrected is not straight-through even once it validates |

Duplicate and unsupported invoices are excluded from the denominator because
this version cannot process them; including them would flatter both rates.

## Exception counts

`v_exception_summary` reports two different numbers per rule code and does not
mix them:

- **Invoices affected** — distinct invoices with at least one failure of that
  code on their current revision.
- **Failed checks** — individual failed results. Three bad line items are
  three failed checks on one invoice.

Failures are counted on the **current revision only**, so a problem a reviewer
has since corrected stops appearing. The full history stays in
`validation_results`.

## What is deliberately absent

No extraction accuracy, no processing latency, no cost per document, no human
handling time, no recovery success rate. Those require the extractor, the
orchestration and a frozen benchmark, none of which exist yet. They will be
defined here when they are measured, with the dataset and date stated.

## One count that is deliberately not in the exception summary

`EXACT_FILE_DUPLICATE` never appears in `v_exception_summary`. That view
reports failures on an invoice's current revision, and an identical-file
arrival produces no revision: it is attached to the submission, because the
file was not reprocessed. Repeat arrivals are counted as "duplicate arrivals"
in `v_processing_overview` instead.

This is a reporting choice, not a gap. Counting the same failure in both
places would let one arrival inflate two different figures.
