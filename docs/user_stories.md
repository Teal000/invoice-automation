# User stories

What the service does, written from the side of the people who use it, with the
acceptance criteria each story has to meet and the tests that check them. The
requirements these trace to are in [business_requirements.md](business_requirements.md);
the process they sit in is drawn in [process_map.md](process_map.md).

Every test named here is checked by `tools/check_user_stories.py`, which fails
if a named test no longer exists, if a requirement has no story, or if the
traceability table at the end disagrees with the stories above it:

```powershell
.\.venv\Scripts\python.exe tools\check_user_stories.py
```

A story marked **Built** has every acceptance criterion covered by a passing
test. **Partly built** says which part is missing. Nothing here is marked built
on the strength of a plan.

## Who uses it

| Role | What they do with it |
|---|---|
| Operations analyst | Submits supplier invoices; today they re-key them |
| Reviewer | Decides what happens to an invoice that failed a check. In a small distributor this may be the same analyst, or a finance lead |
| Finance manager | Reads the reporting and acts on it |
| Operator | Keeps the service running and recovers it when something fails |

## Intake

### US-01 — Record an invoice the moment it arrives

As an **operations analyst**, I want an uploaded invoice recorded straight away,
before it is processed, so that nothing is lost if processing fails or happens later.

- Given a valid PDF, when I upload it, then a submission is recorded and not yet processed.
- Given a recorded submission, when processing runs, then it reaches an outcome.
- Given an empty file or an unaccepted file type, when I upload it, then it is refused.

**Requirement:** 1 · **Status:** Built

**Verified by:**
- `tests/integration/test_api.py::TestArrivalThenProcessing::test_an_upload_is_recorded_without_being_processed`
- `tests/integration/test_api.py::TestArrivalThenProcessing::test_processing_produces_the_outcome`
- `tests/integration/test_api.py::TestUploadValidation::test_an_empty_file_is_refused`
- `tests/integration/test_api.py::TestUploadValidation::test_an_unaccepted_file_type_is_refused`

### US-02 — See where every value came from

As an **operations analyst**, I want every extracted value to point back to where
it was read in the document, so that I can check a figure without searching the PDF.

- Given a processed PDF, then each value records where in the document it came from.
- Given any processed invoice, then the source document and the raw values are kept alongside the normalised ones.

**Requirement:** 2 · **Status:** Built

**Verified by:**
- `tests/integration/test_pdf_pipeline.py::TestOnePdf::test_field_evidence_is_stored_on_the_revision`
- `tests/integration/test_pipeline.py::TestValidInvoice::test_the_source_document_and_raw_values_are_retained`

### US-03 — Never guess at a document it can't read

As an **operations analyst**, I want a document in a layout the service can't
read sent to a person instead of guessed at, so that a wrong value never enters the register.

- Given a PDF in an unknown layout, when it is processed, then it is recorded for review.
- Given that case, then it is kept separate from a document excluded for being the wrong kind.

**Requirement:** 3 · **Status:** Built

**Verified by:**
- `tests/integration/test_pdf_pipeline.py::TestUnreadableLayout::test_a_document_in_an_unknown_layout_is_recorded_for_review`
- `tests/integration/test_pdf_pipeline.py::TestUnreadableLayout::test_it_is_kept_separate_from_an_excluded_document_kind`

## Validation

### US-04 — Apply the same checks to every invoice

As an **operations analyst**, I want every invoice checked against the same
arithmetic, supplier, date and purchase-order rules, so that the result doesn't
depend on who processed it.

- Given each labelled case in the fixture set, when it is processed, then it produces its expected outcome.
- Given the fixture set, then every rule code has at least one case that fails it.
- Given any invoice, then every check is recorded, not only the ones that failed.
- Given a line whose amount is wrong, then only that line is reported.
- Given an order that belongs to another supplier, an inactive supplier, or a due date before the invoice date, then the matching rule fails.

