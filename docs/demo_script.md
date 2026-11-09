# Demonstration script

A run-through of what the project does, in the order that explains it. Written
for a five-to-seven minute recording, with the commands to type.

Two rules for the recording. Say "synthetic" once at the start and let the
visible notices carry it afterwards. **Never say a number that is not on the
screen.** The figures below are there so you know what to expect, not so they
can be read out from memory — the review-queue bands in particular change with
the clock.

Every path, CLI command and API route named here exists
(`tools/check_demo_script.py` checks them), but the script has not been
rehearsed end to end since this rewrite. An earlier version carried
two steps marked NOT BUILT YET; both are now built, and the file it cited for
the extraction baseline no longer exists. If a step here ever stops being
true, mark it, do not quietly leave it.

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

For step 8 you need the dashboard populated and Power BI open. The load below
resets the data, so pause the recording after step 7 and run it then, not
before step 2:

```powershell
.\.venv\Scripts\python.exe tools\load_corpus.py --reset --spread-days 30
```

Then refresh the report in Power BI Desktop. The report imports its data, so
an unrefreshed report shows the previous load and nothing on screen says so.

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

## 7. Nobody is typing these commands

Everything so far was driven by hand. Show that it does not have to be:
`workflows/n8n/` holds three workflows against the same HTTP API — a webhook
that processes an arriving submission, a recovery sweep every fifteen minutes,
and a notification read every five.

Show the n8n executions list. All three have been observed running against the
live service, the two scheduled ones on 9 November; the evidence, including
how it was confirmed from the API's own request log rather than from n8n's
word for it, is in `workflows/n8n/README.md`.

The one sentence worth saying here: publishing a schedule into a running n8n
**does not schedule it** — the timers are registered when the process starts.
Two workflows sat silent for three days looking active by every check that was
being made. It is a good example of what "verified" has to mean.

## 8. The dashboard

Three pages in Power BI, over a role that has `SELECT` on the reporting views
and nothing else — not on the base tables. Say that: the report cannot read a
raw invoice even if someone pointed it at one.

- **Processing overview** — submissions received against unique invoices, and
  the review and straight-through rates. Read the denominators off the screen.
  Both rates are shares of *eligible* invoices, which excludes duplicates and
  unsupported documents, and duplicate arrivals are counted separately so they
  cannot inflate throughput.
- **Exception analysis** — failures per rule code. Two bars per code,
  invoices affected and failed checks; they are different counts and are never
  summed. The engine has thirteen codes and twelve appear: `EXACT_FILE_DUPLICATE`
  is absent by design, because an identical-file arrival creates no revision
  and is counted as a duplicate arrival on page 1 instead.
- **Invoice register** — the queue by age band. The bands are computed from
  `now()`, so they age between any two runs; only the total is stable, and it
  matches the "Needs review" card on page 1.

Then show that the numbers are not taken on trust:

```powershell
.\.venv\Scripts\python.exe tools\reconcile_dashboard.py
```

It recomputes all 13 published figures from the base tables and exits non-zero
on any disagreement.

If Power BI is not available when recording, the honest substitute is:

```powershell
.\.venv\Scripts\python.exe -m invoice_automation.cli overview
.\.venv\Scripts\python.exe -m invoice_automation.cli exceptions
```

and the rendered pages in `dashboard/screenshots/`, whose README records the
figures each image was taken with.

## 9. Results and limits

The frozen 40-document test split, run once
(`evaluation/results/benchmark_test.md`):

| | Familiar layouts (32) | Unseen layout (8) |
|---|---|---|
| Layout identified | 32/32 | **0/8** |
| Critical-field accuracy | 32/32 | **0/8** |
| **Values invented** | — | **0** |

Say both columns together, and say which one means more. The first is the
extractor marking its own homework: the layouts and its label sets were
written side by side. The second is the informative one — faced with an
unfamiliar template it reads nothing and routes the document to a person,
rather than reading the wrong label into a total.

Then the number that is least flattering and most worth saying:

| Fault recall | |
|---|---|
| All seeded faults | 20/24 |
| Faults a rule targets | 19/19 |
| **Faults no rule targets** | **1/5** |

The corpus seeds faults no rule was written to catch, and the system misses
four of the five. The one that was flagged was flagged only because its layout
could not be read, not because the fault was noticed. That is a measured blind
spot rather than an assumed one: this enforces the controls that were written
down, and nothing else.

Close on the improvement to name next: more layouts, some genuinely held out,
and a timed comparison against doing the work by hand. Until that timing
exists there is no claim about time saved.

## What not to say

- No accuracy figure for real invoices. None has been measured.
- No time saved, no cost saved, no adoption. Nothing has been timed.
- Not "fraud detection". The duplicate checks detect repeats, not intent.
- Not "compliant" with anything. The tax rate is a fictional test
  configuration.
- Not "production ready". It runs on one machine against synthetic data.
- Not "AI" or "machine learning". The extractor is text parsing and the rules
  are deterministic. Nothing here learns.
