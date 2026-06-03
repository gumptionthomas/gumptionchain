import gc
import weakref
from typing import Any

import pytest

from gumptionchain.application import close_clients


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


def test_close_clients_smoke_on_real_app(app: Any) -> None:
    """Smoke: pooled ApiClients in a real app's app.clients all close
    when close_clients is called explicitly.

    Doesn't exercise the finalizer path — see
    test_weakref_finalize_fires_on_gc for that.
    """
    if not app.clients:
        pytest.skip('app fixture has no peer clients configured')
    sample = next(iter(app.clients.values()))
    assert sample._client.is_closed is False
    close_clients(app.clients)
    assert sample._client.is_closed is True


def test_weakref_finalize_fires_on_gc() -> None:
    """The pattern init_app uses — weakref.finalize(app, close_clients,
    app.clients) — runs the cleanup when the last reference to the app
    object drops and the GC reclaims it.

    Uses a minimal stand-in app object (no Flask needed) to keep the
    test independent of create_app's heavier setup. The real init_app
    path is exercised indirectly via the app fixture in
    test_close_clients_smoke_on_real_app.
    """

    class _StandinApp:
        pass

    a, b = _RecordingClient(), _RecordingClient()
    clients: dict[str, Any] = {'peer-a': a, 'peer-b': b}
    app = _StandinApp()
    app.clients = clients  # type: ignore[attr-defined]
    weakref.finalize(app, close_clients, clients)

    app_ref = weakref.ref(app)
    del app
    gc.collect()

    assert app_ref() is None, 'standin app was not garbage-collected'
    assert a.closed is True
    assert b.closed is True