**Requirement:** 3 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestGroundTruth::test_every_labelled_case_produces_its_expected_outcome`
- `tests/integration/test_pipeline.py::TestGroundTruth::test_the_manifest_covers_every_rule_code`
- `tests/integration/test_pipeline.py::TestValidInvoice::test_every_check_is_recorded_not_only_the_failures`
- `tests/unit/test_rules_arithmetic.py::TestLineTotalMismatch::test_fails_only_the_offending_line`
- `tests/unit/test_rules_purchase_order.py::TestPoSupplierMismatch::test_fails_when_the_order_belongs_to_another_supplier`
- `tests/unit/test_rules_supplier_duplicates.py::TestUnknownSupplier::test_fails_when_the_supplier_is_inactive`
- `tests/unit/test_rules_identity.py::TestDateInconsistent::test_fails_when_due_date_precedes_invoice_date`

## Duplicates

### US-05 — Never enter the same file twice

As an **operations analyst**, I want an identical file sent twice linked to the
first instead of creating a second invoice, so that the same invoice is never counted twice.

- Given a file already received, when it arrives again, then no second invoice is created.
- Given that repeat, then the arrival stays visible as an event.

**Requirement:** 5 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestDuplicates::test_an_identical_file_does_not_create_a_second_invoice`
- `tests/integration/test_pipeline.py::TestDuplicates::test_the_duplicate_arrival_stays_visible_as_an_event`
- `tests/integration/test_pdf_pipeline.py::TestPdfDuplicates::test_an_identical_pdf_is_linked_not_reprocessed`

### US-06 — Send a second invoice with the same number to a person

As an **operations analyst**, I want a different document carrying the same
supplier and invoice number sent to review, so that a reissued or altered invoice gets looked at.

- Given an invoice number already used by that supplier, when a second document arrives with it, then it is linked for review, not accepted.

**Requirement:** 3, 5 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestDuplicates::test_a_second_document_for_the_same_invoice_number_is_linked_for_review`
- `tests/unit/test_rules_supplier_duplicates.py::TestBusinessDuplicate::test_fails_when_an_invoice_already_exists_for_supplier_and_number`

### US-07 — Stay correct when two uploads collide

As an **operations analyst**, I want two uploads of the same invoice at the same
moment to still end with one accepted invoice, so that timing can't create a duplicate.

- Given two concurrent submissions of the same invoice, then at most one is accepted.

**Requirement:** 5 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestDuplicates::test_concurrent_submissions_cannot_create_two_accepted_invoices`

### US-08 — Make a repeated request harmless

As an **operator**, I want a repeated request with the same key to change
nothing, so that a retried webhook or a double click can't create a second record.

- Given a request already handled, when it is replayed with the same key, then no new record is created.
- Given that replay, then it is recorded as an event.
- Given two requests with the same key, then the database itself refuses the second.

**Requirement:** 5 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestIdempotency::test_replaying_a_request_creates_no_second_record`
- `tests/integration/test_pipeline.py::TestIdempotency::test_the_replay_is_recorded_as_an_event`
- `tests/integration/test_pipeline.py::TestIdempotency::test_the_key_is_enforced_by_the_database`
- `tests/integration/test_api.py::TestIdempotencyAndDuplicates::test_the_same_key_records_nothing_new`

## Review

### US-09 — Start from the problem, not the document

As a **reviewer**, I want a queue of the invoices that need me, each opening
with its failed check visible, so that I start from what is wrong.

- Given invoices needing review, then the queue holds exactly those.
- Given the review screen, then it lists the queue.
- Given an invoice opened from the queue, then its failed check is shown.

**Requirement:** 4 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestTimeSeriesViews::test_the_review_queue_holds_exactly_what_needs_review`
- `tests/integration/test_api_client_and_ui.py::TestReviewScreen::test_it_renders_the_queue`
- `tests/integration/test_api_client_and_ui.py::TestReviewScreen::test_an_invoice_opens_with_its_failed_check_visible`

### US-10 — Have a correction checked, not trusted

As a **reviewer**, I want a corrected value to go through the same rules again,
so that a correction can't slip a bad figure past the checks.

- Given a correction that fixes the problem, then the invoice validates.
- Given a correction, then its values are checked, not assumed correct.
- Given a correction that doesn't fix it, then the case is raised again.

**Requirement:** 4 · **Status:** Built

**Verified by:**
- `tests/integration/test_review.py::TestCorrection::test_a_correction_that_fixes_the_problem_validates_the_invoice`
- `tests/integration/test_review.py::TestCorrection::test_the_corrected_values_are_checked_not_trusted`
- `tests/integration/test_review.py::TestCorrection::test_a_correction_that_does_not_resolve_it_announces_again`

### US-11 — Leave a record of what changed and why

As a **reviewer**, I want every decision to need a reason and to keep the
earlier version, so that anyone can see later what was changed and why.

- Given a correction, then the action is recorded with its reason, and both versions survive.
- Given an empty reason, then the database refuses it, and so does the API.
- Given any reviewer action, then it is recorded as an event.

**Requirement:** 4 · **Status:** Built

