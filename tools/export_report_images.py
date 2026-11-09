"""Turn the Power BI report's exported PDF into page images.

Power BI Desktop has no command line and no headless export: the only way to
get the rendered report out of it is a human choosing File > Export > Export
to PDF. Everything after that is mechanical, so it lives here rather than in
a list of manual steps that nobody repeats the same way twice.

    .\\.venv\\Scripts\\python.exe tools\\export_report_images.py

Reads dashboard/screenshots/report.pdf and writes one PNG per page, named
after the report page it shows. It refuses to run if the page count does not
match the expected page order, because a renamed or reordered page would
otherwise produce images with confidently wrong filenames.

The figures a reader should check each image against are written to
dashboard/screenshots/README.md by tools/reconcile_dashboard.py's output,
which this script asks you to paste rather than inventing: the review-queue
bands change with the clock, so an image is only interpretable beside the
moment it was taken.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover - a missing dev dependency
    sys.exit("pymupdf is not installed. pip install -r requirements-dev.txt")

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "dashboard" / "screenshots"
PDF = SHOTS / "report.pdf"

#: The order Power BI exports pages in is the report's own page order.
EXPECTED_PAGES = [
    "1-processing-overview",
    "2-exception-analysis",
    "3-invoice-register",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="Render resolution. 150 is legible at full width without being "
             "a multi-megabyte file in the repository.",
    )
    parser.add_argument("--pdf", type=Path, default=PDF)
    args = parser.parse_args()

    if not args.pdf.exists():
        sys.exit(
            f"{args.pdf} not found.\n\n"
            "In Power BI Desktop: File > Export > Export to PDF, and save it "
            "there. Refresh first, so the images and the reconciliation "
            "describe the same data."
        )

    with pymupdf.open(args.pdf) as doc:
        if doc.page_count != len(EXPECTED_PAGES):
            sys.exit(
                f"The PDF has {doc.page_count} pages but {len(EXPECTED_PAGES)} "
                f"were expected ({', '.join(EXPECTED_PAGES)}). Refusing to "
                "guess which image is which; update EXPECTED_PAGES if the "
                "report gained or lost a page."
            )
        written = []
        for page, name in zip(doc, EXPECTED_PAGES):
            out = SHOTS / f"{name}.png"
            page.get_pixmap(dpi=args.dpi).save(out)
            written.append((out, out.stat().st_size))

    for out, size in written:
        print(f"  {out.relative_to(ROOT)}  {size / 1024:.0f} KB")
    print(f"\n{len(written)} images written at {args.dpi} dpi.")
    print("Record what they show: tools/reconcile_dashboard.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
