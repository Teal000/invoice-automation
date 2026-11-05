-- 0005_review_actions.sql
-- What a reviewer did, and why.
--
-- The plan's requirement: a reviewer can correct data, explain a decision and
-- trigger revalidation. Two things follow that the schema has to carry.
--
-- First, a reason is mandatory. An override with no stated reason is the one
-- thing an audit trail cannot reconstruct afterwards, so the column is NOT
-- NULL and a check forbids an empty string. "Someone approved it" is not an
-- answer to "why was this approved".
--
-- Second, a reviewer accepting a documented exception is **not** the same as
-- every automated check passing. Both end with the invoice in the register,
-- and a report that treats them alike would overstate how much the rules
-- actually handled. The register view gains `accepted_with_exception`,
-- derived from this table so there is no second copy of the fact to drift,
-- and the straight-through rate stops counting those invoices.

CREATE TABLE review_actions (
    review_action_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id       uuid NOT NULL REFERENCES invoices (invoice_id),
    actor            text NOT NULL,
    action           text NOT NULL
                     CHECK (action IN ('correct', 'reject', 'accept_exception')),
    -- Why. Required, and not satisfied by whitespace.
    reason           text NOT NULL CHECK (btrim(reason) <> ''),
    -- The revision the reviewer was looking at, and the one they produced.
    -- A correction has both; a rejection or an acceptance only the former.
    from_revision_id uuid REFERENCES invoice_revisions (revision_id),
    to_revision_id   uuid REFERENCES invoice_revisions (revision_id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- A correction must produce a revision; the other actions must not.
    CONSTRAINT review_actions_revision_chk CHECK (
        (action = 'correct' AND to_revision_id IS NOT NULL)
        OR (action <> 'correct' AND to_revision_id IS NULL)
    )
);

CREATE INDEX review_actions_invoice_idx ON review_actions (invoice_id, created_at);

COMMENT ON TABLE review_actions IS
    'Reviewer decisions with a mandatory stated reason. A correction records the revision it came from and the one it produced.';

-- ---------------------------------------------------------------------------
-- The register gains the distinction between "the rules passed" and "a person
-- accepted an exception". Columns are appended, never reordered, so the view
-- can be replaced while v_processing_overview depends on it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_invoice_register AS
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
    EXISTS (
        SELECT 1 FROM invoice_revisions ir
        WHERE ir.invoice_id = i.invoice_id AND ir.source = 'reviewer_correction'
    )                            AS has_reviewer_edit,
    -- Appended below this line.
    EXISTS (
        SELECT 1 FROM review_actions ra
        WHERE ra.invoice_id = i.invoice_id AND ra.action = 'accept_exception'
    )                            AS accepted_with_exception,
    (
        SELECT count(*) FROM review_actions ra WHERE ra.invoice_id = i.invoice_id
    )                            AS review_action_count
FROM invoices i
LEFT JOIN invoice_revisions r ON r.revision_id = i.current_revision_id
LEFT JOIN suppliers s ON s.supplier_id = i.supplier_id;

COMMENT ON VIEW v_invoice_register IS
    'One row per canonical invoice, with its current revision. accepted_with_exception marks an invoice a person accepted despite a failed check, which is not the same as the checks passing. Synthetic portfolio data.';

-- ---------------------------------------------------------------------------
-- The straight-through rate must not count an invoice a person had to decide
-- about. "Straight through" means the rules handled it unaided.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_processing_overview AS
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
            WHERE business_state = 'validated'
              AND NOT has_reviewer_edit
              AND NOT accepted_with_exception
        )                                                          AS validated_without_edit,
        count(*) FILTER (WHERE accepted_with_exception)             AS accepted_with_exception_invoices,
        count(*) FILTER (WHERE has_reviewer_edit)                   AS corrected_invoices
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
    END AS straight_through_rate,
    ic.accepted_with_exception_invoices,
    ic.corrected_invoices
FROM submission_counts sc
CROSS JOIN invoice_counts ic;

COMMENT ON VIEW v_processing_overview IS
    'Counts and two rates with explicit denominators. straight_through_rate counts only invoices the rules validated with no reviewer correction and no accepted exception.';