**Verified by:**
- `tests/integration/test_review.py::TestCorrection::test_the_action_is_recorded_with_its_reason`
- `tests/integration/test_review.py::TestCorrection::test_both_revisions_survive`
- `tests/integration/test_review.py::TestCorrection::test_an_empty_reason_is_refused_by_the_database`
- `tests/integration/test_review.py::TestReviewApi::test_a_reason_is_required`
- `tests/integration/test_review.py::TestEventTrail::test_every_reviewer_action_is_an_event`

### US-12 — Don't overwrite someone else's change

As a **reviewer**, I want my edit refused if someone changed the invoice after I
opened it, so that two reviewers can't silently overwrite each other.

- Given an edit made against an older version, then it is refused as a conflict.

**Requirement:** 4 · **Status:** Built

**Verified by:**
- `tests/integration/test_review.py::TestCorrection::test_an_edit_against_a_stale_revision_is_refused`
- `tests/integration/test_review.py::TestReviewApi::test_a_stale_revision_is_a_conflict`

### US-13 — Accept a justified exception without calling it a pass

As a **reviewer**, I want to accept a documented exception and have it marked as
one, so that it isn't counted as an invoice that passed on its own.

- Given an accepted exception, then it enters the register marked as an exception.
- Given that, then it is excluded from the straight-through rate.
- Given an invoice that already passed, then accepting it as an exception is refused.

**Requirement:** 4, 7 · **Status:** Built

**Verified by:**
- `tests/integration/test_review.py::TestAcceptedException::test_accepting_puts_it_in_the_register_but_marks_it`
- `tests/integration/test_review.py::TestAcceptedException::test_the_straight_through_rate_excludes_it`
- `tests/integration/test_review.py::TestAcceptedException::test_accepting_an_invoice_that_passed_is_refused`

### US-14 — Reject an invoice so a corrected one can follow

As a **reviewer**, I want to reject an invoice with a reason, so that the
supplier can send a corrected one under the same number.

- Given a rejection, then the reason and the new state are recorded.
- Given a rejection, then the invoice number is released for a new copy.
- Given a rejected invoice, then it can't be corrected or rejected again.

**Requirement:** 4 · **Status:** Built

**Verified by:**
- `tests/integration/test_review.py::TestRejection::test_rejecting_records_the_reason_and_the_state`
- `tests/integration/test_review.py::TestRejection::test_rejection_releases_the_invoice_number`
- `tests/integration/test_review.py::TestRejection::test_a_rejected_invoice_cannot_be_corrected`
- `tests/integration/test_review.py::TestRejection::test_rejecting_twice_is_refused`

### US-15 — Put a name on every decision

As a **reviewer**, I want to be asked who I am before I can decide anything, so
that every decision has a name attached.

- Given the review screen, then no decision is offered until a name is given.

**Requirement:** 4 · **Status:** Built

**Verified by:**
- `tests/integration/test_api_client_and_ui.py::TestReviewScreen::test_it_asks_for_a_name_before_offering_decisions`

### US-16 — Hear about a case once

As a **reviewer**, I want to be told once about a case that needs me, not every
time it is processed, so that notifications stay worth reading.

- Given a case needing review, then exactly one notification is written.
- Given that case processed again, then nothing new is written.
- Given a new version of it, then that is new information and is announced.
- Given the outbox, then the database enforces the de-duplication.

**Requirement:** 5 · **Status:** Partly built: notifications are written to an
outbox with these guarantees, but nothing delivers them yet.

**Verified by:**
- `tests/integration/test_recovery.py::TestReviewNotifications::test_a_review_case_is_announced_once`
- `tests/integration/test_recovery.py::TestReviewNotifications::test_processing_again_does_not_announce_again`
- `tests/integration/test_recovery.py::TestReviewNotifications::test_a_new_revision_is_new_information`
- `tests/integration/test_recovery.py::TestReviewNotifications::test_the_database_enforces_the_deduplication`

## Recovery

### US-17 — Lose nothing when processing fails

As an **operator**, I want a failure part way through to leave the document,
its history and a job I can retry, so that a technical fault never loses an invoice.

- Given a stored file that goes missing mid-process, then the job fails but the document and history are kept.
- Given that failed job, when the file is restored and the job retried, then it succeeds.

**Requirement:** 6 · **Status:** Built

**Verified by:**
- `tests/integration/test_api.py::TestRecoverableFailure::test_a_missing_stored_file_fails_the_job_but_keeps_everything`
- `tests/integration/test_api.py::TestRecoverableFailure::test_the_job_can_then_be_retried_to_success`

