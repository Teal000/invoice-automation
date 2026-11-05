-- 0002_reporting_views.sql
-- Reporting views. Every figure these views produce has its denominator
-- written down here and repeated in dashboard/metric_definitions.md, so a
-- number on a dashboard can always be traced to a definition.
--
-- Amounts are submitted or accepted invoice values. They are not confirmed
-- payments: this project does not execute or record payments.

-- ---------------------------------------------------------------------------
-- The current state of each invoice, flattened for reporting.
-- ---------------------------------------------------------------------------
CREATE VIEW v_invoice_register AS
SELECT
    i.invoice_id,
    i.business_state,
    i.supplier_id,
    s.name                       AS supplier_name,
    r.supplier_reference,
    i.invoice_number,
    r.invoice_date,
    r.due_date,
    r.currency,
    r.subtotal,
    r.tax_total,
    r.total,
    r.purchase_order_number,
    r.revision_number            AS current_revision_number,
    r.source                     AS current_revision_source,
    i.created_at                 AS first_recorded_at,
    i.updated_at                 AS last_changed_at,
    -- Distinct failed rule codes on the current revision. Counted distinct on
    -- purpose: three failed line checks are one kind of problem, and the
    -- per-failure count is available separately in v_rule_failures.
    (
        SELECT count(DISTINCT v.code)
        FROM validation_results v
        WHERE v.revision_id = i.current_revision_id AND v.status = 'fail'
    )                            AS failed_rule_codes,
    (
        SELECT count(*)
        FROM validation_results v
        WHERE v.revision_id = i.current_revision_id AND v.status = 'fail'
    )                            AS failed_checks,
    -- True when a reviewer has edited this invoice at least once.
    EXISTS (
        SELECT 1 FROM invoice_revisions ir
        WHERE ir.invoice_id = i.invoice_id AND ir.source = 'reviewer_correction'
    )                            AS has_reviewer_edit
FROM invoices i
LEFT JOIN invoice_revisions r ON r.revision_id = i.current_revision_id
LEFT JOIN suppliers s ON s.supplier_id = i.supplier_id;

COMMENT ON VIEW v_invoice_register IS
    'One row per canonical invoice, with its current revision. Synthetic portfolio data.';

-- ---------------------------------------------------------------------------
-- Individual rule failures on current revisions, for exception analysis.
-- ---------------------------------------------------------------------------
CREATE VIEW v_rule_failures AS
SELECT
    v.validation_result_id,
    v.code,
    v.severity,
    v.disposition,
    v.field_name,
    v.expected_value,
    v.observed_value,
    v.evaluated_at,
    i.invoice_id,
    i.business_state,
    i.supplier_id,
    s.name AS supplier_name
FROM validation_results v
JOIN invoices i ON i.invoice_id = v.invoice_id
                AND v.revision_id = i.current_revision_id
LEFT JOIN suppliers s ON s.supplier_id = i.supplier_id
WHERE v.status = 'fail';

-- ---------------------------------------------------------------------------
-- Failure counts per rule code. Two different counts, never conflated:
-- invoices affected, and individual failed checks.
-- ---------------------------------------------------------------------------
CREATE VIEW v_exception_summary AS
SELECT
    code,
    count(DISTINCT invoice_id) AS invoices_affected,
    count(*)                   AS failed_checks
FROM v_rule_failures
GROUP BY code;

-- ---------------------------------------------------------------------------
-- Processing overview. Denominators:
--
--   review_rate            = invoices in needs_review / eligible invoices
--   straight_through_rate  = invoices validated with no reviewer edit
--                            / eligible invoices
--
-- "Eligible" excludes invoices recorded as duplicate or unsupported: this
-- version cannot process them, so counting them would flatter both rates.
-- Duplicate arrivals are reported separately and never counted as processed
-- invoices.
-- ---------------------------------------------------------------------------
CREATE VIEW v_processing_overview AS
WITH submission_counts AS (
    SELECT
        count(*)                                                   AS submissions_received,
        count(*) FILTER (WHERE outcome = 'duplicate')              AS duplicate_arrivals,
        count(*) FILTER (WHERE outcome = 'failed')                 AS failed_submissions,
        count(*) FILTER (WHERE outcome = 'pending')                AS pending_submissions
    FROM submissions
),
invoice_counts AS (
    SELECT
        count(*)                                                   AS unique_invoices,
        count(*) FILTER (WHERE business_state = 'validated')        AS validated_invoices,
        count(*) FILTER (WHERE business_state = 'needs_review')     AS needs_review_invoices,
        count(*) FILTER (WHERE business_state = 'unsupported')      AS unsupported_invoices,
        count(*) FILTER (WHERE business_state = 'rejected')         AS rejected_invoices,
        count(*) FILTER (WHERE business_state = 'duplicate')        AS duplicate_invoices,
        count(*) FILTER (
            WHERE business_state NOT IN ('duplicate', 'unsupported')
        )                                                          AS eligible_invoices,
        count(*) FILTER (
            WHERE business_state = 'validated' AND NOT has_reviewer_edit
        )                                                          AS validated_without_edit
    FROM v_invoice_register
)
SELECT
    sc.submissions_received,
    sc.duplicate_arrivals,
    sc.failed_submissions,
    sc.pending_submissions,
    ic.unique_invoices,
    ic.eligible_invoices,
    ic.validated_invoices,
    ic.needs_review_invoices,
    ic.unsupported_invoices,
    ic.rejected_invoices,
    ic.duplicate_invoices,
    ic.validated_without_edit,
    CASE WHEN ic.eligible_invoices = 0 THEN NULL
         ELSE round(ic.needs_review_invoices::numeric / ic.eligible_invoices, 4)
    END AS review_rate,
    CASE WHEN ic.eligible_invoices = 0 THEN NULL
         ELSE round(ic.validated_without_edit::numeric / ic.eligible_invoices, 4)
    END AS straight_through_rate
FROM submission_counts sc
CROSS JOIN invoice_counts ic;

COMMENT ON VIEW v_processing_overview IS
    'Counts and two rates with explicit denominators. Rates are NULL when there are no eligible invoices rather than reported as zero.';
