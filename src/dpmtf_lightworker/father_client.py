"""Father API client (GOAL.md §20).

A LightWorker talks to Father over a small set of HTTP endpoints.
This module wraps each endpoint as a method. The HTTP call is
injected as a ``transport`` callable so the default test suite
runs without Father (§29): tests construct a fake transport that
records what was called and returns whatever the test wants.

The contract for ``transport``:

    transport(method: str, path: str, body: Any) -> Any

    method is the HTTP verb ("GET", "POST", ...).
    path is the path component, beginning with "/".
    body is the JSON-encodable payload, or None for GETs.
    The return value is the decoded response.

§20 lists nine required protocol properties. **Two are behaviour in
this module**, not prose in a docstring:

* Idempotent completion and failure. Calling ``complete`` or
  ``fail`` twice for the same ``(execution_id, attempt_id)`` must
  issue exactly one request. The second call returns the cached
  response from the first.
* Duplicate-execution protection. ``claim`` for an
  ``execution_id`` this client has already claimed must not issue
  a second claim; it returns the cached response.

The remaining seven properties are documented below as
**not implemented here**: worker identity, capabilities, static
matching, atomic claim, attempt-bound heartbeat, cancellation.
A worker cannot make a claim atomic — that is the server's
guarantee — and a client method that says otherwise is a false
claim about the system.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

Transport = Callable[[str, str, Any], Any]


_DEFAULT_TIMEOUT_SECONDS: float = 30.0


class FatherClientError(Exception):
    """Raised when the HTTP transport fails.

    The default transport raises this; tests with a fake transport
    can return whatever they want.

    ``status`` carries the HTTP status when Father answered, and is ``None``
    when it did not answer at all. `urllib`'s HTTPError is a subclass of
    URLError, so without this a 422 refusal and an unreachable host arrived
    as the same exception with the same shape.

    The worker needs the difference. A refused result is Father working
    correctly and saying no; an unreachable Father is Father being absent.
    Treating the first as the second made the worker report a failure on an
    execution Father had already closed, which came back 500.
    """

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def refused(self) -> bool:
        """True when Father answered and declined. 4xx, not 5xx.

        A 5xx is Father broken, which is worth reporting as a failure. A 4xx
        is Father's considered answer, and reporting a failure against it is
        arguing with a verdict.
        """
        return self.status is not None and 400 <= self.status < 500


class FatherClient:
    """A client for the eight Father endpoints in §20.

    The constructor accepts a ``transport`` callable so tests can
    substitute a fake. The default transport uses the standard
    library ``urllib`` and is built lazily — nothing connects at
    import time, nothing connects at construction time, the request
    only fires when a method is called.
    """

    def __init__(
        self,
        *,
        base_url: str,
        worker_id: str,
        transport: Optional[Transport] = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        auth_token: Optional[str] = None,
    ) -> None:
        if not base_url:
            raise ValueError("FatherClient.base_url must not be empty")
        if not worker_id:
            raise ValueError("FatherClient.worker_id must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("FatherClient.timeout_seconds must be positive")

        self._base_url = base_url.rstrip("/")
        self._worker_id = worker_id
        self._timeout_seconds = timeout_seconds
        if transport is None:
            self._transport: Transport = _make_default_transport(  # noqa: E501
                base_url=self._base_url,
                timeout_seconds=self._timeout_seconds,
                auth_token=auth_token or os.environ.get("LIGHTWORKER_AUTH_TOKEN"),
            )
        else:
            self._transport = transport

        # Idempotency caches keyed by (execution_id, attempt_id).
        # The second call returns the cached response without
        # invoking the transport (§20 idempotent completion /
        # failure reporting).
        self._completed: dict[tuple[str, str], Any] = {}
        self._failed: dict[tuple[str, str], Any] = {}

        # Duplicate-execution protection for claim: the second
        # claim call for the same execution_id returns the cached
        # response without invoking the transport.
        self._claimed: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # §20 properties — two are behaviour here, seven are NOT.
    # ------------------------------------------------------------------
    #
    # IMPLEMENTED in this module:
    #   * idempotent completion          — see ``complete``.
    #   * idempotent failure reporting   — see ``fail``.
    #   * duplicate-execution protection — see ``claim``.
    #
    # NOT IMPLEMENTED here (Father-side or later runs):
    #   * worker identity                — passed in at construction.
    #   * worker capabilities            — passed in ``register``.
    #   * static worker matching         — Father routes by
    #                                       ``execution_target``.
    #   * atomic role-execution claim    — server's guarantee; the
    #                                       client cannot enforce it.
    #   * attempt-bound heartbeat        — see ``execution_heartbeat``
    #                                       (sends attempt_id; the
    #                                       server enforces the
    #                                       binding).
    #   * cancellation                   — out of scope; not in the
    #                                       eight endpoints of §20.

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def register(self, *, capabilities: Mapping[str, Any]) -> Any:
        """POST /api/lightworkers/register.

        Worker identity (``worker_id``) is carried on the client
        instance; ``capabilities`` is what this worker advertises
        to Father (GPU count, max parallel executions, etc.).
        """
        body = {
            "worker_id": self._worker_id,
            "capabilities": dict(capabilities),
        }
        return self._transport("POST", "/api/lightworkers/register", body)

    def heartbeat(self) -> Any:
        """POST /api/lightworkers/heartbeat."""
        body = {"worker_id": self._worker_id}
        return self._transport("POST", "/api/lightworkers/heartbeat", body)

    def next_execution(self) -> Any:
        """GET /api/lightworkers/{worker_id}/executions/next."""
        path = f"/api/lightworkers/{self._worker_id}/executions/next"
        return self._transport("GET", path, None)

    def claim(self, execution_id: str) -> Any:
        """POST /api/lightworkers/executions/{execution_id}/claim.

        Duplicate-execution protection (§20): the second call for
        the same ``execution_id`` returns the cached response from
        the first call without invoking the transport.
        """
        if execution_id in self._claimed:
            return self._claimed[execution_id]
        path = f"/api/lightworkers/executions/{execution_id}/claim"
        body = {"worker_id": self._worker_id}
        response = self._transport("POST", path, body)
        self._claimed[execution_id] = response
        return response

    def upload_artifact(self, data: bytes) -> str:
        """POST /api/lightworkers/artifacts; returns the content's sha256.

        Content-addressed: the server verifies the declared hash against
        the decoded bytes and stores under that name, so a re-upload of
        identical bytes is a no-op and the returned sha IS the reference a
        result carries instead of inline content.
        """
        import base64
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        body = {
            "worker_id": self._worker_id,
            "sha256": sha,
            "content_b64": base64.b64encode(data).decode("ascii"),
        }
        self._transport("POST", "/api/lightworkers/artifacts", body)
        return sha

    def execution_heartbeat(self, execution_id: str, *, attempt_id: str) -> Any:
        """POST /api/lightworkers/executions/{execution_id}/heartbeat.

        The attempt-bound aspect of §20 is enforced by the server:
        the heartbeat carries ``attempt_id`` and the server rejects
        stale attempts. The client only carries the value.
        """
        path = f"/api/lightworkers/executions/{execution_id}/heartbeat"
        body = {"worker_id": self._worker_id, "attempt_id": attempt_id}
        return self._transport("POST", path, body)

    def report_event(
        self, execution_id: str, *, event: Mapping[str, Any]
    ) -> Any:
        """POST /api/lightworkers/executions/{execution_id}/events."""
        path = f"/api/lightworkers/executions/{execution_id}/events"
        body = {"worker_id": self._worker_id, "event": dict(event)}
        return self._transport("POST", path, body)

    def complete(
        self,
        execution_id: str,
        *,
        attempt_id: str,
        result: Mapping[str, Any],
    ) -> Any:
        """POST /api/lightworkers/executions/{execution_id}/complete.

        Idempotent (§20): the second call for the same
        ``(execution_id, attempt_id)`` returns the cached response
        from the first call without invoking the transport.
        """
        key = (execution_id, attempt_id)
        if key in self._completed:
            return self._completed[key]
        path = f"/api/lightworkers/executions/{execution_id}/complete"
        body = {
            "worker_id": self._worker_id,
            "attempt_id": attempt_id,
            "result": dict(result),
        }
        response = self._transport("POST", path, body)
        self._completed[key] = response
        return response

    def fail(
        self,
        execution_id: str,
        *,
        attempt_id: str,
        failure: Mapping[str, Any],
    ) -> Any:
        """POST /api/lightworkers/executions/{execution_id}/fail.

        Idempotent (§20): the second call for the same
        ``(execution_id, attempt_id)`` returns the cached response
        from the first call without invoking the transport.
        """
        key = (execution_id, attempt_id)
        if key in self._failed:
            return self._failed[key]
        path = f"/api/lightworkers/executions/{execution_id}/fail"
        body = {
            "worker_id": self._worker_id,
            "attempt_id": attempt_id,
            "failure": dict(failure),
        }
        response = self._transport("POST", path, body)
        self._failed[key] = response
        return response

    # ------------------------------------------------------------------
    # Introspection (testing)
    # ------------------------------------------------------------------

    @property
    def completed_cache_size(self) -> int:
        """Number of ``(execution_id, attempt_id)`` pairs cached by ``complete``.

        Exposed for tests; not part of §20.
        """
        return len(self._completed)

    @property
    def failed_cache_size(self) -> int:
        """Number of ``(execution_id, attempt_id)`` pairs cached by ``fail``.

        Exposed for tests; not part of §20.
        """
        return len(self._failed)

    @property
    def claimed_cache_size(self) -> int:
        """Number of ``execution_id`` values cached by ``claim``.

        Exposed for tests; not part of §20.
        """
        return len(self._claimed)


# ---------------------------------------------------------------------------
# Default HTTP transport
# ---------------------------------------------------------------------------


def _make_default_transport(
    *, base_url: str, timeout_seconds: float, auth_token: Optional[str] = None
) -> Transport:
    """Build the default urllib-backed transport.

    The returned callable closes over ``base_url``, ``timeout_seconds``
    and the auth token. Construction does not connect — the request
    fires only when the returned callable is invoked.

    §27 requires the worker's identity to be authenticated and says
    Tailscale membership must not be the sole authorization mechanism.
    The token goes in the ``Authorization`` header rather than the body:
    request bodies are logged and echoed in error messages, headers far
    less often, and ``Bearer`` is where a reader looks for a credential.
    """

    def transport(method: str, path: str, body: Any) -> Any:
        url = base_url + path
        data: Optional[bytes]
        headers: dict[str, str]
        if body is None:
            data = None
            headers = {}
        else:
            data = json.dumps(body).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        request = urllib_request.Request(
            url=url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = urllib_request.urlopen(
                request, timeout=timeout_seconds
            )
        except urllib_error.HTTPError as exc:
            # Checked before URLError: HTTPError is a subclass of it, so the
            # broad clause below would otherwise swallow every status code.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001 - best effort, never fatal
                pass
            raise FatherClientError(
                f"Father request {method} {url} refused with "
                f"{exc.code}: {detail or exc}",
                status=exc.code,
            ) from exc
        except urllib_error.URLError as exc:
            raise FatherClientError(
                f"Father request {method} {url} failed: {exc}"
            ) from exc
        raw = response.read()
        if not raw:
            return None
        text = raw.decode("utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    return transport


__all__ = [
    "FatherClient",
    "FatherClientError",
    "Transport",
]
