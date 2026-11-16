"""Build the as-is and to-be process maps as BPMN 2.0, and render them to SVG.

    .\\.venv\\Scripts\\python.exe tools\\build_process_maps.py            # .bpmn only
    .\\.venv\\Scripts\\python.exe tools\\build_process_maps.py --render   # .bpmn and .svg

The maps are generated rather than drawn, for the same reason the dashboard
figures are reconciled by a script: a picture of a process drifts from the
process. Each map is one spec below. From it this writes `docs/process/*.bpmn`,
which opens in any BPMN modeller (Camunda Modeler, bpmn.io, Bizagi), and with
`--render` an SVG drawn from that file by bpmn-js, the renderer bpmn.io itself
uses. The SVG is therefore a rendering of the .bpmn, not a second drawing that
could disagree with it.

Rendering needs Microsoft Edge or Chrome and a network connection, because
bpmn-js is loaded from unpkg at a pinned version. The .bpmn files need neither.
`tests/unit/test_process_maps.py` checks the committed .bpmn still matches this
spec and that each SVG shows every element in it.

The to-be map follows the code, not the plan. An identical file is not caught
before extraction: `pipeline.process_submission` extracts first, then runs all
thirteen rules together, `EXACT_FILE_DUPLICATE` among them, and only then links
a duplicate to the earlier invoice. The map was first drawn the other way round.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "process"
BPMN_JS = "https://unpkg.com/bpmn-js@17.11.1/dist/bpmn-viewer.production.min.js"
BROWSERS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
)

# ── geometry ─────────────────────────────────────────────────────────────────
POOL_X = 150
POOL_LABEL = 30          # the band holding a pool's name
LANE_LABEL = 30          # the band holding a lane's name
COL_W = 175
X0 = POOL_X + POOL_LABEL + LANE_LABEL + 85   # centre of column 0
SUPPLIER_Y, SUPPLIER_H = 30, 70
POOL_Y = 140
SIZES = {"task": (130, 80), "event": (36, 36), "gateway": (50, 50)}

TASK_TAGS = {"manualTask", "serviceTask", "userTask", "task"}
EVENT_TAGS = {"startEvent", "endEvent", "boundaryEvent"}


@dataclass
class Lane:
    id: str
    name: str
    rows: list[tuple[str, int]]          # (row name, height), top to bottom


@dataclass
class Node:
    id: str
    tag: str                             # BPMN element name, e.g. "userTask"
    name: str
    lane: str
    row: str
    col: float
    event: str = ""                      # "message", "timer" or "error"
    attached_to: str = ""                # boundary events only


@dataclass
class Flow:
    id: str
    src: str
    tgt: str
    name: str = ""
    exit: str | tuple[str, int] = "right"
    enter: str | tuple[str, int] = "left"
    via: float | None = None             # y (or x) of the middle segment, if forced
    label_at: str = "start"              # "start", "turn" (first corner) or "middle"


@dataclass
class Note:
    id: str
    text: str
    lane: str
    row: str
    col: float
    anchor: str                          # node the note is associated with
    anchor_side: str = "top"
    note_side: str = "bottom"


@dataclass
class Map:
    key: str
    title: str
    lanes: list[Lane]
    nodes: list[Node]
    flows: list[Flow]
    notes: list[Note] = field(default_factory=list)
    message_name: str = "Invoice PDF"
    message_target: str = ""


# ── the two maps ─────────────────────────────────────────────────────────────

AS_IS = Map(
    key="as_is",
    title="Supplier invoices today (as-is)",
    lanes=[
        Lane("L_analyst", "Operations analyst", [("notes", 80), ("main", 130), ("exception", 120)]),
        Lane("L_finance", "Finance manager", [("main", 130), ("notes", 80)]),
    ],
    nodes=[
        Node("s_in", "startEvent", "Invoice PDF received by email", "L_analyst", "main", 0,
             event="message"),
        Node("t_key", "manualTask", "Re-key the invoice into the system", "L_analyst", "main", 1),
        Node("t_arith", "manualTask", "Check the line and total arithmetic", "L_analyst", "main", 2),
        Node("t_dup", "manualTask", "Search for an earlier copy", "L_analyst", "main", 3),
        Node("t_po", "manualTask", "Find the purchase order and compare", "L_analyst", "main", 4),
        Node("g_ok", "exclusiveGateway", "Everything agrees?", "L_analyst", "main", 5),
        Node("t_record", "manualTask", "Mark the invoice as checked", "L_analyst", "main", 6),
        Node("e_done", "endEvent", "Invoice in the register", "L_analyst", "main", 7),
        Node("t_follow", "manualTask", "Chase the supplier or a colleague", "L_analyst",
             "exception", 5),
        Node("t_fix", "manualTask", "Correct the record", "L_analyst", "exception", 6),
        Node("s_month", "startEvent", "Report due", "L_finance", "main", 3, event="timer"),
        Node("t_sheet", "manualTask", "Rebuild the figures in a spreadsheet", "L_finance", "main", 4),
        Node("e_rep", "endEvent", "Report sent", "L_finance", "main", 5),
    ],
    flows=[
        Flow("f_in", "s_in", "t_key"),
        Flow("f_key", "t_key", "t_arith"),
        Flow("f_arith", "t_arith", "t_dup"),
        Flow("f_dup", "t_dup", "t_po"),
        Flow("f_po", "t_po", "g_ok"),
        Flow("f_yes", "g_ok", "t_record", name="Yes"),
        Flow("f_no", "g_ok", "t_follow", name="No", exit="bottom", enter="top"),
        Flow("f_follow", "t_follow", "t_fix"),
        Flow("f_fixed", "t_fix", "t_record", exit="top", enter="bottom"),
        Flow("f_done", "t_record", "e_done"),
        Flow("f_month", "s_month", "t_sheet"),
        Flow("f_sheet", "t_sheet", "e_rep"),
    ],
    notes=[
        Note("n_key", "Every value typed by hand", "L_analyst", "notes", 1, "t_key"),
        Note("n_dup", "Depends on someone finding the earlier copy", "L_analyst", "notes", 3,
             "t_dup"),
        Note("n_fix", "No re-check of the corrected values, and no record of why",
             "L_analyst", "exception", 7.2, "t_fix", anchor_side="right", note_side="left"),
        Note("n_sheet", "Rebuilt by hand each time it is needed", "L_finance", "notes", 4,
             "t_sheet", anchor_side="bottom", note_side="top"),
    ],
    message_target="s_in",
)

TO_BE = Map(
    key="to_be",
    title="Supplier invoices with the service (to-be, as built)",
    lanes=[
        Lane("L_service", "Invoice service (API + n8n)",
             [("band", 40), ("main", 130), ("branch", 110), ("retry", 110)]),
        Lane("L_reviewer", "Reviewer",
             [("band", 20), ("main", 130), ("accept", 110), ("reject", 135)]),
        Lane("L_finance", "Finance manager", [("main", 140)]),
    ],
    nodes=[
        Node("s_up", "startEvent", "Invoice PDF uploaded", "L_service", "main", 0,
             event="message"),
        Node("t_store", "serviceTask", "Store the file and record the arrival", "L_service",
             "main", 1),
        Node("t_extract", "serviceTask", "Extract header and line values", "L_service",
             "main", 2),
        Node("b_fail", "boundaryEvent", "Technical failure", "L_service", "main", 2,
             event="error", attached_to="t_extract"),
        Node("t_rules", "serviceTask", "Run the 13 validation rules", "L_service", "main", 3),
        Node("g_dup", "exclusiveGateway", "Exact copy of a file already received?",
             "L_service", "main", 4),
        Node("g_sup", "exclusiveGateway", "Supported document?", "L_service", "main", 5),
        Node("g_pass", "exclusiveGateway", "Every check passed?", "L_service", "main", 6),
        Node("e_valid", "endEvent", "Validated, in the register", "L_service", "main", 7),
        Node("t_link", "serviceTask", "Link to the earlier invoice", "L_service", "branch", 4),
        Node("e_dup", "endEvent", "No second invoice created", "L_service", "retry", 4),
        Node("e_unsup", "endEvent", "Recorded as unsupported", "L_service", "branch", 5),
        Node("t_retry", "serviceTask", "Keep the job; the recovery sweep retries it",
             "L_service", "retry", 3),
        Node("t_review", "userTask", "Review the failed checks", "L_reviewer", "main", 6),
        Node("g_dec", "exclusiveGateway", "Decision", "L_reviewer", "main", 7),
        Node("t_correct", "userTask", "Enter the corrected values and a reason", "L_reviewer",
             "main", 8),
        Node("t_accept", "userTask", "Accept as an exception, with a reason", "L_reviewer",
             "accept", 8),
        Node("e_acc", "endEvent", "In the register, marked as an exception", "L_reviewer",
             "accept", 9),
        Node("t_reject", "userTask", "Reject, with a reason", "L_reviewer", "reject", 7),
        Node("e_rej", "endEvent", "Rejected; the invoice number is released", "L_reviewer",
             "reject", 8),
        Node("s_fin", "startEvent", "Figures needed", "L_finance", "main", 1),
        Node("t_dash", "userTask", "Read the Power BI dashboard", "L_finance", "main", 2),
        Node("e_fin", "endEvent", "Figures that reconcile to the records", "L_finance",
             "main", 3),
    ],
    flows=[
        Flow("f_up", "s_up", "t_store"),
        Flow("f_store", "t_store", "t_extract"),
        Flow("f_extract", "t_extract", "t_rules"),
        Flow("f_rules", "t_rules", "g_dup"),
        Flow("f_dup_yes", "g_dup", "t_link", name="Yes", exit="bottom", enter="top"),
        Flow("f_dup_no", "g_dup", "g_sup", name="No"),
        Flow("f_link", "t_link", "e_dup", exit="bottom", enter="top"),
        Flow("f_sup_no", "g_sup", "e_unsup", name="No", exit="bottom", enter="top"),
        Flow("f_sup_yes", "g_sup", "g_pass", name="Yes"),
        Flow("f_pass_yes", "g_pass", "e_valid", name="Yes"),
        Flow("f_pass_no", "g_pass", "t_review", name="No", exit="bottom", enter="top"),
        Flow("f_fail", "b_fail", "t_retry", exit="bottom", enter="left"),
        Flow("f_retry", "t_retry", "t_extract", name="Retried", exit="top",
             enter=("bottom", 35), via=-1),
        Flow("f_review", "t_review", "g_dec"),
        Flow("f_correct", "g_dec", "t_correct", name="Correct"),
        Flow("f_recheck", "t_correct", "t_rules", name="Checked again by the same rules",
             exit="top", enter="top", via=-2),
        Flow("f_accept", "g_dec", "t_accept", name="Accept", exit="bottom", enter="left",
             label_at="turn"),
        Flow("f_accepted", "t_accept", "e_acc"),
        Flow("f_reject", "g_dec", "t_reject", name="Reject", exit="bottom", enter="top",
             label_at="middle"),
        Flow("f_rejected", "t_reject", "e_rej"),
        Flow("f_fin", "s_fin", "t_dash"),
        Flow("f_dash", "t_dash", "e_fin"),
    ],
    message_target="s_up",
)

MAPS = (AS_IS, TO_BE)


# ── layout ───────────────────────────────────────────────────────────────────

@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def port(self, side: str | tuple[str, int]) -> tuple[float, float]:
        name, offset = (side, 0) if isinstance(side, str) else side
        return {
            "right": (self.x + self.w, self.cy + offset),
            "left": (self.x, self.cy + offset),
            "top": (self.cx + offset, self.y),
            "bottom": (self.cx + offset, self.y + self.h),
        }[name]


def _kind(tag: str) -> str:
    if tag in TASK_TAGS:
        return "task"
    if tag in EVENT_TAGS:
        return "event"
    return "gateway"


class Layout:
    """Places every lane, row and node, and routes every flow orthogonally."""

    def __init__(self, m: Map) -> None:
        self.m = m
        self.max_col = max([n.col for n in m.nodes] + [nt.col for nt in m.notes])
        self.width = (X0 - POOL_X) + self.max_col * COL_W + 65 + 60
        self.lane_box: dict[str, Box] = {}
        self.row_cy: dict[tuple[str, str], float] = {}
        self.row_top: dict[tuple[str, str], float] = {}
        y = POOL_Y
        for lane in m.lanes:
            h = sum(height for _, height in lane.rows)
            self.lane_box[lane.id] = Box(POOL_X + POOL_LABEL, y, self.width - POOL_LABEL, h)
            ry = y
            for row, height in lane.rows:
                self.row_top[(lane.id, row)] = ry
                self.row_cy[(lane.id, row)] = ry + height / 2
                ry += height
            y += h
        self.pool = Box(POOL_X, POOL_Y, self.width, y - POOL_Y)
        self.supplier = Box(POOL_X, SUPPLIER_Y, self.width, SUPPLIER_H)

        self.box: dict[str, Box] = {}
        for n in m.nodes:
            if n.tag == "boundaryEvent":
                continue
            w, h = SIZES[_kind(n.tag)]
            cx = X0 + n.col * COL_W
            cy = self.row_cy[(n.lane, n.row)]
            self.box[n.id] = Box(cx - w / 2, cy - h / 2, w, h)
        for n in m.nodes:
            if n.tag == "boundaryEvent":
                host = self.box[n.attached_to]
                # Sits on the host's bottom edge, left of centre, so the retry can
                # come back in on the right without the two crossing.
                self.box[n.id] = Box(host.cx - 35 - 18, host.y + host.h - 18, 36, 36)
        for nt in m.notes:
            cx = X0 + nt.col * COL_W
            cy = self.row_cy[(nt.lane, nt.row)]
            self.box[nt.id] = Box(cx - 80, cy - 26, 160, 52)

    def via_y(self, flow: Flow) -> float:
        """The y of a forced middle segment. -1: between the service lane's first
        two rows. -2: the empty band at the top of the first lane."""
        first = self.m.lanes[0]
        if flow.via == -1:
            return self.row_top[(first.id, first.rows[2][0])]
        if flow.via == -2:
            return self.row_top[(first.id, first.rows[0][0])] + 20
        return float(flow.via)  # type: ignore[arg-type]

    def route(self, f: Flow) -> list[tuple[float, float]]:
        a, b = self.box[f.src], self.box[f.tgt]
        p0, p3 = a.port(f.exit), b.port(f.enter)
        out_h = (f.exit if isinstance(f.exit, str) else f.exit[0]) in ("left", "right")
        in_h = (f.enter if isinstance(f.enter, str) else f.enter[0]) in ("left", "right")
        if out_h and in_h:
            if p0[1] == p3[1]:
                return [p0, p3]
            mx = (p0[0] + p3[0]) / 2
            return [p0, (mx, p0[1]), (mx, p3[1]), p3]
        if not out_h and in_h:
            return [p0, (p0[0], p3[1]), p3]
        if out_h and not in_h:
            return [p0, (p3[0], p0[1]), p3]
        if p0[0] == p3[0] and f.via is None:
            return [p0, p3]
        y = self.via_y(f) if f.via is not None else (p0[1] + p3[1]) / 2
        return [p0, (p0[0], y), (p3[0], y), p3]


# ── BPMN XML ─────────────────────────────────────────────────────────────────

def _r(v: float) -> str:
    return str(int(round(v)))


def _label_box(m: Map, lay: Layout, node: Node) -> Box | None:
    """External labels for events and gateways; tasks carry their name inside."""
    b = lay.box[node.id]
    kind = _kind(node.tag)
    if kind == "task":
        return None
    if kind == "gateway":
        # Above, because "No" leaves most gateways from the bottom.
        return Box(b.cx - 65, b.y - 40, 130, 34)
    if node.tag == "boundaryEvent":
        return Box(b.x - 92, b.y + 20, 90, 28)
    return Box(b.cx - 60, b.y + b.h + 6, 120, 40)


def _flow_label(points: list[tuple[float, float]], f: Flow) -> Box:
    """bpmn-js wraps an external label at about 90px whatever its bounds say, so
    the boxes here are sized for the wrapped text, not for one long line."""
    (x0, y0), (x1, y1) = points[0], points[1]
    if f.via == -2:                    # the long loop over the top: two lines above it
        mx = (points[1][0] + points[2][0]) / 2
        return Box(mx - 45, points[1][1] - 32, 90, 28)
    if f.label_at == "turn":           # where a shared first segment splits off
        return Box(x1 + 10, y1 - 18, 60, 14)
    if f.label_at == "middle":         # three-quarters down the last segment
        # Not halfway: when two flows share a gateway's bottom, the halfway point
        # of the straight one is exactly where the other turns, and the two
        # labels print on top of each other.
        (xa, ya), (xb, yb) = points[-2], points[-1]
        return Box(xb + 6, ya + (yb - ya) * 0.75, 60, 14)
    if y0 == y1:                       # leaves sideways
        return Box(x0 + 6, y0 - 18, 60, 14)
    if y1 < y0:                        # leaves upwards: above the source, not inside it
        return Box(x0 + 6, y0 - 18, 60, 14)
    return Box(x0 + 6, y0 + 4, 60, 14)  # leaves downwards


def build_xml(m: Map) -> str:
    lay = Layout(m)
    incoming: dict[str, list[str]] = {n.id: [] for n in m.nodes}
    outgoing: dict[str, list[str]] = {n.id: [] for n in m.nodes}
    for f in m.flows:
        outgoing[f.src].append(f.id)
        incoming[f.tgt].append(f.id)

    proc, collab = f"Process_{m.key}", f"Collaboration_{m.key}"
    x: list[str] = []
    add = x.append
    add('<?xml version="1.0" encoding="UTF-8"?>')
    add('<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
        'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
        'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
        f'id="Definitions_{m.key}" '
        'targetNamespace="https://github.com/Teal000/invoice-automation" '
        'exporter="tools/build_process_maps.py" exporterVersion="1">')
    add(f'  <bpmn:collaboration id="{collab}">')
    add(f'    <bpmn:documentation>{escape(m.title)}. Generated from '
        'tools/build_process_maps.py; edit the spec there, not this file.</bpmn:documentation>')
    add('    <bpmn:participant id="P_supplier" name="Supplier" />')
    add(f'    <bpmn:participant id="P_distributor" name="Distributor" processRef="{proc}" />')
    add(f'    <bpmn:messageFlow id="M_invoice" name={quoteattr(m.message_name)} '
        f'sourceRef="P_supplier" targetRef="{m.message_target}" />')
    add('  </bpmn:collaboration>')

    add(f'  <bpmn:process id="{proc}" isExecutable="false">')
    add(f'    <bpmn:laneSet id="LaneSet_{m.key}">')
    for lane in m.lanes:
        add(f'      <bpmn:lane id="{lane.id}" name={quoteattr(lane.name)}>')
        for n in m.nodes:
            if n.lane == lane.id:
                add(f'        <bpmn:flowNodeRef>{n.id}</bpmn:flowNodeRef>')
        add('      </bpmn:lane>')
    add('    </bpmn:laneSet>')
    for n in m.nodes:
        attrs = f'id="{n.id}" name={quoteattr(n.name)}'
        if n.tag == "boundaryEvent":
            attrs += f' attachedToRef="{n.attached_to}"'
        add(f'    <bpmn:{n.tag} {attrs}>')
        for fid in incoming[n.id]:
            add(f'      <bpmn:incoming>{fid}</bpmn:incoming>')
        for fid in outgoing[n.id]:
            add(f'      <bpmn:outgoing>{fid}</bpmn:outgoing>')
        if n.event == "message":
            add(f'      <bpmn:messageEventDefinition id="{n.id}_def" />')
        elif n.event == "timer":
            add(f'      <bpmn:timerEventDefinition id="{n.id}_def" />')
        elif n.event == "error":
            add(f'      <bpmn:errorEventDefinition id="{n.id}_def" />')
        add(f'    </bpmn:{n.tag}>')
    for f in m.flows:
        name = f" name={quoteattr(f.name)}" if f.name else ""
        add(f'    <bpmn:sequenceFlow id="{f.id}"{name} sourceRef="{f.src}" targetRef="{f.tgt}" />')
    for nt in m.notes:
        add(f'    <bpmn:textAnnotation id="{nt.id}">')
        add(f'      <bpmn:text>{escape(nt.text)}</bpmn:text>')
        add('    </bpmn:textAnnotation>')
        add(f'    <bpmn:association id="{nt.id}_link" sourceRef="{nt.anchor}" '
            f'targetRef="{nt.id}" />')
    add('  </bpmn:process>')

    # ── diagram interchange ──
    add(f'  <bpmndi:BPMNDiagram id="Diagram_{m.key}">')
    add(f'    <bpmndi:BPMNPlane id="Plane_{m.key}" bpmnElement="{collab}">')

    def shape(element: str, b: Box, extra: str = "", label: Box | None = None) -> None:
        add(f'      <bpmndi:BPMNShape id="{element}_di" bpmnElement="{element}"{extra}>')
        add(f'        <dc:Bounds x="{_r(b.x)}" y="{_r(b.y)}" width="{_r(b.w)}" height="{_r(b.h)}" />')
        if label is not None:
            add('        <bpmndi:BPMNLabel>')
            add(f'          <dc:Bounds x="{_r(label.x)}" y="{_r(label.y)}" '
                f'width="{_r(label.w)}" height="{_r(label.h)}" />')
            add('        </bpmndi:BPMNLabel>')
        add('      </bpmndi:BPMNShape>')

    def edge(element: str, points: list[tuple[float, float]], label: Box | None = None) -> None:
        add(f'      <bpmndi:BPMNEdge id="{element}_di" bpmnElement="{element}">')
        for px, py in points:
            add(f'        <di:waypoint x="{_r(px)}" y="{_r(py)}" />')
        if label is not None:
            add('        <bpmndi:BPMNLabel>')
            add(f'          <dc:Bounds x="{_r(label.x)}" y="{_r(label.y)}" '
                f'width="{_r(label.w)}" height="{_r(label.h)}" />')
            add('        </bpmndi:BPMNLabel>')
        add('      </bpmndi:BPMNEdge>')

    shape("P_supplier", lay.supplier, ' isHorizontal="true"')
    shape("P_distributor", lay.pool, ' isHorizontal="true"')
    for lane in m.lanes:
        shape(lane.id, lay.lane_box[lane.id], ' isHorizontal="true"')
    for n in m.nodes:
        extra = ' isMarkerVisible="true"' if n.tag == "exclusiveGateway" else ""
        shape(n.id, lay.box[n.id], extra, _label_box(m, lay, n))
    for f in m.flows:
        pts = lay.route(f)
        edge(f.id, pts, _flow_label(pts, f) if f.name else None)
    target = lay.box[m.message_target]
    msg = [(target.cx, lay.supplier.y + lay.supplier.h), (target.cx, target.y)]
    edge("M_invoice", msg, Box(target.cx + 6, lay.supplier.y + lay.supplier.h + 8, 80, 14))
    for nt in m.notes:
        shape(nt.id, lay.box[nt.id])
        a = lay.box[nt.anchor].port(nt.anchor_side)
        b = lay.box[nt.id].port(nt.note_side)
        edge(f"{nt.id}_link", [a, b])
    add('    </bpmndi:BPMNPlane>')
    add('  </bpmndi:BPMNDiagram>')
    add('</bpmn:definitions>')
    return "\n".join(x) + "\n"


# ── rendering ────────────────────────────────────────────────────────────────

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>render</title>
<style>html,body,#c{{height:2400px;width:2400px;margin:0;background:#fff}}</style></head>
<body><div id="c"></div>
<script src="{bpmn_js}"></script>
<script type="text/plain" id="xml">{xml}</script>
<script>
(async () => {{
  const out = (s) => document.body.insertAdjacentHTML("beforeend",
    "<textarea id=out>" + s.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</textarea>");
  try {{
    const viewer = new BpmnJS({{ container: "#c" }});
    const result = await viewer.importXML(document.getElementById("xml").textContent.trim());
    if (result.warnings.length) {{ out("WARNINGS " + result.warnings.map(w => w.message).join(" | ")); return; }}
    let {{ svg }} = await viewer.saveSVG();
    // A white ground, so the diagram stays legible on GitHub's dark theme.
    svg = svg.replace(/(<svg[^>]*>)/, '$1<rect x="-10000" y="-10000" width="30000" height="30000" fill="#ffffff"/>');
    out(svg);
  }} catch (e) {{ out("ERROR " + e); }}
}})();
</script></body></html>
"""


