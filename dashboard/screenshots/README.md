# Report screenshots

Three pages of the Power BI report, rendered from `report.pdf` by
`tools/export_report_images.py`. The PDF comes from File > Export > Export to
PDF in Power BI Desktop, which is the only way to get the rendered report out:
Power BI Desktop has no command line and no headless export.

**Everything shown is synthetic.** The distributor, its suppliers, its purchase
orders and every invoice are invented. Each page says so on the page itself,
which is deliberate: a screenshot separated from this file should still carry
its own caveat.

## Taken 9 November 2026, from the corpus loaded 8 November 2026

The report imports its data, so each image is a snapshot of the last refresh.
Figures below come from `tools/reconcile_dashboard.py` run against the same
database at the same time.

### 1-processing-overview.png

| | |
|---|---|
| Submissions received | 200 |
| Unique invoices | 186 |
| Needs review | 90 |
| Review rate | 0.5085 |
| Straight-through rate | 0.4915 |
| Days with arrivals | 31, 2026-10-09 to 2026-11-08 |

The arrival line is a near-perfect square wave between 6 and 7 a day. Real
arrival volume does not look like that. The timestamps were spread evenly by
`tools/load_corpus.py --spread-days 30` and are invented, as the caption on
the page says.

### 2-exception-analysis.png

Twelve rule codes, sorted by invoices affected:

| Code | Invoices affected | Failed checks |
|---|---|---|
| REQUIRED_FIELD_MISSING | 22 | 86 |
| PO_AMOUNT_MISMATCH | 11 | 11 |
| PO_NOT_FOUND | 10 | 10 |
| TOTAL_MISMATCH | 10 | 10 |
| UNKNOWN_SUPPLIER | 9 | 9 |
| LINE_TOTAL_MISMATCH | 8 | 8 |
| BUSINESS_DUPLICATE | 7 | 7 |
| CURRENCY_UNSUPPORTED | 7 | 7 |
| PO_SUPPLIER_MISMATCH | 5 | 5 |
| SUBTOTAL_MISMATCH | 5 | 5 |
| DATE_INCONSISTENT | 3 | 3 |
| UNSUPPORTED_DOCUMENT | 2 | 2 |

The engine has thirteen codes. `EXACT_FILE_DUPLICATE` is absent by design: an
identical-file arrival creates no revision, so it is counted as a duplicate
arrival on page 1 instead. Counting it in both places would let one arrival
inflate two figures.

The two bars per code are different counts and are never summed. 22 invoices
missing a required field produced 86 failed checks, because one invoice can be
missing several fields.

### 3-invoice-register.png

| Band | Invoices |
|---|---|
| under a day | 1 |
| 1 to 3 days | 9 |
| 3 to 7 days | 14 |
| over a week | 66 |

**These four numbers will not reproduce.** `v_review_queue` computes the age
bands from `now()`, so the queue ages between any two runs. The day before
this screenshot the same data gave 4 / 8 / 13 / 65. Only the total, 90, is
stable, and it matches the "Needs review" card on page 1.

The axis runs in time order because `age_band_order` says so
(`db/migrations/0007_review_queue_band_order.sql`). Sorted as text, "under a
day" lands last.

**Eight invoices in the register read `(absent)` under Invoice number**, of
which the image shows the first two: the table scrolls, and the export
captures whatever is in view. Those are documents whose invoice number could
not be read. Each is still a separate invoice: the
pipeline keys it `UNIDENTIFIED-SUBMISSION:<submission id>` so it stays visible
in the register without merging with every other unidentified document
(ADR-005). `invoice_number` is `NOT NULL` in the schema and none of these is
blank; `(absent)` is the stored display value.

The register shows `invoice_id` because the table visual groups rows that are
identical across every displayed column. Two invoices whose number, supplier,
date and total were all unreadable are identical on screen, and the page
collapsed them into one row until the key was added.

## Checking these against a live database

```powershell
.\.venv\Scripts\python.exe tools\reconcile_dashboard.py
```

It recomputes all 13 figures from the base tables and exits non-zero on any
disagreement. Every number above except the four age bands should match
exactly; the bands should differ, and if they do not, the report has not been
refreshed.
