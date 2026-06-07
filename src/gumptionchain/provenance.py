from __future__ import annotations

from typing import Any

from flask import current_app

from gumptionchain.models import ChainDAO
from gumptionchain.node import Node


def lookup_provenance(txid: str) -> dict[str, Any] | None:
    """Public, in-process provenance lookup — the same data the authed
    /api/transaction/<txid> view returns, minus authentication. Returns the
    #176a provenance dict, or None if the txn is unknown.

    Resolves the longest chain via Node directly (mirroring
    browser.longest_chain) rather than the API layer, to avoid an upward
    dependency from this module into the api blueprint.
    """
    lc = Node(logger=current_app.logger).longest_chain
    if lc is not None:
        return lc.transaction_provenance(txid)
    return ChainDAO.pending_provenance(txid)
