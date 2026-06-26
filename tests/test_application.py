import gc
import logging
import weakref
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import pytest

from gumptionchain.application import close_clients, read_signing_keys
from gumptionchain.signing_key import SigningKey


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


def _key_app(**config: Any) -> Any:
    return SimpleNamespace(
        config=config, logger=logging.getLogger('test-read-signing-keys')
    )


def test_read_signing_keys_inline_secret_loads_one_key() -> None:
    sk = SigningKey()
    app = _key_app(SIGNING_KEY=sk.secret, SIGNING_KEY_DIR=None)
    keys = read_signing_keys(app)
    assert set(keys) == {sk.address}
    assert keys[sk.address].address == sk.address


def test_read_signing_keys_inline_wins_over_dir() -> None:
    inline = SigningKey()
    dir_key = SigningKey()
    with TemporaryDirectory() as d:
        dir_key.to_file(signing_keydir=d)
        app = _key_app(SIGNING_KEY=inline.secret, SIGNING_KEY_DIR=d)
        keys = read_signing_keys(app)
    assert set(keys) == {inline.address}
    assert dir_key.address not in keys


def test_read_signing_keys_dir_only_unchanged() -> None:
    dir_key = SigningKey()
    with TemporaryDirectory() as d:
        dir_key.to_file(signing_keydir=d)
        app = _key_app(SIGNING_KEY=None, SIGNING_KEY_DIR=d)
        keys = read_signing_keys(app)
    assert set(keys) == {dir_key.address}


def test_read_signing_keys_neither_set_is_empty() -> None:
    app = _key_app(SIGNING_KEY=None, SIGNING_KEY_DIR=None)
    assert read_signing_keys(app) == {}


def test_read_signing_keys_inline_malformed_falls_back_to_dir() -> None:
    dir_key = SigningKey()
    with TemporaryDirectory() as d:
        dir_key.to_file(signing_keydir=d)
        app = _key_app(SIGNING_KEY='gcsec1notavalidsecret', SIGNING_KEY_DIR=d)
        keys = read_signing_keys(app)
    # A bad inline secret must not crash startup; it logs and falls back.
    assert set(keys) == {dir_key.address}
