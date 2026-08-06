"""Tests for the Father API client (GOAL.md §20).

Every test uses a fake transport so the suite runs without Father
(§29). The fake records every call and lets the test decide what
to return, so we can assert both the request shape and the
idempotency behaviour without a network.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from dpmtf_lightworker import FatherClient


class FakeTransport:
    """A test transport that records calls and returns canned responses."""

    def __init__(self, response: Any = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._response = response

    def __call__(self, method: str, path: str, body: Any) -> Any:
        self.calls.append((method, path, body))
        return self._response


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport(response={"ok": True})


def _client(transport: Callable[..., Any]) -> FatherClient:
    return FatherClient(
        base_url="http://main5090:9130",
        worker_id="svend3060",
        transport=transport,
    )


def test_register_calls_register_endpoint(
    transport: FakeTransport,
) -> None:
    client = _client(transport)
    client.register(capabilities={"gpu": "rtx3060"})
    assert len(transport.calls) == 1
    method, path, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/lightworkers/register"
    assert body["worker_id"] == "svend3060"
    assert body["capabilities"] == {"gpu": "rtx3060"}


def test_heartbeat_calls_heartbeat_endpoint(
    transport: FakeTransport,
) -> None:
    client = _client(transport)
    client.heartbeat()
    method, path, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/lightworkers/heartbeat"
    assert body == {"worker_id": "svend3060"}


def test_next_execution_calls_next_endpoint(
    transport: FakeTransport,
) -> None:
    client = _client(transport)
    client.next_execution()
    method, path, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/lightworkers/svend3060/executions/next"
    assert body is None


def test_claim_calls_claim_endpoint(transport: FakeTransport) -> None:
    client = _client(transport)
    client.claim("EXEC-123-IMPLE01")
    method, path, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/lightworkers/executions/EXEC-123-IMPLE01/claim"
    assert body == {"worker_id": "svend3060"}


def test_execution_heartbeat_includes_attempt_id(
    transport: FakeTransport,
) -> None:
    client = _client(transport)
    client.execution_heartbeat("EXEC-123-IMPLE01", attempt_id="ATTEMPT-1")
    method, path, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/lightworkers/executions/EXEC-123-IMPLE01/heartbeat"
    assert body == {"worker_id": "svend3060", "attempt_id": "ATTEMPT-1"}


def test_report_event_carries_event_payload(
    transport: FakeTransport,
) -> None:
    client = _client(transport)
    client.report_event(
        "EXEC-123-IMPLE01",
        event={"type": "ROLE_RUNNING", "timestamp": 1700000000.0},
    )
    method, path, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/lightworkers/executions/EXEC-123-IMPLE01/events"
    assert body["worker_id"] == "svend3060"
    assert body["event"] == {
        "type": "ROLE_RUNNING",
        "timestamp": 1700000000.0,
    }


def test_complete_calls_complete_endpoint(
    transport: FakeTransport,
) -> None:
    client = _client(transport)
    client.complete(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        result={"status": "ok"},
    )
    method, path, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/lightworkers/executions/EXEC-123-IMPLE01/complete"
    assert body == {
        "worker_id": "svend3060",
        "attempt_id": "ATTEMPT-1",
        "result": {"status": "ok"},
    }


def test_fail_calls_fail_endpoint(transport: FakeTransport) -> None:
    client = _client(transport)
    client.fail(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        failure={"category": "INTERNAL_WORKER_ERROR"},
    )
    method, path, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/lightworkers/executions/EXEC-123-IMPLE01/fail"
    assert body == {
        "worker_id": "svend3060",
        "attempt_id": "ATTEMPT-1",
        "failure": {"category": "INTERNAL_WORKER_ERROR"},
    }


def test_complete_is_idempotent_per_attempt(
    transport: FakeTransport,
) -> None:
    """§20 idempotent completion: one request per (execution_id, attempt_id)."""
    client = _client(transport)
    transport._response = {"first": True}
    first = client.complete(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        result={"status": "ok"},
    )
    second = client.complete(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        result={"status": "ok"},
    )
    assert first == {"first": True}
    assert second == {"first": True}
    assert len(transport.calls) == 1
    assert client.completed_cache_size == 1


def test_complete_different_attempts_each_call(
    transport: FakeTransport,
) -> None:
    """A different ``attempt_id`` is a separate call."""
    client = _client(transport)
    client.complete(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        result={"status": "ok"},
    )
    client.complete(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-2",
        result={"status": "ok"},
    )
    assert len(transport.calls) == 2
    assert client.completed_cache_size == 2


def test_complete_different_executions_each_call(
    transport: FakeTransport,
) -> None:
    """A different ``execution_id`` is a separate call."""
    client = _client(transport)
    client.complete(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        result={"status": "ok"},
    )
    client.complete(
        "EXEC-456-IMPLE01",
        attempt_id="ATTEMPT-1",
        result={"status": "ok"},
    )
    assert len(transport.calls) == 2
    assert client.completed_cache_size == 2


def test_fail_is_idempotent_per_attempt(
    transport: FakeTransport,
) -> None:
    """§20 idempotent failure reporting: one request per (execution_id, attempt_id)."""
    client = _client(transport)
    transport._response = {"first_failure": True}
    first = client.fail(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        failure={"category": "INTERNAL_WORKER_ERROR"},
    )
    second = client.fail(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        failure={"category": "INTERNAL_WORKER_ERROR"},
    )
    assert first == {"first_failure": True}
    assert second == {"first_failure": True}
    assert len(transport.calls) == 1
    assert client.failed_cache_size == 1


def test_fail_different_attempts_each_call(
    transport: FakeTransport,
) -> None:
    """A different ``attempt_id`` is a separate fail call."""
    client = _client(transport)
    client.fail(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        failure={"category": "INTERNAL_WORKER_ERROR"},
    )
    client.fail(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-2",
        failure={"category": "INTERNAL_WORKER_ERROR"},
    )
    assert len(transport.calls) == 2


def test_claim_is_idempotent_per_execution(
    transport: FakeTransport,
) -> None:
    """§20 duplicate-execution protection: one claim request per execution_id."""
    client = _client(transport)
    transport._response = {"claimed": True}
    first = client.claim("EXEC-123-IMPLE01")
    second = client.claim("EXEC-123-IMPLE01")
    assert first == {"claimed": True}
    assert second == {"claimed": True}
    assert len(transport.calls) == 1
    assert client.claimed_cache_size == 1


def test_claim_different_executions_each_call(
    transport: FakeTransport,
) -> None:
    """A different ``execution_id`` is a separate claim."""
    client = _client(transport)
    client.claim("EXEC-123-IMPLE01")
    client.claim("EXEC-456-IMPLE01")
    assert len(transport.calls) == 2
    assert client.claimed_cache_size == 2


def test_complete_idempotency_does_not_affect_fail(
    transport: FakeTransport,
) -> None:
    """The complete and fail caches are independent."""
    client = _client(transport)
    client.complete(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        result={"status": "ok"},
    )
    client.fail(
        "EXEC-123-IMPLE01",
        attempt_id="ATTEMPT-1",
        failure={"category": "INTERNAL_WORKER_ERROR"},
    )
    assert len(transport.calls) == 2


def test_default_transport_is_lazy() -> None:
    """The default transport is constructed only when ``transport=None``."""
    client = FatherClient(
        base_url="http://main5090:9130",
        worker_id="svend3060",
    )
    # No network call has been made yet. We can only verify that the
    # client is constructed without raising; if the default transport
    # had connected at construction time this test would block or
    # fail in a sandbox without main5090.
    assert client._worker_id == "svend3060"


def test_empty_base_url_rejected() -> None:
    """An empty base_url is rejected at construction time."""
    with pytest.raises(ValueError):
        FatherClient(base_url="", worker_id="svend3060")


def test_empty_worker_id_rejected() -> None:
    """An empty worker_id is rejected at construction time."""
    with pytest.raises(ValueError):
        FatherClient(base_url="http://main5090:9130", worker_id="")


def test_trailing_slash_on_base_url_normalized(
    transport: FakeTransport,
) -> None:
    """A trailing slash on ``base_url`` does not produce a doubled slash."""
    client = FatherClient(
        base_url="http://main5090:9130/",
        worker_id="svend3060",
        transport=transport,
    )
    client.heartbeat()
    # The transport sees the path (no URL), so this only asserts the
    # construction did not raise. The path assertion in the other
    # tests confirms the path component starts with a single slash.
    assert transport.calls[0][1] == "/api/lightworkers/heartbeat"
