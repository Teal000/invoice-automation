-- 0006_reporting_timeseries.sql
-- Views the dashboard needs that the single-row summary cannot provide.
--
-- `v_processing_overview` answers "where do things stand now". A dashboard
-- also has to answer "what has been happening" and "what is waiting", and
-- neither can be drawn from one row of totals. Two views, both defined here
-- so that the dashboard contains no SQL of its own: a figure on a chart
-- should be traceable to a view, not to a formula buried in a report file
-- that nobody can diff.

-- ---------------------------------------------------------------------------
-- One row per day: arrivals and what became of them.
--
-- Dated by arrival (`submissions.received_at`), not by invoice date. The two
-- differ and conflating them is a classic way to produce a chart nobody can
-- reconcile: an invoice dated in March can arrive in June.
-- ---------------------------------------------------------------------------
CREATE VIEW v_daily_processing AS
SELECT
    date_trunc('day', s.received_at)::date                      AS arrival_date,
    count(*)                                                    AS submissions,
    count(*) FILTER (WHERE s.outcome = 'validated')             AS validated,
    count(*) FILTER (WHERE s.outcome = 'needs_review')          AS needs_review,
    count(*) FILTER (WHERE s.outcome = 'duplicate')             AS duplicate_arrivals,
    count(*) FILTER (WHERE s.outcome = 'unsupported')           AS unsupported,
    count(*) FILTER (WHERE s.outcome = 'failed')                AS failed,
    count(*) FILTER (WHERE s.outcome = 'pending')               AS still_pending,
    count(DISTINCT s.invoice_id) FILTER (WHERE s.invoice_id IS NOT NULL)
                                                                AS invoices_touched
FROM submissions s
GROUP BY 1;

COMMENT ON VIEW v_daily_processing IS
    'Arrivals per day and their outcomes, dated by when the document arrived rather than by the invoice date. Synthetic portfolio data.';

-- ---------------------------------------------------------------------------
-- What is waiting for a person, and how long it has waited.
--
-- Age runs from when the document **arrived**, not from when the invoice row
-- was created. The two differ whenever documents are loaded in bulk, and the
-- arrival is what the business waited from. An invoice a reviewer corrected
-- and which still fails restarts nothing: it has been waiting since it
-- arrived.
-- ---------------------------------------------------------------------------
CREATE VIEW v_review_queue AS
SELECT
    r.invoice_id,
    r.invoice_number,
    r.supplier_name,
    r.supplier_reference,
    r.invoice_date,
    r.total,
    r.currency,
    r.failed_rule_codes,
    r.failed_checks,
    r.has_reviewer_edit,
    r.review_action_count,
    arrived.first_arrival                                       AS first_recorded_at,
    (now() - arrived.first_arrival)                             AS waiting_for,
    floor(extract(epoch FROM now() - arrived.first_arrival) / 86400)::int
                                                                AS days_waiting,
    CASE
        WHEN now() - arrived.first_arrival < interval '1 day'  THEN 'under a day'
        WHEN now() - arrived.first_arrival < interval '3 days' THEN '1 to 3 days'
        WHEN now() - arrived.first_arrival < interval '7 days' THEN '3 to 7 days'
        ELSE 'over a week'
    END                                                         AS age_band,
    (
        SELECT string_agg(DISTINCT v.code, ', ' ORDER BY v.code)
        FROM validation_results v
        JOIN invoices i ON i.invoice_id = v.invoice_id
        WHERE v.invoice_id = r.invoice_id
          AND v.revision_id = i.current_revision_id
          AND v.status = 'fail'
    )                                                           AS failed_codes
FROM v_invoice_register r
CROSS JOIN LATERAL (
    SELECT min(s.received_at) AS first_arrival
    FROM submissions s
    WHERE s.invoice_id = r.invoice_id
) arrived
WHERE r.business_state = 'needs_review';

COMMENT ON VIEW v_review_queue IS
    'Invoices waiting for a person, with how long they have waited and which checks failed on the current revision. Age runs from first arrival.';
