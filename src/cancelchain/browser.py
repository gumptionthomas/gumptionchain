from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, current_app, render_template
from werkzeug.exceptions import HTTPException

from cancelchain.block import Block
from cancelchain.chain import Chain
from cancelchain.database import db
from cancelchain.models import BlockDAO, ChainDAO, TransactionDAO
from cancelchain.node import Node
from cancelchain.transaction import Transaction


def longest_chain() -> Chain | None:
    return Node(logger=current_app.logger).longest_chain


blueprint = Blueprint('browser', __name__)


@blueprint.route('/')
def index_view() -> Any:
    try:
        lc = longest_chain()
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        return e
    return render_template('index.html', title='Home', lc=lc)


@blueprint.route('/chains')
def chains_view() -> Any:
    try:
        chains_page = db.paginate(ChainDAO.chains())
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        return e
    return render_template(
        'chains.html', title='Chains', chains_page=chains_page
    )


@blueprint.route('/block')
@blueprint.route('/block/<mill_hash:block_hash>')
def block_view(block_hash: str | None = None) -> Any:
    try:
        if block_hash is None:
            lc = longest_chain()
            last_block = lc.last_block if lc is not None else None
            block_hash = (
                last_block.block_hash if last_block is not None else None
            )
        block_dao = BlockDAO.get(block_hash=block_hash)
        if block_dao is None:
            abort(404)
        block = Block.from_dao(block_dao)
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        return e
    return render_template(
        'block.html',
        title=f'Block #{block.idx}: {block.block_hash}',
        block=block,
        block_dao=block_dao,
    )


@blueprint.route('/transaction/<mill_hash:txid>')
def transaction_view(txid: str) -> Any:
    try:
        inflows = []
        inflow_total = 0
        outflows = []
        outflow_total = 0
        transaction_dao = TransactionDAO.get(txid)
        if transaction_dao is None:
            abort(404)
        transaction = Transaction.from_dao(transaction_dao)
        for inflow in transaction.inflows:
            transaction_dao = TransactionDAO.get(inflow.outflow_txid)  # type: ignore[arg-type]
            ioflow_txn = Transaction.from_dao(transaction_dao)
            ioflow = ioflow_txn.get_outflow(inflow.outflow_idx)  # type: ignore[arg-type]
            inflows.append((inflow, ioflow_txn, ioflow))
            inflow_total += ioflow.amount  # type: ignore[union-attr,operator]
        for outflow in transaction.outflows:
            outflows.append(outflow)
            outflow_total += outflow.amount  # type: ignore[operator]
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        return e
    return render_template(
        'transaction.html',
        title=f'Transaction: {transaction.txid}',
        transaction=transaction,
        transaction_dao=transaction_dao,
        inflows=inflows,
        inflow_total=inflow_total,
        outflows=outflows,
        outflow_total=outflow_total,
    )
