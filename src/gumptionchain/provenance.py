from __future__ import annotations

from typing import Any

from gumptionchain.api import node_lc_dao
from gumptionchain.models import ChainDAO


def lookup_provenance(txid: str) -> dict[str, Any] | None:
    """Public, in-process provenance lookup — the same code path the authed
    /api/transaction/<txid> view uses, minus authentication. Returns the
    #176a provenance dict, or None if the txn is unknown.
    """
    _, lc, _ = node_lc_dao()
    if lc is not None:
        return lc.transaction_provenance(txid)
    return ChainDAO.pending_provenance(txid)
