# Business requirements

## The process this addresses

A small distributor receives supplier invoices as PDFs. An operations analyst
enters the details into a system, checks the arithmetic, looks for documents
that have already been received, compares each invoice with its purchase
order, and follows up on anything that does not agree. Reporting on what
happened means further spreadsheet work.

The parts that repeat — re-entry, arithmetic checking, duplicate spotting,
order matching — are mechanical and rule-based. The parts that need judgement
— deciding what to do about a mismatch, accepting a documented exception —
are not. The aim is to automate the first set, make the second set visible and
quick to act on, and leave a record of both.

The distributor, its suppliers, its purchase orders and every invoice in this
repository are invented.

## What the system must do

1. Accept a document through one supported interface and carry it to a
   recorded outcome.
2. Keep every extracted value connected to the document it came from.
3. Detect, deterministically, the arithmetic, duplicate, supplier and
   purchase-order problems listed in the rule table below.
4. Let a reviewer correct values, record why, and have the checks run again.
5. Ensure repeated submissions and retries create neither duplicate business
   records nor repeated notifications.
6. Survive interruption without losing the original document or the event
   history.
7. Produce SQL reports and a dashboard that reconcile with the underlying
   records.
8. Produce evaluation results reproducible from a documented dataset and
   configuration.

Where each stands today:

| # | Requirement | State |
|---|---|---|
| 1 | A document reaches a recorded outcome | Met, for PDFs and structured payloads, through the CLI and over HTTP |
| 2 | Extracted values stay connected to their source | Met: the document is stored under its hash, and each value records the page, source line and bounding box it came from |
| 3 | Deterministic detection of the listed problems | Met: 13 rules, each with an isolated test case |
| 4 | A reviewer can correct, explain and revalidate | Met in the API and CLI; there is no review *interface* yet |
| 5 | Repeats and retries create no duplicate records or notifications | Met: idempotency keys, duplicate guards and a de-duplicated outbox, all enforced by database constraints |
| 6 | Interrupted processing recovers without losing the document or history | Met for processing: a failure leaves the stored document, the event trail and a retryable job, and a sweep finishes stalled work |
| 7 | SQL reports and a dashboard reconcile with the records | Met: six reporting views, a three-page Power BI report reading them through a read-only role, and `tools/reconcile_dashboard.py` checking all 13 figures against the base tables |
| 8 | Reproducible evaluation | Half met: the extraction baseline runs from a documented command; the wider benchmark the plan describes does not exist |

The README states what is built and how it was checked.

## Validation rules

| Code | Check | Outcome when it fails |
|---|---|---|
| `REQUIRED_FIELD_MISSING` | Every required field is present and parseable | Review |
| `UNKNOWN_SUPPLIER` | The supplier reference resolves to an active registry entry | Review |
| `EXACT_FILE_DUPLICATE` | This file hash has not been received before | Linked to the earlier submission, not reprocessed |
| `BUSINESS_DUPLICATE` | No invoice exists for this supplier and invoice number | Review; both files kept |
| `LINE_TOTAL_MISMATCH` | Quantity × unit price matches the line amount | Review |
| `SUBTOTAL_MISMATCH` | Line amounts sum to the subtotal | Review |
| `TOTAL_MISMATCH` | Subtotal plus document tax matches the total | Review |
| `CURRENCY_UNSUPPORTED` | The currency is in the configured set | Unsupported |
| `PO_NOT_FOUND` | The referenced purchase order exists | Review |
| `PO_SUPPLIER_MISMATCH` | The invoice supplier is the order supplier | Review |
| `PO_AMOUNT_MISMATCH` | The difference is within the configured tolerance | Review |
| `DATE_INCONSISTENT` | The due date is not before the invoice date | Review |
| `UNSUPPORTED_DOCUMENT` | The document is a plain invoice | Unsupported |

Required fields are `document_type`, `invoice_number`, `supplier_reference`,
`invoice_date`, `currency`, `purchase_order_number`, `subtotal`, `tax_total`,
`total`, at least one line item, and on each line its description, quantity,
unit price and line amount. `due_date` is optional.

`document_type` is required because an extractor that cannot tell what a
document is has failed to read a field, and that is a review case. It is kept
separate from `UNSUPPORTED_DOCUMENT`, which is for a kind that *was* read and
is excluded, such as a credit note. Merging the two would move documents the
system merely failed on into a bucket meant for cases nobody can fix.

Every failed check stores the expected value, the observed value and the field
it concerns. A check that cannot be evaluated is stored as skipped with the
reason, and is never treated as a pass.

## Configuration, stated explicitly

| Setting | Value | Note |
|---|---|---|
| Currency | TND | One currency; no conversion |
| Decimal places | 3 | TND is divided into 1000 millimes |
| Rounding | `ROUND_HALF_UP` | Applied once, when normalising |
| Arithmetic tolerance | 0.001 | One millime |
| Order tolerance | 1.000 TND or 2% of the order amount, whichever is larger | |
| Tax rate | 19% | Fictional test configuration for the document generator, not a validation rule and not tax guidance |

## Business and job state

Business state, recorded on the invoice: `received`, `processing`,
`validated`, `needs_review`, `duplicate`, `unsupported`, `rejected`.

Job state, recorded on the processing job: `queued`, `running`, `succeeded`,
`retry_pending`, `failed`.

They are kept apart deliberately. A job that succeeded technically can leave
an invoice needing review, and a failed job says nothing about whether the
invoice is correct.

"Accepted" in this project means accepted into the demonstration register. It
does not mean approved for payment. A reviewer accepting a documented
exception is a different thing from every automated check passing, and the
reports distinguish the two.

## Out of scope in this version

- Payment execution, bank connections, posting to a real accounting ledger.
- Currency conversion.
- Partial deliveries, split purchase orders, invoices spanning several orders,
  credit-note settlement.
- Any claim of tax compliance or fraud detection.
- A chatbot, an autonomous agent, or a trained model.

A document that needs one of the excluded cases receives an explicit
exception code. It is never approximated into something the system can handle.