### US-18 — Pick up stalled work, within limits

As an **operator**, I want stalled or failed work picked up automatically, with
a limit, so that one bad document can't block the queue or retry forever.

- Given a failed attempt whose cause has gone, then the sweep recovers it.
- Given a job left running past its lease, then the sweep picks it up.
- Given one job that keeps failing, then the others still go through.
- Given a job with no attempts left, then it is left for a person.
- Given a recovered job, then no second invoice is created.

**Requirement:** 6 · **Status:** Built

**Verified by:**
- `tests/integration/test_recovery.py::TestRecoverySweep::test_a_failed_attempt_is_recovered_once_the_cause_is_gone`
- `tests/integration/test_recovery.py::TestRecoverySweep::test_a_stalled_running_job_is_picked_up`
- `tests/integration/test_recovery.py::TestRecoverySweep::test_one_failing_job_does_not_block_the_others`
- `tests/integration/test_recovery.py::TestRecoverySweep::test_a_job_with_no_attempts_left_is_left_for_a_person`
- `tests/integration/test_recovery.py::TestRecoverySweep::test_the_sweep_does_not_create_a_second_invoice`

## Reporting

### US-19 — Trust a figure without rebuilding it

As a **finance manager**, I want reports that agree with the underlying records,
so that I can act on a figure without rebuilding it in a spreadsheet.

- Given the overview, then it matches the same figures computed directly in SQL.
- Given the API summary, then it reconciles with the register.
- Given the Power BI report, then every published figure is recomputed from the base tables and checked.

**Requirement:** 7 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestReporting::test_the_overview_reconciles_with_direct_sql`
- `tests/integration/test_api.py::TestReads::test_the_summary_reconciles_with_the_register`
- `tools/reconcile_dashboard.py`

### US-20 — Count invoices, not arrivals

As a **finance manager**, I want repeat arrivals kept out of the processed count,
and invoice counts kept apart from counts of failed checks, so that neither inflates the other.

- Given a duplicate arrival, then it isn't counted as a processed invoice.
- Given exception reporting, then invoices with an exception and individual failed checks are counted separately.

**Requirement:** 7 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestReporting::test_duplicate_arrivals_are_not_counted_as_processed_invoices`
- `tests/integration/test_pipeline.py::TestReporting::test_exception_counts_separate_invoices_from_individual_checks`

### US-21 — Age the backlog from arrival

As a **finance manager**, I want the review backlog aged from when an invoice
arrived, so that old invoice dates can't hide a slow queue.

- Given the review queue, then age runs from arrival, not from the invoice.
- Given the daily view, then it dates by arrival, not by invoice date.

**Requirement:** 7 · **Status:** Built

**Verified by:**
- `tests/integration/test_pipeline.py::TestTimeSeriesViews::test_queue_age_runs_from_arrival_not_from_the_invoice_row`
- `tests/integration/test_pipeline.py::TestTimeSeriesViews::test_the_daily_view_dates_by_arrival_not_by_invoice_date`

## Evaluation

### US-22 — Know what an accuracy figure is worth

As **whoever decides whether to trust the extraction**, I want accuracy measured
on a frozen test set that includes a layout it was never built for, so that the
figure says something about documents it hasn't seen.

- Given the corpus, then it is regenerated byte-for-byte from one seed.
- Given the frozen test split, when the benchmark runs, then it reports familiar and unseen layouts separately.

**Requirement:** 8 · **Status:** Built

**Verified by:**
- `evaluation/methodology.md`
- `evaluation/run_benchmark.py`
- `evaluation/results/benchmark_test.json`

## Not built

Kept here so the list of what exists can't quietly grow to include them.

| What | Why not |
|---|---|
| Reading scanned invoices | No OCR |
| Delivering notifications | Written to an outbox; nothing sends them |
| Measured reviewer time saved | Nothing has been timed |
| Real supplier invoices | All data is synthetic |
| Credit notes, partial deliveries, split orders | Out of scope; marked unsupported rather than approximated |

## Traceability

| Requirement | Stories |
|---|---|
| 1 | US-01 |
| 2 | US-02 |
| 3 | US-03, US-04, US-06 |
| 4 | US-09, US-10, US-11, US-12, US-13, US-14, US-15 |
| 5 | US-05, US-06, US-07, US-08, US-16 |
| 6 | US-17, US-18 |
| 7 | US-13, US-19, US-20, US-21 |
| 8 | US-22 |
