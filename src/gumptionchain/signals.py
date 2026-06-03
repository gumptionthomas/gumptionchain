from __future__ import annotations

from blinker import Namespace

_signals = Namespace()

txn_failed = _signals.signal('transaction-failed')
# Fires for each newly-persisted block. From Node.process_block (the
# single-block delegate of receive_block): fires immediately after the
# per-block commit. From Node.fill_chain: fires only after the batch's
# db.session.commit() succeeds, in apply order — never for blocks that
# were rolled back by a later validation failure.
new_block = _signals.signal('new-block')
http_post = _signals.signal('http-post')
