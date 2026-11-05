# Demonstration script

A run-through of what the project does, in the order that explains it. Written
for a three-to-five minute recording, with the commands to type.

Two rules for the recording. Say "synthetic" once at the start and let the
visible notices carry it afterwards. Never say a number that is not on the
screen.

**Steps 7 and 8 do not exist yet.** The dashboard is not built and the wider
benchmark is not run. They are listed so the script is complete when they are,
and so the recording does not accidentally imply they already exist.

## Before recording

```powershell
.\.venv\Scripts\python.exe tools\pg_dev_server.py start
```

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli reset --yes
```

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli serve
```

Have a second terminal ready, and `http://127.0.0.1:8000/docs` open.

## 1. The problem, in 20–30 seconds

An operations analyst receives supplier invoices as PDFs, keys them in, checks
the arithmetic, looks for documents already received, compares each with its
purchase order, and chases what disagrees. The mechanical parts repeat; the
judgement parts do not. Show one of the synthetic PDFs
(`data/fixtures/pdf/valid_001.pdf`) so the audience sees what arrives.

Say: everything in this demonstration is synthetic, and nothing here executes
a payment.

## 2. One clean invoice, end to end

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli ingest data\fixtures\pdf\valid_001.pdf
```

Point out, from the output: the file was stored under its own hash, the
arrival got a submission id, and the invoice validated with every applicable
check passing.

Then show where the values came from:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli show <invoice-id>
```

The checks table is the thing to linger on: every rule, its status, and for a
failure the expected and observed values. Mention that a skipped check is
recorded as skipped, not as a pass.

## 3. The same file again

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli ingest data\fixtures\pdf\exact_file_duplicate_016.pdf
```

Same bytes, different name. It is linked to the earlier submission, no second
invoice is created, and the arrival stays visible as an event. Say the word
"duplicate", not "fraud".

## 4. A mismatch, and a reviewer resolving it

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli ingest data\fixtures\pdf\total_mismatch_008.pdf
```

It fails `TOTAL_MISMATCH` and goes to review, with expected 1591.923 against
observed 1600.000 on screen.

Show that somebody needs telling, once:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli notifications
```

Then correct it over the API, from `/docs` or with `curl`:
`POST /invoices/{id}/revisions` with the corrected values, the revision number
being corrected, and a reason.

Two things to say while it runs:

- The corrected values go through the **same rules** as the extracted ones. A
  correction that introduced a different error would fail.
- The reason is required. An override with no reason is the one thing the
  audit trail cannot reconstruct afterwards.

## 5. The history

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli show <invoice-id>
```

Both revisions are present, the original failure is still recorded against
revision 1, and the checks table marks which revision is current. Nothing was
rewritten to make the invoice look clean.

Worth adding: had the reviewer *accepted* the exception instead of correcting
it, the invoice would be in the register but marked `accepted_with_exception`,
and excluded from the straight-through rate — because a person deciding is not
the rules passing.

## 6. A controlled failure, and recovery

Upload without processing, then make the stored file unreadable (rename it),
then process:

```powershell
curl -s -X POST -H "X-API-Key: $env:INVAUT_API_KEY" -F "file=@data/fixtures/pdf/valid_002.pdf" http://127.0.0.1:8000/submissions
```

Processing returns 500 with `retryable: true`. Show that nothing was lost:
`GET /submissions/{id}` still has the document and the job is
`retry_pending`, and the register has no half-written invoice.

Put the file back, then:

```powershell
curl -s -X POST -H "X-API-Key: $env:INVAUT_API_KEY" http://127.0.0.1:8000/jobs/recover
```

The sweep finishes it on attempt 2. The document was never uploaded again,
because the bytes were already stored.

## 7. The dashboard — NOT BUILT YET

When it exists: two metrics only, with their denominators read out. Until
then, the nearest honest substitute is:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli overview
```

and saying that the review rate and straight-through rate are shares of
*eligible* invoices, which excludes duplicates and unsupported documents, and
that duplicate arrivals are counted separately so they cannot inflate
throughput.

## 8. Results and limits — PARTLY AVAILABLE

What can be shown today is the extraction baseline
(`evaluation/results/extraction_baseline.md`): 16 of 16 on the documents the
extractor was built for, and **0 of 3** on a fourth layout it was never
taught, with **zero invented values**.

Say both numbers together, and say which one means more: the first is the
extractor marking its own homework, because the layouts and its label sets
were written side by side. The second is the informative one — faced with an
unfamiliar template it reads nothing and routes the document to a person,
rather than reading the wrong label into a total.

Then the one improvement to name next: more layouts, with some genuinely held
out, and a timed comparison against doing it by hand. Until that timing
exists, there is no claim about time saved.

## What not to say

- No accuracy figure for real invoices. None has been measured.
- No time saved, no cost saved, no adoption. Nothing has been timed.
- Not "fraud detection". The duplicate checks detect repeats, not intent.
- Not "compliant" with anything. The tax rate is a fictional test
  configuration.
- Not "production ready". It runs on one machine against synthetic data.
