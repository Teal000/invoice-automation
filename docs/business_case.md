# Invoice processing and exception reporting — the case in one page

*Draft, 6 November 2026. Every figure here comes from
`evaluation/results/`; every number not measured is named as missing rather
than estimated.*

## The process

A small distributor receives supplier invoices as PDFs. Someone keys the
details in, checks the arithmetic, looks for documents already received,
compares each invoice with its purchase order, and chases whatever disagrees.
Reporting on any of it means more spreadsheet work.

The repetitive parts — re-entry, arithmetic, duplicate spotting, order
matching — are rule-based. The judgement parts — what to do about a mismatch,
whether to accept a documented exception — are not. This project automates
the first and makes the second quick to act on, leaving a record of both.

**Everything in the demonstration is invented**: the distributor, its
suppliers, its purchase orders, every invoice and every amount.

## What was built

A document arrives over HTTP or the command line. It is stored under a hash
of its own bytes, recorded as an arrival, and queued. Processing then reads
it, extracts the values, runs thirteen deterministic checks, and records an
outcome — validated, needs review, duplicate, or unsupported — with the
expected and observed value stored for every failed check.

A reviewer can correct the values, reject the invoice, or accept it despite a
failed check, always with a stated reason. Corrected values go through the
same rules as extracted ones; an edit cannot bypass a check.

n8n triggers processing over the API and retries it. PostgreSQL holds the
authoritative record: arrivals, revisions, rule outcomes, reviewer decisions
and an append-only event log that the database itself refuses to alter.

Built with Python, PostgreSQL, FastAPI, pdfplumber, n8n, Streamlit and
Docker. 332 automated tests, run against PostgreSQL 16.2 and 18.6.

## What was measured

A corpus of **200 synthetic documents** across five layout families, split
120 development / 40 validation / **40 frozen test**. The test split contains
a layout the extractor was never taught. Method and caveats:
`evaluation/methodology.md`.

Frozen test split, run once:

| | Layouts it was taught (32) | A layout it had never seen (8) |
|---|---|---|
| Layout identified | 32/32 | **0/8** |
| Every critical field correct | 32/32 | **0/8** |
| **Wrong values produced** | — | **0** |

Fault detection across the same 40 documents: **19/19** of the faults a rule
targets, and **0 of 4** of the faults deliberately seeded that no rule
targets — a supplier name disagreeing with a correct code, an invoice dated
next year, a zero-quantity line, a duplicated line. (A fifth undetectable
case was flagged only because its layout was unreadable, not because the
fault was noticed.)

## What those numbers mean

**On familiar layouts it is accurate, and that is the least interesting
result.** The layouts and the extractor's label sets were written by the same
person, so a high score there measures consistency, not capability.

**On an unfamiliar layout it reads nothing and invents nothing.** The eight
unseen documents produced no values at all and were routed to a person. That
is the designed failure: reading nothing is recoverable, while reading the
wrong label into a total is not, because a wrong total that agrees with a
wrong subtotal passes every arithmetic check. This is the property that would
matter on a real supplier's template.

**It catches exactly what it was told to catch.** The seeded faults no rule
targets were missed completely. That is a measured blind spot rather than an
assumed one, and it sets the honest expectation: this system enforces the
controls that were written down, and nothing else.

## What this does not establish

- Nothing about **real supplier invoices**. The documents come from one
  generator. The closest evidence here — the unseen layout — says the
  extractor reads nothing from an unfamiliar template.
- Nothing about **scanned documents**. There is no OCR.
- Nothing about **time or cost saved**. No work has been timed, by hand or
  by machine, so there is no saving to report.
- Nothing about **reliability at volume**. One machine, synthetic data,
  injected failures in tests rather than observed ones in use.
- It is not a compliance control, not fraud detection, and it pays nothing.
  "Accepted" means recorded in a demonstration register.

## What would come next, in order of value

1. **A small set of real documents**, with permission and anonymised, scored
   separately. The only thing that can speak to readiness — and the most
   likely outcome is that the extractor fails often, which is the point of
   measuring it.
2. **A timed comparison** on 20–30 cases of similar difficulty, recording who
   did the work and in what order. Until that exists no claim about effort
   can be made at all.
3. **More fault types the rules do not target**, so the blind-spot figure
   stays honest as rules are added.
4. OCR for scanned documents, and an authenticated reviewer identity so the
   audit trail can answer *who*, not only *what* and *why*.
