# Reporting views

The views are defined in `db/migrations/0002_reporting_views.sql` and are not
duplicated here. A second copy would drift from the applied one, and the
applied one is what the dashboard reads.

Current views:

- `v_invoice_register` - one row per canonical invoice with its current revision
- `v_rule_failures` - individual failures on current revisions
- `v_exception_summary` - failures per rule code, invoices and checks counted apart
- `v_processing_overview` - counts plus review rate and straight-through rate

Definitions and denominators: `dashboard/metric_definitions.md`.
