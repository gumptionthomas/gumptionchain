from typing import Any

import pytest

from cancelchain.application import close_clients


class _RecordingClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _RaisingClient:
    def __init__(self) -> None:
        self.attempted = False

    def close(self) -> None:
        self.attempted = True
        msg = 'boom'
        raise RuntimeError(msg)


def test_close_clients_closes_each() -> None:
    a, b = _RecordingClient(), _RecordingClient()
    close_clients({'peer-a': a, 'peer-b': b})  # type: ignore[arg-type]
    assert a.closed is True
    assert b.closed is True


def test_close_clients_swallows_errors_and_continues() -> None:
    """A bad client raising on close() must not stop the others.

    Logging is not guaranteed at process-exit / weakref-finalizer time,
    so the helper swallows errors rather than propagating or logging.
    """
    bad = _RaisingClient()
    good = _RecordingClient()
    clients: dict[str, Any] = {'bad': bad, 'good': good}
    close_clients(clients)
    assert bad.attempted is True
    assert good.closed is True


def test_close_clients_on_empty_dict() -> None:
    close_clients({})  # no-op, must not raise


def test_init_app_registers_finalizer(app: Any) -> None:
    """The init_app path registers a weakref.finalize for app.clients.

    Smoke test: the app fixture builds an app via create_app, which
    calls init_app, which schedules a finalizer. Calling
    close_clients(app.clients) directly closes the pooled clients;
    subsequent calls are no-ops thanks to httpx.Client.close()'s
    idempotency. We assert that pooled clients exist and that closing
    them sets is_closed.
    """
    if not app.clients:
        pytest.skip('app fixture has no peer clients configured')
    sample = next(iter(app.clients.values()))
    assert sample._client.is_closed is False
    close_clients(app.clients)
    assert sample._client.is_closed is True
