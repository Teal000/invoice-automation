# Building the dashboard

Everything except the clicks is done: the reporting views exist, a read-only
role is created, 200 synthetic documents are loaded, and every figure the
report must show is printed by a reconciliation script. What remains is
building the report in Power BI Desktop, which is a GUI job.

Power BI Desktop is not installed on this machine. When you want it:

```powershell
winget install --id Microsoft.PowerBI --accept-package-agreements --accept-source-agreements
```

## 1. Have data worth charting

With the container stack up (`docker compose up -d`):

```powershell
.\.venv\Scripts\python.exe tools\load_corpus.py --reset --spread-days 30
```

That pushes all 200 corpus documents through the real path — stored, recorded,
extracted, validated — and spreads their arrival timestamps across the past
30 days so the daily chart has more than one bar.

**Those timestamps are invented**, exactly as the documents are. Say so on the
page. Without `--spread-days` everything arrives in the same second, which is
truthful and makes a dull chart; with it, the chart is readable and the page
has to admit what it is showing.

## 2. Create the read-only connection

```powershell
.\.venv\Scripts\python.exe tools\create_reporting_role.py
```

It creates `invoice_reporting`, grants `SELECT` on the six reporting views and
**on nothing else**, then proves it: the script tries to read a table and to
write, and reports both refusals. The password is generated into `.env`.

In Power BI: **Get Data → PostgreSQL database**, server `127.0.0.1:5432`,
database `invoice_automation`, and the `invoice_reporting` credentials. Choose
**Import**, not DirectQuery — the figures are small, and Import means a
screenshot is reproducible from a file rather than depending on a live
database.

Load these six views and nothing else:

| View | Used by |
|---|---|
| `v_processing_overview` | Processing overview — the headline figures |
| `v_daily_processing` | Processing overview — what happened over time |
| `v_exception_summary` | Exception analysis — counts per rule |
| `v_rule_failures` | Exception analysis — failures by supplier and code |
| `v_review_queue` | Exception analysis — what is waiting and for how long |
| `v_invoice_register` | Invoice register |

## 3. The three pages

### Processing overview

Cards, straight from `v_processing_overview`: submissions received, duplicate
arrivals, unique invoices, invoices needing review, review rate,
straight-through rate.

A line or column chart from `v_daily_processing`: `arrival_date` on the axis,
`submissions` and `needs_review` as values. Add `failed` only if it is ever
non-zero — an always-empty series on a portfolio screenshot invites the
question of what it is for.

**Use the rates as the view gives them.** They are computed in SQL with a
stated denominator: eligible invoices, which excludes duplicates and
unsupported documents. Recreating them in DAX as
`DIVIDE(needs_review, unique_invoices)` would silently change the denominator
and produce a different, wrong number that still looks plausible. If a rate is
ever needed under a slicer, write it explicitly and reconcile it:

```dax
Review rate (filtered) =
VAR Eligible =
    CALCULATE (
        COUNTROWS ( v_invoice_register ),
        NOT v_invoice_register[business_state] IN { "duplicate", "unsupported" }
    )
VAR NeedingReview =
    CALCULATE (
        COUNTROWS ( v_invoice_register ),
        v_invoice_register[business_state] = "needs_review"
    )
RETURN
    DIVIDE ( NeedingReview, Eligible )
```

### Exception analysis

From `v_exception_summary`, a bar chart of `invoices_affected` by `code`, and
**a separate** bar chart of `failed_checks` by `code`.

Do not add those two together and do not plot them on one axis as if they
were comparable. Three bad line items are three failed checks on one invoice.
The plan is explicit about showing both without confusing them, and the
reconciliation script prints them side by side so the difference is visible.

From `v_rule_failures`, a matrix of `code` against `supplier_name`.

From `v_review_queue`, a column chart of `age_band` (ordered: under a day,
1 to 3 days, 3 to 7 days, over a week) and a table of the oldest items with
`invoice_number`, `supplier_name`, `days_waiting`, `failed_codes`.

