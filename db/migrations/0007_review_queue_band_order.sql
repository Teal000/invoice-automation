-- 0007_review_queue_band_order.sql
-- Give the review-queue age bands an explicit order.
--
-- `age_band` is text, so every reporting tool sorts it alphabetically:
--
--     1 to 3 days, 3 to 7 days, over a week, under a day
--
-- which puts the newest arrivals at the far end of a time axis. The Power BI
-- report showed exactly that, and the mistake is invisible whenever the
-- "under a day" band happens to be empty: the remaining three sort the same
-- way under either rule, so the chart looks correct until the day it isn't.
--
-- Sorting the chart by its own measure instead would order the bands by how
-- many invoices are in them, which coincides with time order today and would
-- stop doing so the moment the backlog changed shape. That is the kind of
-- figure that stays right by luck.
--
-- So the order is written down here, next to the bands it orders, rather
-- than configured in a report file. The same argument as every other figure
-- on the dashboard: it should be traceable to a view.
--
-- CREATE OR REPLACE VIEW can only append columns, so `age_band_order` goes
-- last rather than beside `age_band`. Grants on the view survive a replace,
-- so the read-only reporting role keeps its SELECT without being re-granted.

CREATE OR REPLACE VIEW v_review_queue AS
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
    )                                                           AS failed_codes,
    -- Deliberately the same boundaries, in the same order, as the CASE above.
    -- They are duplicated rather than derived because a view cannot reference
    -- its own output column, and a mismatch between the two would be a bug:
    -- tests/integration asserts every band maps to exactly one order value.
    CASE
        WHEN now() - arrived.first_arrival < interval '1 day'  THEN 1
        WHEN now() - arrived.first_arrival < interval '3 days' THEN 2
        WHEN now() - arrived.first_arrival < interval '7 days' THEN 3
        ELSE 4
    END                                                         AS age_band_order
FROM v_invoice_register r
CROSS JOIN LATERAL (
    SELECT min(s.received_at) AS first_arrival
    FROM submissions s
    WHERE s.invoice_id = r.invoice_id
) arrived
WHERE r.business_state = 'needs_review';

COMMENT ON VIEW v_review_queue IS
    'Invoices waiting for a person, with how long they have waited and which checks failed on the current revision. Age runs from first arrival. age_band_order sorts the bands chronologically; sorting age_band as text puts "under a day" last.';
