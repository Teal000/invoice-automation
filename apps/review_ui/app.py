"""The review queue.

    .venv/Scripts/python.exe -m streamlit run apps/review_ui/app.py

A reviewer opens an invoice that failed a check, sees what the rule expected
against what the document said and where on the page that value was read,
then corrects it, rejects it, or accepts it with a stated reason.

It talks to the API over HTTP and never to the database. That is deliberate:
this screen then exercises the same endpoints any other caller uses, so a gap
in the API shows up here rather than being quietly worked around with a
direct query.

Three things it refuses to do, each for a reason:

* It does not let an action through without a reason. The field is required
  here as well as in the database, so the reviewer is told before the request
  is sent rather than after.
* It does not hide a failed check once an exception is accepted. The invoice
  enters the register marked as accepted, and the failure stays visible.
* It does not re-key values silently. A correction shows the original and the
  corrected values side by side before it is submitted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from invoice_automation.api_client import ApiClient, ApiError  # noqa: E402
from invoice_automation.config import get_settings  # noqa: E402

STATE_COLOURS = {
    "validated": "🟢",
    "needs_review": "🟠",
    "unsupported": "⚪",
    "duplicate": "🔵",
    "rejected": "🔴",
}


@st.cache_resource
def _default_client(base_url: str) -> ApiClient:
    settings = get_settings()
    return ApiClient.for_base_url(base_url, settings.api_key)


def client() -> ApiClient:
    """The API client this screen uses.

    An injected client in session state wins, which is how the tests drive
    the screen against the real endpoints without a running server. Nothing
    else in the app knows the difference.
    """
    injected = st.session_state.get("api_client")
    api = injected or _default_client(
        st.session_state.get("base_url", "http://127.0.0.1:8000")
    )
    api.actor = st.session_state.get("actor") or "review-ui"
    return api


def amount(value: Any) -> str:
    return "—" if value in (None, "") else str(value)


def sidebar() -> str:
    st.sidebar.title("Review queue")
    st.sidebar.caption("Synthetic portfolio data. Nothing here is a payment.")

    actor = st.sidebar.text_input(
        "Your name",
        value=st.session_state.get("actor", ""),
        help=(
            "Recorded against every action you take. This is a label, not a "
            "sign-in: the service has one credential and no users, so the "
            "trail shows what was done and why, but cannot prove who did it."
        ),
    )
    st.session_state["actor"] = actor

    try:
        health = client().health()
        if health.get("database") == "reachable":
            st.sidebar.success(f"API reachable · {health['migrations_applied']} migrations")
        else:
            st.sidebar.error(f"API degraded: {health.get('database')}")
    except Exception as exc:  # noqa: BLE001 - a UI reports, it does not raise
        st.sidebar.error(f"API unreachable: {exc}")
        st.sidebar.info(
            "Start it with: python -m invoice_automation.cli serve"
        )

    return st.sidebar.radio(
        "View", ["Queue", "Invoice", "Overview"], label_visibility="collapsed"
    )


def queue_view() -> None:
    st.header("Invoices needing review")

    columns = st.columns([2, 2, 1])
    state = columns[0].selectbox(
        "State", ["needs_review", "validated", "unsupported", "rejected", "duplicate", "(any)"]
    )
    code = columns[1].text_input("Exception code", placeholder="e.g. TOTAL_MISMATCH")
    limit = columns[2].number_input("Limit", min_value=1, max_value=200, value=50)

    try:
        body = client().invoices(
            state=None if state == "(any)" else state,
            exception_code=code or None,
            limit=int(limit),
        )
    except ApiError as exc:
        if exc.is_unreachable:
            st.error("The API is unreachable. Is it running?")
            st.code("python -m invoice_automation.cli serve", language="bash")
        else:
            st.error(f"Could not load the queue: {exc.message}")
        return

    invoices = body["invoices"]
    if not invoices:
        st.info("Nothing matches. An empty review queue is a result, not an error.")
        return

    st.caption(f"{body['count']} shown")
    for invoice in invoices:
        marker = STATE_COLOURS.get(invoice["business_state"], "•")
        label = (
            f"{marker} {invoice['invoice_number']} · "
            f"{invoice['supplier_name'] or invoice['supplier_reference'] or 'unknown supplier'} · "
            f"{amount(invoice['total'])} {(invoice['currency'] or '').strip()}"
        )
        with st.expander(label):
            left, right = st.columns(2)
            left.metric("Failed rule codes", invoice["failed_rule_codes"])
            right.metric("Revision", invoice["current_revision_number"] or "—")
            if invoice.get("accepted_with_exception"):
                st.warning(
                    "Accepted with an exception by a reviewer. In the register, "
                    "but not because the checks passed."
                )
            if st.button("Open", key=f"open-{invoice['invoice_id']}"):
                st.session_state["invoice_id"] = invoice["invoice_id"]
                st.rerun()


def invoice_view() -> None:
    invoice_id = st.session_state.get("invoice_id")
    invoice_id = st.text_input("Invoice id", value=invoice_id or "")
    if not invoice_id:
        st.info("Pick an invoice from the queue, or paste an id.")
        return
    st.session_state["invoice_id"] = invoice_id

    try:
        detail = client().invoice(invoice_id)
    except ApiError as exc:
        st.error("The API is unreachable." if exc.is_unreachable else f"{exc.code}: {exc.message}")
        return

    marker = STATE_COLOURS.get(detail["business_state"], "•")
    st.header(f"{marker} {detail['invoice_number']}")
    st.caption(
        f"{detail.get('supplier_name') or detail.get('supplier_reference') or 'unknown supplier'} · "
        f"revision {detail.get('current_revision_number')} "
        f"({detail.get('current_revision_source') or 'unknown source'})"
    )

    if detail.get("accepted_with_exception"):
        st.warning(
            "A reviewer accepted this invoice despite a failed check. It is in "
            "the register, and the failed check below is still part of its "
            "record. It does not count towards the straight-through rate."
        )

    totals = st.columns(4)
    totals[0].metric("Subtotal", amount(detail["subtotal"]))
    totals[1].metric("Tax", amount(detail["tax_total"]))
    totals[2].metric("Total", amount(detail["total"]))
    totals[3].metric("PO", detail["purchase_order_number"] or "—")

    _checks(detail)
    _lines(detail)
    _history(detail)
    _actions(detail)


def _checks(detail: dict[str, Any]) -> None:
    st.subheader("Checks")
    current = [c for c in detail["checks"] if c.get("is_current")]
    failed = [c for c in current if c["status"] == "fail"]

    if failed:
        for check in failed:
            st.error(
                f"**{check['code']}** on `{check['field_name'] or '—'}` — "
                f"expected `{check['expected']  if 'expected' in check else check['expected_value']}`, "
                f"document said `{check['observed'] if 'observed' in check else check['observed_value']}`"
            )
    else:
        st.success("No failed checks on the current revision.")

    with st.expander("Every check, including earlier revisions"):
        st.dataframe(
            [
                {
                    "revision": c["revision_number"] if not c["submission_level"] else "arrival",
                    "current": "yes" if c.get("is_current") else "",
                    "code": c["code"],
                    "status": c["status"],
                    "field": c["field_name"],
                    "expected": c["expected_value"],
                    "observed": c["observed_value"],
                }
                for c in detail["checks"]
            ],
            width="stretch",
        )
    st.caption(
        "A skipped check is not a passed one: it means the rule could not be "
        "evaluated, and the reason is in the message."
    )


def _lines(detail: dict[str, Any]) -> None:
    st.subheader("Lines")
    st.dataframe(detail["lines"], width="stretch")


def _history(detail: dict[str, Any]) -> None:
    with st.expander("History"):
        st.write("**Revisions**")
        st.dataframe(detail["revisions"], width="stretch")
        if detail.get("review_actions"):
            st.write("**Reviewer actions**")
            st.dataframe(detail["review_actions"], width="stretch")
        st.write("**Events**")
        st.dataframe(
            [
                {k: v for k, v in event.items() if k != "details"}
                for event in detail["events"]
            ],
            width="stretch",
        )


def _actions(detail: dict[str, Any]) -> None:
    st.subheader("Decide")
    if detail["business_state"] == "rejected":
        st.info("This invoice was rejected. Submit a reissued document instead.")
        return
    if not st.session_state.get("actor"):
        st.warning("Put your name in the sidebar first: every action records who took it.")
        return

    revision = detail["current_revision_number"]
    correct_tab, reject_tab, accept_tab = st.tabs(
        ["Correct the values", "Reject", "Accept the exception"]
    )

    with correct_tab:
        st.caption(
            "Corrected values are checked by the same rules as extracted ones. "
            "A correction that introduces a different error will come back "
            "failing."
        )
        raw = detail.get("raw_values") or {}
        edited = st.text_area(
            "Corrected values (the extraction payload shape)",
            value=json.dumps(raw, indent=2) if raw else _blank_payload(detail),
            height=320,
        )
        reason = st.text_input("Reason", key="correct-reason")
        if st.button("Submit correction", type="primary"):
            if not reason.strip():
                st.error("A reason is required.")
            else:
                _submit(
                    lambda: client().correct(
                        detail["invoice_id"],
                        expected_revision_number=revision,
                        invoice=json.loads(edited),
                        reason=reason,
                    )
                )

    with reject_tab:
        st.caption(
            "Rejecting releases the supplier and invoice number, so a reissued "
            "document can be accepted later. The record and its history stay."
        )
        reason = st.text_input("Reason", key="reject-reason")
        if st.button("Reject this invoice"):
            if not reason.strip():
                st.error("A reason is required.")
            else:
                _submit(
                    lambda: client().review_action(
                        detail["invoice_id"],
                        action="reject",
                        expected_revision_number=revision,
                        reason=reason,
                    )
                )

    with accept_tab:
        st.caption(
            "The invoice enters the register with the failed check still on "
            "its record, marked as accepted by a reviewer, and excluded from "
            "the straight-through rate. This is not the same as the checks "
            "passing."
        )
        reason = st.text_input("Reason", key="accept-reason")
        if st.button("Accept with exception"):
            if not reason.strip():
                st.error("A reason is required.")
            else:
                _submit(
                    lambda: client().review_action(
                        detail["invoice_id"],
                        action="accept_exception",
                        expected_revision_number=revision,
                        reason=reason,
                    )
                )


def _blank_payload(detail: dict[str, Any]) -> str:
    """A starting point when the detail carries no raw values."""
    return json.dumps(
        {
            "invoice_number": detail["invoice_number"],
            "supplier_reference": detail["supplier_reference"],
            "invoice_date": str(detail["invoice_date"] or ""),
            "due_date": str(detail["due_date"] or ""),
            "currency": (detail["currency"] or "").strip(),
            "purchase_order_number": detail["purchase_order_number"],
            "subtotal": str(detail["subtotal"] or ""),
            "tax_total": str(detail["tax_total"] or ""),
            "total": str(detail["total"] or ""),
            "line_items": [
                {
                    "line_number": line["line_number"],
                    "description": line["description"],
                    "quantity": str(line["quantity"] or ""),
                    "unit_price": str(line["unit_price"] or ""),
                    "line_amount": str(line["line_amount"] or ""),
                }
                for line in detail["lines"]
            ],
        },
        indent=2,
    )


def _submit(action) -> None:
    try:
        outcome = action()
    except ApiError as exc:
        if exc.is_conflict and exc.code == "revision_conflict":
            st.error(
                "Somebody changed this invoice while you were working on it. "
                f"It is now at revision {exc.detail.get('current_revision_number')}. "
                "Reload before deciding, so their change is not discarded."
            )
        else:
            st.error(f"{exc.code}: {exc.message}")
        return
    except json.JSONDecodeError as exc:
        st.error(f"The corrected values are not valid JSON: {exc}")
        return

    st.success(f"Recorded. The invoice is now **{outcome['state']}**.")
    if outcome.get("note"):
        st.info(outcome["note"])
    if outcome.get("failed_codes"):
        st.warning("Still failing: " + ", ".join(outcome["failed_codes"]))
    st.rerun()


def overview_view() -> None:
    st.header("Processing overview")
    try:
        summary = client().summary()
    except ApiError as exc:
        st.error("The API is unreachable." if exc.is_unreachable else f"{exc.code}: {exc.message}")
        return

    row = st.columns(4)
    row[0].metric("Submissions", summary["submissions_received"])
    row[1].metric("Unique invoices", summary["unique_invoices"])
    row[2].metric("Needs review", summary["needs_review_invoices"])
    row[3].metric("Duplicate arrivals", summary["duplicate_arrivals"])

    rates = st.columns(2)
    rates[0].metric("Review rate", summary["review_rate"] or "—")
    rates[1].metric("Straight through", summary["straight_through_rate"] or "—")
    st.caption(
        "Both rates are shares of eligible invoices, which excludes duplicates "
        "and unsupported documents. Straight through counts only invoices the "
        "rules validated with no reviewer correction and no accepted "
        "exception. Definitions: dashboard/metric_definitions.md."
    )

    if summary["exceptions"]:
        st.subheader("Exceptions by rule")
        st.dataframe(summary["exceptions"], width="stretch")

    st.caption(summary["data_note"])


def main() -> None:
    st.set_page_config(page_title="Invoice review queue", page_icon="🧾", layout="wide")
    view = sidebar()
    if view == "Queue":
        queue_view()
    elif view == "Invoice":
        invoice_view()
    else:
        overview_view()


main()
