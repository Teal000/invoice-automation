"""The process maps in docs/process are generated; these keep them honest.

A process map is easy to leave behind when the process changes, and a stale
one is worse than none because it is believed. So the committed .bpmn must be
exactly what `tools/build_process_maps.py` produces today, every element in it
must be drawn, and each SVG must show every element of the .bpmn beside it.
None of this needs a browser; only producing the SVGs does.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROCESS = ROOT / "docs" / "process"
NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
FLOW_NODES = ("startEvent", "endEvent", "boundaryEvent", "manualTask", "serviceTask",
              "userTask", "task", "exclusiveGateway")


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_process_maps", ROOT / "tools" / "build_process_maps.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered first: @dataclass resolves its module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()
KEYS = [m.key for m in BUILDER.MAPS]


def _tree(key: str) -> ET.Element:
    return ET.parse(PROCESS / f"{key}.bpmn").getroot()


def _nodes(root: ET.Element) -> dict[str, ET.Element]:
    found = {}
    for tag in FLOW_NODES:
        for el in root.iter(f"{{{NS['bpmn']}}}{tag}"):
            found[el.get("id")] = el
    return found


def _flows(root: ET.Element) -> list[ET.Element]:
    return list(root.iter(f"{{{NS['bpmn']}}}sequenceFlow"))


@pytest.mark.parametrize("key", KEYS)
def test_the_committed_map_is_what_the_spec_produces(key: str):
    """Edit the spec in tools/build_process_maps.py, never the .bpmn by hand."""
    m = next(m for m in BUILDER.MAPS if m.key == key)
    committed = (PROCESS / f"{key}.bpmn").read_text(encoding="utf-8")
    assert committed == BUILDER.build_xml(m), (
        f"docs/process/{key}.bpmn is stale: run tools/build_process_maps.py --render")


@pytest.mark.parametrize("key", KEYS)
def test_every_flow_joins_two_existing_steps(key: str):
    root = _tree(key)
    nodes = _nodes(root)
    for flow in _flows(root):
        assert flow.get("sourceRef") in nodes, flow.get("id")
        assert flow.get("targetRef") in nodes, flow.get("id")


@pytest.mark.parametrize("key", KEYS)
def test_nothing_is_stranded(key: str):
    """Every step can be reached and every step leads somewhere, except where a
    process genuinely starts or ends. A boundary event starts its own path."""
    root = _tree(key)
    for node_id, el in _nodes(root).items():
        tag = el.tag.split("}")[1]
        has_in = el.find("bpmn:incoming", NS) is not None
        has_out = el.find("bpmn:outgoing", NS) is not None
        if tag not in ("startEvent", "boundaryEvent"):
            assert has_in, f"{node_id} cannot be reached"
        if tag != "endEvent":
            assert has_out, f"{node_id} leads nowhere"


@pytest.mark.parametrize("key", KEYS)
def test_every_branch_out_of_a_decision_is_labelled(key: str):
    root = _tree(key)
    flows = {f.get("id"): f for f in _flows(root)}
    for gateway in root.iter(f"{{{NS['bpmn']}}}exclusiveGateway"):
        out = [o.text for o in gateway.findall("bpmn:outgoing", NS)]
        if len(out) > 1:
            assert all(flows[f].get("name") for f in out), gateway.get("id")


@pytest.mark.parametrize("key", KEYS)
def test_every_step_sits_in_exactly_one_lane(key: str):
    root = _tree(key)
    refs = [r.text for r in root.iter(f"{{{NS['bpmn']}}}flowNodeRef")]
    assert sorted(refs) == sorted(_nodes(root))


@pytest.mark.parametrize("key", KEYS)
def test_every_element_is_drawn(key: str):
    root = _tree(key)
    drawn = {el.get("bpmnElement") for el in root.iter()
             if el.tag in (f"{{{NS['bpmndi']}}}BPMNShape", f"{{{NS['bpmndi']}}}BPMNEdge")}
    wanted = set(_nodes(root)) | {f.get("id") for f in _flows(root)}
    assert wanted <= drawn, sorted(wanted - drawn)


@pytest.mark.parametrize("key", KEYS)
def test_every_connector_runs_in_straight_horizontal_and_vertical_lines(key: str):
    """Sequence flows only; the dotted links to annotations may run diagonally."""
    root = _tree(key)
    flow_ids = {f.get("id") for f in _flows(root)}
    for edge in root.iter(f"{{{NS['bpmndi']}}}BPMNEdge"):
        if edge.get("bpmnElement") not in flow_ids:
            continue
        pts = [(int(w.get("x")), int(w.get("y"))) for w in edge.findall("di:waypoint", NS)]
        assert len(pts) >= 2
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            assert x0 == x1 or y0 == y1, f"{edge.get('bpmnElement')} has a diagonal segment"


@pytest.mark.parametrize("key", KEYS)
def test_the_picture_shows_every_element_of_the_map(key: str):
    """bpmn-js tags each drawn element with its id. An SVG rendered from an older
    .bpmn is missing whatever was added since, and this is where that shows."""
    svg = (PROCESS / f"{key}.svg").read_text(encoding="utf-8")
    shown = set(re.findall(r'data-element-id="([^"]+)"', svg))
    root = _tree(key)
    wanted = set(_nodes(root)) | {f.get("id") for f in _flows(root)}
    assert wanted <= shown, sorted(wanted - shown)


def test_the_to_be_map_extracts_before_it_checks_for_a_duplicate_file():
    """The order the code runs in, not the order a plan might suggest: an
    identical file is extracted and then caught by EXACT_FILE_DUPLICATE with
    the other rules, not stopped on arrival."""
    root = _tree("to_be")
    edges = {(f.get("sourceRef"), f.get("targetRef")) for f in _flows(root)}
    assert ("t_store", "t_extract") in edges
    assert ("t_extract", "t_rules") in edges
    assert ("t_rules", "g_dup") in edges
