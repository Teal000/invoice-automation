-- 0003_field_evidence.sql
-- Where each extracted value was read from.
--
-- The data contract asks that an extracted value keep a connection to its
-- source. Until now a revision stored the raw values and the problems; with
-- documents entering as PDFs it also needs to say *where* on the document
-- each value came from, so a reviewer can check a figure without reading the
-- whole file and so a wrong value can be traced to a wrong label rather than
-- guessed at.
--
-- One row of evidence per field, as JSON:
--   {"field_name": "total", "page_number": 1,
--    "source_text": "Total TTC 1 591,923", "bbox": [120.0, 300.1, 575.3, 310.4]}
--
-- Empty for a revision whose values did not come from a document, which is
-- the case for every structured fixture.

ALTER TABLE invoice_revisions
    ADD COLUMN field_evidence jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN invoice_revisions.field_evidence IS
    'Per-field provenance: page number, the source line text, and its bounding box on the page. Empty when the values did not come from a document.';