def find_browser() -> Path:
    for path in BROWSERS:
        if path.exists():
            return path
    found = shutil.which("msedge") or shutil.which("chrome")
    if found:
        return Path(found)
    sys.exit("No Edge or Chrome found; the .bpmn files are written, the SVGs are not.")


def render(xml: str, browser: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "render.html"
        page.write_text(PAGE.format(bpmn_js=BPMN_JS, xml=xml.replace("</script", "<\\/script")),
                        encoding="utf-8")
        result = subprocess.run(
            [str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={Path(tmp) / 'profile'}",
             "--virtual-time-budget=20000", "--dump-dom", page.as_uri()],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
    match = re.search(r'<textarea id="out">(.*?)</textarea>', result.stdout, re.DOTALL)
    if not match:
        sys.exit(f"Renderer produced no output.\n{result.stderr[-2000:]}")
    body = html.unescape(match.group(1))
    if body.startswith(("WARNINGS", "ERROR")):
        sys.exit(f"bpmn-js refused the diagram: {body[:2000]}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--render", action="store_true", help="also render each map to SVG")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    browser = find_browser() if args.render else None
    for m in MAPS:
        xml = build_xml(m)
        (OUT_DIR / f"{m.key}.bpmn").write_text(xml, encoding="utf-8", newline="\n")
        print(f"wrote docs/process/{m.key}.bpmn")
        if browser is not None:
            svg = render(xml, browser)
            (OUT_DIR / f"{m.key}.svg").write_text(svg, encoding="utf-8", newline="\n")
            print(f"wrote docs/process/{m.key}.svg ({len(svg):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
