"""Layout specifications: the labels each document family uses.

Three families are supported, matching the three the generator draws. A
layout is a set of label synonyms and table headers, not code, so adding a
fourth family means adding a specification here, not changing the extractor.

Detection is by scoring: every layout is tried and the one that locates the
most header fields wins. A document that matches nothing well is reported as
an unknown layout rather than forced into the closest one.

Honest note: these labels were written alongside the generator in
``data/generate_pdf_fixtures.py``. Extraction accuracy measured on that set
therefore reflects the generator's assumptions, and says nothing about a
supplier document that words its labels differently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from .words import Document, Line

#: How a single field is located.
#:   label  - the value is whatever follows a start-anchored label
#:   regex  - the value is a capture group found anywhere in the document
#:   label_regex - find the label line, then apply a regex to the remainder
FieldRule = Callable[[Document], tuple[str, Line] | None]


def label(*labels: str) -> FieldRule:
    def rule(document: Document) -> tuple[str, Line] | None:
        return document.value_for(labels)

    return rule


def regex(pattern: str) -> FieldRule:
    compiled = re.compile(pattern)

    def rule(document: Document) -> tuple[str, Line] | None:
        found = document.search(compiled)
        if found is None:
            return None
        match, line = found
        value = (match.group(1) or "").strip()
        return (value, line) if value else None

    return rule


def label_regex(labels: Sequence[str], pattern: str) -> FieldRule:
    compiled = re.compile(pattern)

    def rule(document: Document) -> tuple[str, Line] | None:
        found = document.value_for(labels)
        if found is None:
            return None
        value, line = found
        match = compiled.search(value)
        if not match:
            return None
        captured = (match.group(1) or "").strip()
        return (captured, line) if captured else None

    return rule


@dataclass(frozen=True)
class LayoutSpec:
    name: str
    invoice_number: FieldRule
    invoice_date: FieldRule
    due_date: FieldRule
    purchase_order_number: FieldRule
    supplier_name: FieldRule
    supplier_reference: FieldRule
    #: Some layouts carry no currency field; the currency then comes from the
    #: code written after the total amount.
    currency: FieldRule | None
    subtotal_labels: tuple[str, ...]
    tax_labels: tuple[str, ...]
    total_labels: tuple[str, ...]
    #: Optional overrides for layouts that put several amounts on one line,
    #: where "the rightmost number after the label" would read the wrong one.
    subtotal_rule: FieldRule | None = None
    tax_rule: FieldRule | None = None
    total_rule: FieldRule | None = None
    #: Ordered (column name, accepted header texts).
    columns: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def header_rules(self) -> dict[str, FieldRule]:
        rules = {
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "purchase_order_number": self.purchase_order_number,
            "supplier_name": self.supplier_name,
            "supplier_reference": self.supplier_reference,
        }
        if self.currency is not None:
            rules["currency"] = self.currency
        return rules


#: Shared table-stop labels: the totals block of any of the three layouts.
TOTALS_LABELS = (
    "Base HT",
    "Taxes",
    "Net à régler",
    "Total HT",
    "Sous-total",
    "Montant hors taxe",
    "TVA",
    "Taxe",
    "Dont taxe",
    "Total TTC",
    "Total à payer",
    "Document synthetique",
)

ATLAS = LayoutSpec(
    name="atlas",
    invoice_number=label("Facture N° :", "Facture No :", "Facture N°"),
    invoice_date=label("Date :"),
    due_date=label("Echéance :", "Échéance :"),
    purchase_order_number=label("Bon de commande :"),
    supplier_name=label("Fournisseur :"),
    supplier_reference=label("Code fournisseur :"),
    currency=label("Devise :"),
    subtotal_labels=("Total HT",),
    tax_labels=("TVA",),
    total_labels=("Total TTC",),
    columns=(
        ("description", ("Désignation",)),
        ("quantity", ("Qté",)),
        ("unit_price", ("P.U. HT", "P.U.")),
        ("line_amount", ("Montant HT", "Montant")),
    ),
)

MEDINA = LayoutSpec(
    name="medina",
    invoice_number=label("N° de facture", "No de facture"),
    invoice_date=label("Date d'émission"),
    due_date=label("Date d'échéance"),
    purchase_order_number=label("Réf. commande", "Ref. commande"),
    # This layout prints the supplier name as the document's own heading,
    # with no label, so it is read as the line above the strapline.
    supplier_name=regex(r"^(.+\(synthetic\))$"),
    supplier_reference=label("Identifiant fournisseur"),
    currency=label("Monnaie"),
    subtotal_labels=("Sous-total",),
    tax_labels=("Taxe",),
    total_labels=("Total à payer",),
    columns=(
        ("description", ("Description",)),
        ("quantity", ("Quantité",)),
        ("unit_price", ("Prix unitaire",)),
        ("line_amount", ("Total ligne",)),
    ),
)

CARTHAGE = LayoutSpec(
    name="carthage",
    # The number is in the title line: "FACTURE N° XX-2026-0001".
    invoice_number=regex(r"N°\s+(\S+)\s*$"),
    invoice_date=label_regex(("Emise le", "Émise le"), r"(.+)"),
    due_date=label_regex(("Règlement avant le", "Reglement avant le"), r"(.+)"),
    purchase_order_number=label("Commande :"),
    # "Fournisseur : Name (synthetic) [SUP-999]" - name before the bracket.
    supplier_name=label_regex(("Fournisseur :",), r"^(.*?)\s*\[[^\]]*\]\s*$"),
    supplier_reference=label_regex(("Fournisseur :",), r"\[([^\]]+)\]"),
    currency=None,
    subtotal_labels=("Montant hors taxe",),
    tax_labels=("Dont taxe",),
    total_labels=("Total TTC",),
    columns=(
        ("description", ("Article",)),
        ("quantity", ("Nombre",)),
        ("unit_price", ("Tarif",)),
        ("line_amount", ("Somme",)),
    ),
)


#: Amounts on one line: "Base HT 1 114,520 / Taxes 211,759 / Net a regler 1 326,279".
#: Reading the rightmost number after the label would return the final total
#: for all three, so each is captured by position instead.
_AMOUNT = r"([\d][\d\s.,]*)"

SOUSSE = LayoutSpec(
    name="sousse",
    invoice_number=label("Numéro de pièce", "Numero de piece"),
    invoice_date=label("Daté du", "Date du"),
    due_date=label("Exigible le"),
    purchase_order_number=label("Rattaché à la commande", "Rattache a la commande"),
    # "Emetteur" also prefixes "Emetteur ref.", and the longest label wins, so
    # the reference does not capture the name or the other way round.
    supplier_name=label("Emetteur"),
    supplier_reference=label("Emetteur réf.", "Emetteur ref."),
    currency=label("Unité monétaire", "Unite monetaire"),
    subtotal_labels=("Base HT",),
    tax_labels=("Taxes",),
    total_labels=("Net à régler", "Net a regler"),
    subtotal_rule=regex(r"Base HT\s+" + _AMOUNT + r"\s*/"),
    tax_rule=regex(r"Taxes\s+" + _AMOUNT + r"\s*/"),
    total_rule=regex(r"Net à régler\s+" + _AMOUNT + r"\s*$"),
    columns=(
        ("description", ("Libellé",)),
        ("quantity", ("Nb",)),
        ("unit_price", ("Prix",)),
        ("line_amount", ("Valeur",)),
    ),
)

LAYOUTS: tuple[LayoutSpec, ...] = (ATLAS, MEDINA, CARTHAGE, SOUSSE)

#: Document titles, longest first so a specific title is not shadowed by a
#: shorter one that is a prefix of it.
DOCUMENT_TITLES: tuple[tuple[str, str], ...] = (
    ("facture proforma", "proforma"),
    ("facture partielle", "partial_invoice"),
    ("note de debit", "debit_note"),
    ("releve de compte", "statement"),
    ("avoir", "credit_note"),
    ("facture", "invoice"),
)


def score_layout(spec: LayoutSpec, document: Document) -> int:
    """How many of a layout's header fields and totals it can locate."""
    score = sum(1 for rule in spec.header_rules().values() if rule(document) is not None)
    for labels in (spec.subtotal_labels, spec.tax_labels, spec.total_labels):
        if document.find_label(labels) is not None:
            score += 1
    return score


def detect_layout(document: Document) -> tuple[LayoutSpec | None, dict[str, int]]:
    """Pick the best-matching layout, or ``None`` when nothing matches well.

    A layout must locate at least half of what it looks for. Below that the
    document is reported as an unknown layout, which routes it to review
    rather than producing values read with the wrong label set.
    """
    scores = {spec.name: score_layout(spec, document) for spec in LAYOUTS}
    best = max(LAYOUTS, key=lambda spec: scores[spec.name])
    possible = len(best.header_rules()) + 3
    if scores[best.name] < possible / 2:
        return None, scores
    return best, scores
