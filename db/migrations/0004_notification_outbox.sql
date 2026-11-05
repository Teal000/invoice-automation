-- 0004_notification_outbox.sql
-- Notifications waiting to be sent, and the guard against sending twice.
--
-- An outbox rather than a direct send. Processing happens inside a
-- transaction; sending a message does not roll back. Writing the intent to
-- send into the same transaction as the work, and delivering it afterwards,
-- is what stops a rolled-back invoice from having already told somebody
-- about itself.
--
-- `dedupe_key` is the whole point. Processing can run more than once for the
-- same submission — a repeated trigger, a retry after a timeout, a recovery
-- sweep — and a reviewer must not be told three times. The key is derived
-- from what the notification is *about*, not from when it was produced, and
-- the uniqueness is enforced here rather than by application code.
--
-- Nothing in this version actually delivers anything. There is no mail
-- transport, no webhook and no chat integration: `mark_sent` records that a
-- caller claims to have delivered it. The table is honest about that by
-- keeping `channel` free text and `sent_at` nullable.

CREATE TABLE notification_outbox (
    notification_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- What it is about. Both nullable: a notification may concern an arrival
    -- that never became an invoice.
    invoice_id      uuid REFERENCES invoices (invoice_id),
    submission_id   uuid REFERENCES submissions (submission_id),
    notification_type text NOT NULL
                      CHECK (notification_type IN ('review_required',
                                                   'processing_failed',
                                                   'duplicate_received')),
    -- Derived from the subject, never from the clock, so a repeat is a
    -- collision rather than a new row.
    dedupe_key      text NOT NULL UNIQUE,
    channel         text NOT NULL DEFAULT 'none',
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sent', 'suppressed', 'failed')),
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempt_count   integer NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    sent_at         timestamptz,
    CONSTRAINT notification_outbox_anchor_chk
        CHECK (invoice_id IS NOT NULL OR submission_id IS NOT NULL),
    -- A sent notification must say when, and an unsent one must not.
    CONSTRAINT notification_outbox_sent_at_chk
        CHECK ((status = 'sent') = (sent_at IS NOT NULL))
);

CREATE INDEX notification_outbox_pending_idx
    ON notification_outbox (created_at)
    WHERE status = 'pending';

CREATE INDEX notification_outbox_invoice_idx ON notification_outbox (invoice_id);

COMMENT ON TABLE notification_outbox IS
    'Notifications written in the same transaction as the work that caused them, delivered afterwards. Nothing in version 0.1 delivers them; status sent means a caller reported delivery.';

COMMENT ON COLUMN notification_outbox.dedupe_key IS
    'Derived from the subject of the notification, never from the time. Unique, so a reprocessed submission cannot notify twice.';
