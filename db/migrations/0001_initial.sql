-- 0001_initial.sql
-- Invoice processing and exception reporting: initial schema.
--
-- Design notes that the rest of the project depends on:
--
--   * A submission is not an invoice. Every arrival is recorded in
--     `submissions`; a canonical business record lives in `invoices`. A
--     duplicate arrival therefore stays visible as an event without creating
--     a second accepted invoice.
--   * Technical job state (`processing_jobs`) is kept apart from business
--     state (`invoices.business_state`).
--   * Uniqueness is enforced by database constraints, not only by application
--     checks, so two simultaneous submissions cannot both create an invoice.
--   * Money is NUMERIC, never floating point. Three decimal places, because
--     the configured currency TND is subdivided into 1000 millimes.
--   * All invoice data in this project is synthetic. Supplier rows carry an
--     explicit `is_synthetic` flag so that a database dump cannot be mistaken
--     for real supplier data.
--
-- Written for PostgreSQL 18. `gen_random_uuid()` is built in since
-- PostgreSQL 13, so no extension is required.

-- ---------------------------------------------------------------------------
-- Migration bookkeeping
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------
CREATE TABLE suppliers (
    supplier_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL,
    reference            text NOT NULL,
    reference_normalized text NOT NULL UNIQUE,
    permitted_currency   char(3) NOT NULL,
    is_active            boolean NOT NULL DEFAULT true,
    -- Every supplier in this project is invented. Kept in the data, not only
    -- in the documentation.
    is_synthetic         boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE suppliers IS
    'Synthetic supplier registry. permitted_currency is recorded for later use; no rule in version 0.1 compares it against the document currency.';

CREATE TABLE purchase_orders (
    purchase_order_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    po_number            text NOT NULL,
    po_number_normalized text NOT NULL UNIQUE,
    supplier_id          uuid NOT NULL REFERENCES suppliers (supplier_id),
    currency             char(3) NOT NULL,
    expected_amount      numeric(18, 3) NOT NULL,
    state                text NOT NULL DEFAULT 'open'
                         CHECK (state IN ('open', 'closed', 'cancelled')),
    is_synthetic         boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX purchase_orders_supplier_idx ON purchase_orders (supplier_id);

CREATE TABLE purchase_order_lines (
    purchase_order_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    purchase_order_id      uuid NOT NULL
                           REFERENCES purchase_orders (purchase_order_id)
                           ON DELETE CASCADE,
    line_number            integer NOT NULL,
    description            text,
    quantity               numeric(18, 4),
    unit_price             numeric(18, 3),
    line_amount            numeric(18, 3),
    UNIQUE (purchase_order_id, line_number)
);

-- ---------------------------------------------------------------------------
-- Arrivals: documents and submissions
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
    document_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    original_file_name text NOT NULL,
    -- Path relative to the configured storage root. The original file is kept
    -- so that an extracted value can always be traced back to its source.
    storage_path      text,
    content_sha256    char(64) NOT NULL,
    byte_size         bigint,
    media_type        text,
    extraction_method text,
    -- The values exactly as extracted, before normalisation.
    raw_payload       jsonb,
    raw_text          text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX documents_sha256_idx ON documents (content_sha256);

CREATE TABLE submissions (
    submission_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id                uuid REFERENCES documents (document_id),
    source                     text NOT NULL
                               CHECK (source IN ('cli', 'api_upload', 'mailbox', 'n8n')),
    -- A caller-supplied key. Replaying the same request with the same key
    -- must not create a second submission, so the uniqueness is enforced here
    -- rather than in application code. NULLs are permitted and do not collide.
    idempotency_key            text UNIQUE,
    received_at                timestamptz NOT NULL DEFAULT now(),
    outcome                    text NOT NULL DEFAULT 'pending'
                               CHECK (outcome IN ('pending', 'validated', 'needs_review',
                                                  'duplicate', 'unsupported', 'failed')),
    invoice_id                 uuid,
    duplicate_of_submission_id uuid REFERENCES submissions (submission_id),
    notes                      text
);

CREATE INDEX submissions_received_at_idx ON submissions (received_at);
CREATE INDEX submissions_outcome_idx ON submissions (outcome);

-- ---------------------------------------------------------------------------
-- Canonical invoices and their revisions
-- ---------------------------------------------------------------------------
CREATE TABLE invoices (
    invoice_id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id               uuid REFERENCES suppliers (supplier_id),
    -- Identity for duplicate detection. Either the resolved supplier id or,
    -- when the supplier did not resolve, 'unresolved:<normalised reference>'.
    -- Two documents from an unknown supplier with the same invoice number are
    -- still treated as the same business invoice.
    supplier_key              text NOT NULL,
    invoice_number            text NOT NULL,
    invoice_number_normalized text NOT NULL,
    current_revision_id       uuid,
    business_state            text NOT NULL DEFAULT 'received'
                              CHECK (business_state IN ('received', 'processing',
                                                        'validated', 'needs_review',
                                                        'duplicate', 'unsupported',
                                                        'rejected')),
    first_submission_id       uuid REFERENCES submissions (submission_id),
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);

-- The duplicate guard. A rejected invoice releases its number, so a corrected
-- document can be accepted later.
CREATE UNIQUE INDEX invoices_supplier_number_uniq
    ON invoices (supplier_key, invoice_number_normalized)
    WHERE business_state <> 'rejected';

CREATE INDEX invoices_state_idx ON invoices (business_state);
CREATE INDEX invoices_supplier_idx ON invoices (supplier_id);

ALTER TABLE submissions
    ADD CONSTRAINT submissions_invoice_fk
    FOREIGN KEY (invoice_id) REFERENCES invoices (invoice_id);

CREATE TABLE invoice_revisions (
    revision_id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id                       uuid NOT NULL
                                     REFERENCES invoices (invoice_id)
                                     ON DELETE CASCADE,
    revision_number                  integer NOT NULL,
    source                           text NOT NULL
                                     CHECK (source IN ('extraction', 'reviewer_correction')),
    submission_id                    uuid REFERENCES submissions (submission_id),
    document_id                      uuid REFERENCES documents (document_id),
    document_type                    text NOT NULL,
    invoice_number                   text,
    invoice_number_normalized        text,
    supplier_reference               text,
    supplier_reference_normalized    text,
    supplier_name                    text,
    invoice_date                     date,
    due_date                         date,
    currency                         char(3),
    purchase_order_number            text,
    purchase_order_number_normalized text,
    subtotal                         numeric(18, 3),
    tax_total                        numeric(18, 3),
    total                            numeric(18, 3),
    -- Raw strings as extracted, kept next to the normalised columns.
    raw_values                       jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Fields that were absent or unparseable, with the reason.
    field_problems                   jsonb NOT NULL DEFAULT '[]'::jsonb,
    extraction_method                text,
    created_by                       text NOT NULL,
    created_at                       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (invoice_id, revision_number)
);

ALTER TABLE invoices
    ADD CONSTRAINT invoices_current_revision_fk
    FOREIGN KEY (current_revision_id)
    REFERENCES invoice_revisions (revision_id);

CREATE TABLE invoice_lines (
    invoice_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision_id     uuid NOT NULL
                    REFERENCES invoice_revisions (revision_id)
                    ON DELETE CASCADE,
    line_number     integer NOT NULL,
    description     text,
    quantity        numeric(18, 4),
    unit_price      numeric(18, 3),
    line_amount     numeric(18, 3),
    UNIQUE (revision_id, line_number)
);

-- ---------------------------------------------------------------------------
-- Rule outcomes
-- ---------------------------------------------------------------------------
CREATE TABLE validation_results (
    validation_result_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id        uuid REFERENCES submissions (submission_id),
    invoice_id           uuid REFERENCES invoices (invoice_id),
    revision_id          uuid REFERENCES invoice_revisions (revision_id),
    code                 text NOT NULL,
    status               text NOT NULL CHECK (status IN ('pass', 'fail', 'skipped')),
    severity             text NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    disposition          text NOT NULL
                         CHECK (disposition IN ('review', 'unsupported', 'duplicate_link')),
    message              text,
    field_name           text,
    -- Expected and observed are stored for every result, which is what makes
    -- a failure reviewable rather than just a red flag.
    expected_value       text,
    observed_value       text,
    details              jsonb NOT NULL DEFAULT '{}'::jsonb,
    evaluated_at         timestamptz NOT NULL DEFAULT now(),
    -- A result must attach to something: an arrival, an invoice, or both.
    CONSTRAINT validation_results_anchor_chk
        CHECK (submission_id IS NOT NULL OR invoice_id IS NOT NULL)
);

CREATE INDEX validation_results_revision_idx ON validation_results (revision_id);
CREATE INDEX validation_results_code_status_idx ON validation_results (code, status);

-- ---------------------------------------------------------------------------
-- Technical processing state
-- ---------------------------------------------------------------------------
CREATE TABLE processing_jobs (
    job_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id    uuid NOT NULL REFERENCES submissions (submission_id),
    job_type         text NOT NULL DEFAULT 'process_submission',
    status           text NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'succeeded',
                                       'retry_pending', 'failed')),
    attempt_count    integer NOT NULL DEFAULT 0,
    max_attempts     integer NOT NULL DEFAULT 3,
    lease_expires_at timestamptz,
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    -- One processing job per submission and type. A retry increments
    -- attempt_count instead of creating a second job, so a retried submission
    -- cannot produce a second invoice.
    UNIQUE (submission_id, job_type)
);

CREATE INDEX processing_jobs_status_idx ON processing_jobs (status);

-- ---------------------------------------------------------------------------
-- Append-only event history
-- ---------------------------------------------------------------------------
CREATE TABLE audit_events (
    event_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    event_type  text NOT NULL,
    entity_type text NOT NULL,
    entity_id   uuid,
    actor       text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_events_entity_idx ON audit_events (entity_type, entity_id);
CREATE INDEX audit_events_occurred_at_idx ON audit_events (occurred_at);

-- Append-only is enforced, not merely intended: an interrupted or replayed
-- run must not be able to rewrite history.
CREATE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (attempted %)', TG_OP;
END;
$$;

CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
