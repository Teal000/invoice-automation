"""Words, lines and tables from a PDF page.

Text extraction here is geometric rather than string-based: pdfplumber gives
every word with its position, words are grouped into lines by vertical
position, and table cells are assigned to columns by comparing each word's
horizontal centre with the column boundaries taken from the header row.

That matters for two reasons. A label and its value are only related because
they sit on the same line, and an amount written the French way ("1 337,750")
arrives as two separate words that have to be rejoined — which is also true of
real invoices, so the work is not wasted on the fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import pdfplumber

#: Vertical distance within which two words count as being on the same line.
LINE_TOLERANCE = 2.0

#: A token that could be part of an amount or a quantity: digits with comma,
#: dot or apostrophe separators, optionally signed.
_NUMERIC_TOKEN = re.compile(r"^[-+]?[\d][\d.,'  ]*$")

#: A three-letter upper-case currency code.
_CURRENCY_TOKEN = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page_number: int

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]
    page_number: int

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def top(self) -> float:
        return min(word.top for word in self.words)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            min(w.x0 for w in self.words),
            min(w.top for w in self.words),
            max(w.x1 for w in self.words),
            max(w.bottom for w in self.words),
        )

    def remainder_after(self, prefix_word_count: int) -> str:
        return " ".join(word.text for word in self.words[prefix_word_count:])


@dataclass(frozen=True)
class Document:
    """Every line of a PDF, in reading order, plus its plain text."""

    lines: tuple[Line, ...]
    raw_text: str
    page_count: int

    def find_label(
        self, labels: Sequence[str]
    ) -> tuple[Line, str, int] | None:
        """Find a line that *starts* with one of ``labels``.

        Start-anchored on purpose. On a document carrying both
        "Fournisseur :" and "Code fournisseur :", a contains-match for
        "Fournisseur" would hit the wrong line; a start-match cannot. When
        several labels match, the longest wins, so a specific label beats a
        general one that is a prefix of it.
        """
        best: tuple[Line, str, int] | None = None
        for line in self.lines:
            normalised = _normalise(line.text)
            for label in labels:
                target = _normalise(label)
                if not normalised.startswith(target):
                    continue
                consumed = len(_normalise_words(label))
                if best is None or len(target) > len(_normalise(best[1])):
                    best = (line, label, consumed)
        return best

    def value_for(self, labels: Sequence[str]) -> tuple[str, Line] | None:
        """The text following a start-anchored label on its own line."""
        found = self.find_label(labels)
        if found is None:
            return None
        line, _, consumed = found
        value = line.remainder_after(consumed).strip()
        value = value.lstrip(":-–—").strip()
        return (value, line) if value else None

    def search(self, pattern: re.Pattern[str]) -> tuple[re.Match[str], Line] | None:
        for line in self.lines:
            match = pattern.search(line.text)
            if match:
                return match, line
        return None


def _normalise(text: str) -> str:
    """Case-fold and squeeze whitespace so label matching is not brittle."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _normalise_words(text: str) -> list[str]:
    return _normalise(text).split(" ")


def load_document(path: str) -> Document:
    """Read every word of a PDF and group the words into lines."""
    lines: list[Line] = []
    texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            texts.append(page.extract_text() or "")
            words = [
                Word(
                    text=word["text"],
                    x0=float(word["x0"]),
                    x1=float(word["x1"]),
                    top=float(word["top"]),
                    bottom=float(word["bottom"]),
                    page_number=page_number,
                )
                for word in page.extract_words(use_text_flow=False)
            ]
            lines.extend(group_lines(words, page_number))
    return Document(
        lines=tuple(lines), raw_text="\n".join(texts), page_count=page_count
    )


def group_lines(words: Iterable[Word], page_number: int) -> list[Line]:
    """Group words into lines by vertical position, then sort by x."""
    remaining = sorted(words, key=lambda w: (w.top, w.x0))
    lines: list[Line] = []
    current: list[Word] = []
    for word in remaining:
        if current and abs(word.top - current[0].top) > LINE_TOLERANCE:
            lines.append(
                Line(words=tuple(sorted(current, key=lambda w: w.x0)), page_number=page_number)
            )
            current = []
        current.append(word)
    if current:
        lines.append(
            Line(words=tuple(sorted(current, key=lambda w: w.x0)), page_number=page_number)
        )
    return lines


def is_numeric_token(text: str) -> bool:
    return bool(_NUMERIC_TOKEN.match(text))