### Invoice register

A table from `v_invoice_register`: `business_state`, `supplier_name`,
`invoice_number`, `invoice_date`, `total`, `currency`,
`purchase_order_number`, `failed_rule_codes`, `has_reviewer_edit`,
`accepted_with_exception`.

Add `accepted_with_exception` as a visible column, not a hidden flag. An
invoice a person accepted despite a failed check is in the register and is
**not** the rules having passed; the register is where that distinction is
most likely to be lost.

## 4. What every page must carry

- A visible **"Synthetic portfolio data"** label. Every view's comment says it;
  the report should too.
- A note that amounts are submitted or recorded invoice values, **not
  payments**. Nothing in this project pays anything.
- For the overview page, a note that the arrival timestamps were spread
  artificially if `--spread-days` was used.

Definitions for every measure are in
[metric_definitions.md](metric_definitions.md). Keep them in the report as a
text box or a final page — a figure whose denominator is not written down
cannot be reconciled by anyone but its author.

## 5. Reconcile before screenshotting

```powershell
.\.venv\Scripts\python.exe tools\reconcile_dashboard.py
```

It recomputes every headline figure from the base tables, independently of
the views, and prints what the dashboard must show. Open it beside the report
and compare. It exits non-zero if the views and direct SQL ever disagree.

This is the plan's requirement that dashboard aggregates reconcile with direct
SQL calculations. Doing it by eye once, before the screenshots, is the
difference between a dashboard and a picture of some numbers.

## 6. Export

Save the `.pbix` into this directory and export screenshots of each page into
`dashboard/screenshots/`, so the work can be seen by someone without Power BI.
The `.pbix` itself holds imported data; since everything here is synthetic,
that is fine to commit.

---

## 7. The report is a .pbip project, not a .pbix

The report lives in `dashboard/report/` as a **Power BI project** rather than
a binary `.pbix`. Microsoft's PBIR format stores each page and each visual as
its own JSON file with a published schema, and
[documents it as editable from outside Power BI](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report).

This is the same argument the reporting views make. `0006_reporting_timeseries.sql`
says a figure on a chart should be traceable to a view, "not to a formula
buried in a report file that nobody can diff". A `.pbix` is exactly that file.
A `.pbip` is a diff.

```
dashboard/report/
  invoice_automation.pbip
  invoice_automation.Report/definition/
    pages/pages.json                 page order and active page
    pages/<id>/page.json             name, size
    pages/<id>/visuals/<id>/visual.json
  invoice_automation.SemanticModel/definition/
    tables/*.tmdl                    one per view, with its M query
```

Turn on **Options → Preview features → Detect and reload external PBIP
changes** before editing the files. Power BI then offers to reload whenever
the JSON changes on disk, and validates it on load: a malformed file is
rejected rather than silently ignored.

Do not edit the files and the open report at the same time. Whichever saves
last wins, and neither is merged.

### What is deliberately not in the repository

`.gitignore` excludes `*.pbix`, `**/.pbi/cache.abf` and
`**/.pbi/localSettings.json`. `cache.abf` is the imported data itself, and
`localSettings.json` records absolute paths on one machine. The connection is
stored in the TMDL as host, port and database only — no username, no
password. Credentials stay in the Windows credential store, so a fresh clone
prompts for them.

### Two values to verify after any structural edit

The visuals Power BI wrote itself confirm that aggregation `Function: 0` is
Sum. Two further codes used on the invoice register page were inferred rather
than observed, so check them instead of trusting them:

| Visual | Expected | If wrong |
|---|---|---|
| Review queue by age, bar heights | 4 / 8 / 13 / 65, summing to 90 | `CountNonNull` is not function 5 |
| Review queue by age, axis order | under a day, 1 to 3 days, 3 to 7 days, over a week | `Min` is not function 3 |

Both figures come from `tools/reconcile_dashboard.py`. If the axis is
alphabetical, the sort fell back to the category and the ordering is wrong
even though the counts are right.
