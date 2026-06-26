from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from typing import ClassVar, Self


@dataclass
class EnvironSettings:
    _prefix: ClassVar[str] = ''

    @classmethod
    def getenv(cls, name: str) -> str | None:
        return os.environ.get(f'{cls._prefix}{name}')

    @classmethod
    def from_env(cls) -> Self:
        c = cls()
        for f in fields(c):
            if (v := cls.getenv(f.name)) is not None:
                v = v.strip()
                try:
                    setattr(c, f.name, json.loads(v))
                except Exception:
                    setattr(c, f.name, v)
        return c


@dataclass
class EnvAppSettings(EnvironSettings):
    _prefix: ClassVar[str] = 'GC_'

    NODE_HOST: str | None = field(default=None)
    # Canonical WebAuthn RP ID for passkey ceremonies on the browser pages.
    # None → the page falls back to its origin hostname (self-scoped). Set to a
    # shared EGU rpId (e.g. the hub domain) to federate one passkey identity
    # across distinct origins via Related Origin Requests (gump-hub#67).
    RP_ID: str | None = field(default=None)
    PEERS: list[str] = field(default_factory=list)
    API_CLIENT_TIMEOUT: int = field(default=10)
    MAX_CHAIN_FILL_DEPTH: int = field(default=50000)
    FORK_PRUNE_DEPTH: int = field(default=100)
    SYNC_BATCH_SIZE: int = field(default=256)
    MAX_PENDING_TXNS: int = field(default=10000)
    MAX_PENDING_PER_TRANSACTOR: int = field(default=100)
    API_ASYNC_PROCESSING: bool = field(default=False)
    DEFAULT_COMMAND_HOST: str | None = field(default=None)
    SIGNING_KEY_DIR: str | None = field(default=None)
    # Inline single-key identity: a gcsec1… secret (env GC_SIGNING_KEY) loaded
    # as THE node key, for platforms that inject secrets as env vars not files
    # (Fly.io). Takes precedence over SIGNING_KEY_DIR. Never logged.
    SIGNING_KEY: str | None = field(default=None)
    ADMIN_ADDRESSES: list[str] = field(default_factory=list)
    MILLER_ADDRESSES: list[str] = field(default_factory=list)
    TRANSACTOR_ADDRESSES: list[str] = field(default_factory=list)
    READER_ADDRESSES: list[str] = field(default_factory=list)
