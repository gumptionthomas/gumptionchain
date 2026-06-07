from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, current_app, jsonify, render_template
from werkzeug.exceptions import HTTPException

from gumptionchain.block import Block
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.models import BlockDAO, ChainDAO, TransactionDAO
from gumptionchain.node import Node
from gumptionchain.provenance import lookup_provenance
from gumptionchain.transaction import Transaction


def longest_chain() -> Chain | None:
    return Node(logger=current_app.logger).longest_chain


blueprint = Blueprint(
    'browser',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static/gumptionchain',
)


@blueprint.route('/')
def index_view() -> Any:
    try:
        lc = longest_chain()
    except HTTPException as e:
        return e
    except Exception as e:
        # Log the full traceback server-side, then return a controlled 500
        # response. `return e` would hand Flask a raw Exception (not a valid
        # response → make_response TypeError); abort(500) yields a proper
        # error response with no internal detail in the body (audit WEB2).
        current_app.logger.exception(e)
        abort(500)
    return render_template('index.html', title='Home', lc=lc)


@blueprint.route('/chains')
def chains_view() -> Any:
    try:
        chains_page = db.paginate(ChainDAO.chains())
    except HTTPException as e:
        return e
    except Exception as e:
        # Log the full traceback server-side, then return a controlled 500
        # response. `return e` would hand Flask a raw Exception (not a valid
        # response → make_response TypeError); abort(500) yields a proper
        # error response with no internal detail in the body (audit WEB2).
        current_app.logger.exception(e)
        abort(500)
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
        # Log the full traceback server-side, then return a controlled 500
        # response. `return e` would hand Flask a raw Exception (not a valid
        # response → make_response TypeError); abort(500) yields a proper
        # error response with no internal detail in the body (audit WEB2).
        current_app.logger.exception(e)
        abort(500)
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
        # Log the full traceback server-side, then return a controlled 500
        # response. `return e` would hand Flask a raw Exception (not a valid
        # response → make_response TypeError); abort(500) yields a proper
        # error response with no internal detail in the body (audit WEB2).
        current_app.logger.exception(e)
        abort(500)
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


@blueprint.route('/transaction/<mill_hash:txid>/provenance.json')
def transaction_provenance_view(txid: str) -> Any:
    try:
        prov = lookup_provenance(txid)
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        abort(500)
    if prov is None:
        return jsonify({'error': 'transaction not found'}), 404
    # Route param (mill_hash-validated) is authoritative for txid; unpack prov
    # first so a stray 'txid' key can't override the request path.
    return jsonify({**prov, 'txid': txid})