def is_currency_token(text: str) -> bool:
    return bool(_CURRENCY_TOKEN.match(text))


def trailing_amount(text: str) -> tuple[str | None, str | None]:
    """Split a line tail into (amount, currency code).

    Reads tokens from the right. A trailing currency code is taken off first,
    which is how the compact layout states its currency; then the run of
    numeric tokens is rejoined, so "Taxe (19%) 291,574" yields 291,574 rather
    than the 19 in the label, and "1 337,750" is read as one amount.
    """
    tokens = text.split()
    if not tokens:
        return None, None

    currency: str | None = None
    if is_currency_token(tokens[-1]):
        currency = tokens[-1]
        tokens = tokens[:-1]

    tail: list[str] = []
    for token in reversed(tokens):
        if is_numeric_token(token):
            tail.insert(0, token)
            continue
        break
    if not tail:
        return None, currency
    return " ".join(tail), currency


@dataclass(frozen=True)
class TableColumn:
    name: str
    x0: float
    x1: float


@dataclass(frozen=True)
class TableRow:
    cells: dict[str, str]
    line: Line


@dataclass(frozen=True)
class Table:
    header: Line
    columns: tuple[TableColumn, ...]
    rows: tuple[TableRow, ...] = field(default_factory=tuple)


def find_table(
    document: Document,
    column_synonyms: Sequence[tuple[str, Sequence[str]]],
    stop_labels: Sequence[str],
) -> Table | None:
    """Locate the line-item table and read its rows.

    ``column_synonyms`` is an ordered sequence of (column name, accepted header
    texts). A header line must match every column, which keeps a stray line
    from being mistaken for the table header. Rows are read until a line
    starts with one of ``stop_labels`` (the totals block) or stops looking
    like a row.
    """
    header_index, columns = _find_header(document, column_synonyms)
    if header_index is None:
        return None

    boundaries = _column_boundaries(columns)
    stops = [_normalise(label) for label in stop_labels]
    rows: list[TableRow] = []

    for line in document.lines[header_index + 1 :]:
        normalised = _normalise(line.text)
        if any(normalised.startswith(stop) for stop in stops):
            break
        cells = _assign_cells(line, columns, boundaries)
        # A row needs a description and at least one numeric cell; anything
        # else is a footer, a notice or a stray caption.
        numeric_cells = [
            value
            for name, value in cells.items()
            if name != columns[0].name and value and is_numeric_token(value.split()[-1])
        ]
        if not cells.get(columns[0].name) or not numeric_cells:
            break
        rows.append(TableRow(cells=cells, line=line))

    return Table(header=document.lines[header_index], columns=columns, rows=tuple(rows))


def _find_header(
    document: Document, column_synonyms: Sequence[tuple[str, Sequence[str]]]
) -> tuple[int | None, tuple[TableColumn, ...]]:
    for index, line in enumerate(document.lines):
        columns: list[TableColumn] = []
        for name, synonyms in column_synonyms:
            span = _header_span(line, synonyms)
            if span is None:
                columns = []
                break
            columns.append(TableColumn(name=name, x0=span[0], x1=span[1]))
        if columns:
            return index, tuple(columns)
    return None, ()


def _header_span(line: Line, synonyms: Sequence[str]) -> tuple[float, float] | None:
    """Find a synonym within a line and return the x-span of its words."""
    words = list(line.words)
    normalised = [_normalise(word.text) for word in words]
    for synonym in synonyms:
        target = _normalise_words(synonym)
        for start in range(len(words) - len(target) + 1):
            if normalised[start : start + len(target)] == target:
                matched = words[start : start + len(target)]
                return min(w.x0 for w in matched), max(w.x1 for w in matched)
    return None


def _column_boundaries(columns: Sequence[TableColumn]) -> list[float]:
    """Left boundary of each column, midway between adjacent headers."""
    boundaries = [float("-inf")]
    for previous, current in zip(columns, columns[1:]):
        boundaries.append((previous.x1 + current.x0) / 2)
    return boundaries


def _assign_cells(
    line: Line, columns: Sequence[TableColumn], boundaries: Sequence[float]
) -> dict[str, str]:
    buckets: dict[str, list[Word]] = {column.name: [] for column in columns}
    for word in line.words:
        index = 0
        for position, boundary in enumerate(boundaries):
            if word.centre >= boundary:
                index = position
        buckets[columns[index].name].append(word)
    return {
        name: " ".join(word.text for word in sorted(words, key=lambda w: w.x0))
        for name, words in buckets.items()
    }
