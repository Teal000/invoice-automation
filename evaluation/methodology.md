# Evaluation methodology

How the numbers in `evaluation/results/` were produced, what they mean, and
what they do not. One rule governs this file: a figure here is either
reproducible from a documented command or it does not belong here.

## What has been measured

**Extraction from synthetic PDFs.** One run, reproducible:

```powershell
.\.venv\Scripts\python.exe data\generate_pdf_fixtures.py
.\.venv\Scripts\python.exe evaluation\run_extraction_baseline.py
```

Outputs `results/extraction_baseline.json` (every case, field by field) and
`results/extraction_baseline.md` (the summary).

## What has not been measured

- Human handling time, with or without the workflow. No timing has been done,
  so no time saving is claimed anywhere in this repository.
- Processing latency and cost per document.
- Exception precision and recall as *detection* rates. The rules are
  deterministic, and the current dataset labels exactly the failure each case
  was built to trigger; measuring precision and recall against a dataset whose
  labels were derived from the same rules would measure nothing. These become
  meaningful when the dataset contains faults the rules were not designed
  against.
- Recovery success. No retry or recovery path exists yet.
- Anything about scanned documents. There is no OCR.

## The dataset

16 documents. Each is rendered by `data/generate_pdf_fixtures.py` from a
structured payload in `data/fixtures/structured`, so that payload **is** the
ground truth: no hand labelling, and no opportunity for the labels to drift
from the documents.

Three layout families — `atlas`, `medina`, `carthage` — differing in label
wording, field order and geometry. Fourteen of the sixteen documents carry a
deliberately seeded fault (a wrong total, a missing field, an unknown
supplier, a duplicate); two are clean.

A fourth layout, `sousse`, is drawn by the same generator and is **absent from
the extractor's label sets**. Three documents are rendered in it as a held-out
probe. Adding `sousse` labels to `src/invoice_automation/extraction/layouts.py`
would destroy the only measurement here that is not self-referential, so the
manifest says so in writing.

Generation is deterministic (reportlab `invariant=1`): regenerating produces
byte-identical files, which is also what lets one case be a genuine
byte-identical resend for the duplicate check.

## How a value is counted correct

Both sides go through the same normalisation
(`invoice_automation.models.normalize_invoice`) and the **normalised** values
are compared: a date against a date, an amount against a `Decimal`. So
`12/03/2026` read from a document matches `2026-03-12` in the payload, and
`1 337,750` matches `1337.750`. The question asked is whether the meaning
survived extraction, not whether the formatting did.

Two denominators are reported because they answer different questions:

- **All labelled fields** — every contract field on every document. A field
  the document genuinely lacks counts as correct when the extractor also
  reports it absent. Describes end-to-end behaviour.
- **Fields present in the ground truth** — only fields that carry a value.
  The harder figure, and the one to quote for "does it read what is there".

**Critical-field document accuracy** counts a document correct only when
every critical field is right. The critical set is stated in the results file:
document type, invoice number, supplier reference, invoice date, currency, PO
number, subtotal, tax, total. Supplier name is excluded because it is display
text that no rule keys on.

Documents whose business outcome is an exclusion (a credit note, an
unconfigured currency) are reported both inside and outside the totals, so
they cannot quietly move either figure.

**Fields invented** counts values the extractor produced that are wrong. An
explicit "could not classify" marker is a refusal, not a value, and is not
counted — counting it would overstate the failure and hide what the number is
for.

## What the current results do and do not support

The headline figures are high and that is close to meaningless on its own. The
layouts and the extractor's label sets were written together; the set is 16
documents; nothing is held out. A fair reading is: *the extractor reads the
documents this generator draws, and the pipeline behind it records them
correctly.*

The held-out layout is the result worth attention. Nothing is recognised,
nothing is recovered, and **nothing is invented**: the documents fail
`REQUIRED_FIELD_MISSING` and go to review. That is the designed failure mode,
and it is the property that would matter in production, where an unseen layout
is the normal case rather than the exception.

No confidence intervals are reported. With 16 documents any interval would be
wide enough to be useless, and quoting one would dress up a small sample as a
measurement.

## What would make this a real evaluation

In rough order of value:

1. **More layouts, with some genuinely held out.** The plan's split — about
   200 documents across five families, with a fifth layout unseen until the
   final run and the test set frozen before any tuning — is the target.
2. **Faults the rules were not built against**, so exception precision and
   recall measure detection rather than restate the rule table.
3. **A small set of real documents**, with permission and anonymised,
   reported separately. This is the only thing that can speak to readiness,
   and it would most likely show the current extractor failing often, which is
   the point of measuring it.
4. **A timed manual comparison** on 20-30 cases, same difficulty across
   conditions, with who did the work and in what order recorded. Until that
   exists, no claim about saved effort can be made at all.
