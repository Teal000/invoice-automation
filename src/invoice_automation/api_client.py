"""A small typed client for the API.

The review interface talks to the service over HTTP rather than reaching into
the database. That is slower and it is the right choice: the reviewer screen
then exercises the same endpoints an orchestrator or any other caller uses, so
a gap in the API shows up here instead of being quietly worked around.

The client holds no business logic. It sends requests, raises a useful error
when the service refuses, and returns what came back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

API_KEY_HEADER = "X-API-Key"
ACTOR_HEADER = "X-Actor"


class ApiError(RuntimeError):
    """The service answered with an error.

    Carries the service's own error code so a caller can act on it — a
    revision conflict needs a different response from a missing invoice — and
    the status, so the two cannot be confused.
    """

    def __init__(self, status_code: int, code: str, message: str, detail: Any = None):
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail

    @property
    def is_conflict(self) -> bool:
        return self.status_code == 409

    @property
    def is_unreachable(self) -> bool:
        return self.status_code == 0


class ApiUnavailable(ApiError):
    """The service could not be reached at all.

    A transport failure is raised as the same type as a refusal, so a caller
    handles one error and cannot forget the case where the service is simply
    not running. That case is the most likely one in a local setup and the
    easiest to leave as an unhandled traceback in front of a user.
    """

    def __init__(self, message: str):
        super().__init__(0, "unreachable", message)


@dataclass
class ApiClient:
    """Thin wrapper over an ``httpx.Client``.

    The HTTP client is injected rather than built here, so the tests can pass
    FastAPI's ``TestClient`` — itself an ``httpx.Client`` — and exercise the
    real endpoints without a running server.
    """

    http: httpx.Client
    api_key: str | None = None
    actor: str | None = None

    @classmethod
    def for_base_url(
        cls, base_url: str, api_key: str, *, actor: str | None = None, timeout: float = 30.0
    ) -> "ApiClient":
        return cls(
            http=httpx.Client(base_url=base_url, timeout=timeout),
            api_key=api_key,
            actor=actor,
        )

    # -- plumbing ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers[API_KEY_HEADER] = self.api_key
        if self.actor:
            headers[ACTOR_HEADER] = self.actor
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        try:
            response = self.http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            # The service is not running, or the address is wrong. Raised as
            # an ApiError so every caller handles it the same way.
            raise ApiUnavailable(f"could not reach the API: {exc}") from exc
        if response.status_code >= 400:
            raise _as_error(response)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # -- reads -------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            response = self.http.get("/health")
        except httpx.HTTPError as exc:
            raise ApiUnavailable(f"could not reach the API: {exc}") from exc
        return response.json()

    def invoices(
        self,
        *,
        state: str | None = None,
        exception_code: str | None = None,
        supplier_reference: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        if state:
            params["state"] = state
        if exception_code:
            params["exception_code"] = exception_code
        if supplier_reference:
            params["supplier_reference"] = supplier_reference
        return self._request("GET", "/invoices", params=params)

    def invoice(self, invoice_id: UUID | str) -> dict[str, Any]:
        return self._request("GET", f"/invoices/{invoice_id}")

    def summary(self) -> dict[str, Any]:
        return self._request("GET", "/reports/summary")

    def notifications(self, *, status: str | None = None) -> dict[str, Any]:
        params = {"status": status} if status else {}
        return self._request("GET", "/notifications", params=params)

    # -- reviewer actions --------------------------------------------------

    def correct(
        self,
        invoice_id: UUID | str,
        *,
        expected_revision_number: int,
        invoice: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/invoices/{invoice_id}/revisions",
            json={
                "expected_revision_number": expected_revision_number,
                "reason": reason,
                "invoice": invoice,
            },
        )

    def review_action(
        self,
        invoice_id: UUID | str,
        *,
        action: str,
        expected_revision_number: int,
        reason: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/invoices/{invoice_id}/review-actions",
            json={
                "action": action,
                "expected_revision_number": expected_revision_number,
                "reason": reason,
            },
        )

    # -- processing --------------------------------------------------------

    def upload(
        self,
        *,
        file_name: str,
        content: bytes,
        media_type: str,
        process: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self._request(
            "POST",
            "/submissions",
            files={"file": (file_name, content, media_type)},
            params={"process": str(process).lower()},
            headers=headers,
        )

    def process(self, submission_id: UUID | str) -> dict[str, Any]:
        return self._request("POST", f"/submissions/{submission_id}/process")

    def submission(self, submission_id: UUID | str) -> dict[str, Any]:
        return self._request("GET", f"/submissions/{submission_id}")

    def recover(self, *, limit: int = 20) -> dict[str, Any]:
        return self._request("POST", "/jobs/recover", params={"limit": limit})


def _as_error(response: httpx.Response) -> ApiError:
    """Turn the service's error envelope into an exception.

    Falls back gracefully: a proxy or a crash can produce a non-JSON body, and
    the client should still raise something that says what happened rather
    than a decoding error that hides it.
    """
    try:
        body = response.json()
        error = body.get("error", {})
        return ApiError(
            response.status_code,
            error.get("code", "unknown"),
            error.get("message", response.text[:200]),
            error.get("detail"),
        )
    except Exception:  # noqa: BLE001 - any non-JSON body lands here
        return ApiError(response.status_code, "unknown", response.text[:200])
