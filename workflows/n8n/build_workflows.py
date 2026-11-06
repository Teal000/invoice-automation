"""Generate the n8n workflow exports.

    .venv/Scripts/python.exe workflows/n8n/build_workflows.py

Each workflow carries a fixed id, so re-importing updates it in place rather
than leaving another copy behind: importing three times without one left nine
workflows in n8n, which is its own small lesson about idempotency.

Written as a generator rather than by hand so the node type versions live in
one place. They are not guessed: they were read from the running n8n instance
(`n8n-nodes-base/dist/types/nodes.json` inside the container) for the version
pinned in compose.yaml. A workflow carrying a type version the installed n8n
does not have is imported but behaves oddly, which is worse than failing.

Two things the workflows deliberately do **not** do:

* They do not check whether a submission has already been processed before
  calling the API. The API is authoritative and already refuses to process a
  settled submission, returning what was recorded. Re-implementing that check
  in the orchestrator would create a second opinion about the same question,
  and the orchestrator would be the one that is wrong after a race.
* They carry no secret. The API key lives in an n8n credential, referenced
  here by name only, and that credential is seeded inside the container from
  its own environment. The first version read the key from `$env` in an
  expression; n8n blocks that by default, and unblocking it would have
  weakened the sandbox for every workflow, so the credential is the right
  mechanism rather than a workaround.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Read from the running n8n 2.46.6, not from memory.
WEBHOOK = 2.2
HTTP_REQUEST = 4.5
IF_NODE = 2.4
SCHEDULE = 1.4
NO_OP = 1

#: The API inside the compose network, by service name. Hardcoded rather than
#: read from the environment: n8n blocks expression access to env vars by
#: default, and the first run of these workflows failed with "access to env
#: vars denied". Turning that block off would weaken n8n's sandbox for every
#: workflow, so the base URL is written here and the key lives in a
#: credential instead.
API = "http://api:8000"

#: The credential the HTTP nodes authenticate with. Referenced by id and name
#: only: the value lives in n8n, seeded from the container's own environment,
#: and never appears in these exports.
CREDENTIAL = {
    "httpHeaderAuth": {"id": "invoiceApiKey", "name": "Invoice API key"}
}


def http_node(
    name: str,
    *,
    method: str,
    url: str,
    position: list[int],
    query: dict | None = None,
    retry: bool = False,
) -> dict:
    # A parameter containing an expression must start with "=" or n8n sends
    # the braces through as literal text. Without it the first live run put
    # "{{ $json.body.submission_id }}" in the URL path and the API answered
    # 422 uuid_parsing, which is exactly the sort of thing only a real
    # execution shows.
    if "{{" in url and not url.startswith("="):
        url = "=" + url

    parameters: dict = {
        "method": method,
        "url": url,
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {},
    }
    if query:
        parameters["sendQuery"] = True
        parameters["queryParameters"] = {
            "parameters": [{"name": k, "value": v} for k, v in query.items()]
        }
    node = {
        "parameters": parameters,
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": HTTP_REQUEST,
        "position": position,
        "credentials": CREDENTIAL,
    }
    if retry:
        # Bounded retries, handled by the node. The API distinguishes a
        # technical failure (500, retryable) from a business outcome, so a
        # retry here only ever repeats something that genuinely failed.
        node["retryOnFail"] = True
        node["maxTries"] = 3
        node["waitBetweenTries"] = 5000
        # A failure that outlasts the retries must not stop the workflow
        # silently: it continues to the error branch and is visible.
        node["onError"] = "continueErrorOutput"
    return node


def if_node(name: str, *, left: str, equals: str, position: list[int]) -> dict:
    return {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "loose",
                    "version": 2,
                },
                "conditions": [
                    {
                        "id": f"{name.lower().replace(' ', '-')}-cond",
                        "leftValue": left,
                        "rightValue": equals,
                        "operator": {"type": "string", "operation": "equals"},
                    }
                ],
                "combinator": "and",
            },
            "looseTypeValidation": True,
            "options": {},
        },
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": "n8n-nodes-base.if",
        "typeVersion": IF_NODE,
        "position": position,
    }


def noop(name: str, position: list[int]) -> dict:
    return {
        "parameters": {},
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": "n8n-nodes-base.noOp",
        "typeVersion": NO_OP,
        "position": position,
    }


def connect(source: str, target: str, *, output: int = 0) -> dict:
    return {source: {"main": [[] for _ in range(output)] + [[{"node": target, "type": "main", "index": 0}]]}}


def merge_connections(*parts: dict) -> dict:
    merged: dict = {}
    for part in parts:
        for node, spec in part.items():
            if node not in merged:
                merged[node] = spec
                continue
            existing = merged[node]["main"]
            incoming = spec["main"]
            while len(existing) < len(incoming):
                existing.append([])
            for index, outputs in enumerate(incoming):
                existing[index].extend(outputs)
    return merged


def process_submission() -> dict:
    """Webhook in, processed invoice out, routed on the outcome."""
    nodes = [
        {
            "parameters": {
                "httpMethod": "POST",
                "path": "invoice-submission",
                "responseMode": "lastNode",
                "options": {},
            },
            "id": "submission-arrived",
            "name": "Submission arrived",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": WEBHOOK,
            "position": [-220, 0],
            "webhookId": "7b1f1a6e-0000-4000-8000-000000000001",
        },
        http_node(
            "Process the submission",
            method="POST",
            url=f"{API}/submissions/{{{{ $json.body.submission_id }}}}/process",
            position=[0, 0],
            retry=True,
        ),
        if_node(
            "Needs review?",
            left="={{ $json.state }}",
            equals="needs_review",
            position=[220, -40],
        ),
        noop("Review queue notified by the outbox", [440, -120]),
        noop("No action needed", [440, 40]),
        noop("Processing failed after retries", [220, 160]),
    ]
    connections = merge_connections(
        connect("Submission arrived", "Process the submission"),
        connect("Process the submission", "Needs review?"),
        connect("Process the submission", "Processing failed after retries", output=1),
        connect("Needs review?", "Review queue notified by the outbox"),
        connect("Needs review?", "No action needed", output=1),
    )
    return {
        "id": "invoiceProcessSubmission",
        "name": "Process a submission",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
    }


def recover_stalled_work() -> dict:
    """Scheduled sweep: finish work that stalled."""
    nodes = [
        {
            "parameters": {
                "rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}
            },
            "id": "every-fifteen-minutes",
            "name": "Every fifteen minutes",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": SCHEDULE,
            "position": [-220, 0],
        },
        http_node(
            "Recover stalled jobs",
            method="POST",
            url=f"{API}/jobs/recover",
            position=[0, 0],
            query={"limit": "20"},
            retry=True,
        ),
        if_node(
            "Anything still failing?",
            left="={{ $json.still_failing > 0 ? 'yes' : 'no' }}",
            equals="yes",
            position=[220, 0],
        ),
        noop("Needs a person", [440, -80]),
        noop("Nothing outstanding", [440, 80]),
        noop("Sweep itself failed", [220, 180]),
    ]
    connections = merge_connections(
        connect("Every fifteen minutes", "Recover stalled jobs"),
        connect("Recover stalled jobs", "Anything still failing?"),
        connect("Recover stalled jobs", "Sweep itself failed", output=1),
        connect("Anything still failing?", "Needs a person"),
        connect("Anything still failing?", "Nothing outstanding", output=1),
    )
    return {
        "id": "invoiceRecoverStalled",
        "name": "Recover stalled work",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
    }


def pending_notifications() -> dict:
    """Scheduled read of the outbox.

    It stops at reading. Nothing in this project delivers a notification, and
    a workflow that marked them sent without sending them would put a lie in
    the database. When a mail or chat node is added, the next step is
    POST /notifications/{id}/sent with that channel named.
    """
    nodes = [
        {
            "parameters": {
                "rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}
            },
            "id": "every-five-minutes",
            "name": "Every five minutes",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": SCHEDULE,
            "position": [-220, 0],
        },
        http_node(
            "Read pending notifications",
            method="GET",
            url=f"{API}/notifications",
            position=[0, 0],
            query={"status": "pending"},
            retry=True,
        ),
        if_node(
            "Any waiting?",
            left="={{ $json.count > 0 ? 'yes' : 'no' }}",
            equals="yes",
            position=[220, 0],
        ),
        noop("Would be delivered here (not implemented)", [440, -80]),
        noop("Outbox empty", [440, 80]),
    ]
    connections = merge_connections(
        connect("Every five minutes", "Read pending notifications"),
        connect("Read pending notifications", "Any waiting?"),
        connect("Any waiting?", "Would be delivered here (not implemented)"),
        connect("Any waiting?", "Outbox empty", output=1),
    )
    return {
        "id": "invoicePendingNotifications",
        "name": "Read pending notifications",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
    }


def main() -> None:
    for file_name, workflow in (
        ("process_submission.json", process_submission()),
        ("recover_stalled_work.json", recover_stalled_work()),
        ("pending_notifications.json", pending_notifications()),
    ):
        path = HERE / file_name
        path.write_text(
            json.dumps(workflow, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"wrote {path.name}: {len(workflow['nodes'])} nodes")


if __name__ == "__main__":
    main()
