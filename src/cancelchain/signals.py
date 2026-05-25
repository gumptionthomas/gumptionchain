from __future__ import annotations

from blinker import Namespace

_signals = Namespace()

txn_failed = _signals.signal('transaction-failed')
new_block = _signals.signal('new-block')
http_post = _signals.signal('http-post')
