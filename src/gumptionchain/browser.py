from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from flask import Blueprint, abort, current_app, jsonify, render_template
from flask_sqlalchemy.pagination import SelectPagination
from werkzeug.exceptions import HTTPException

from gumptionchain.block import Block
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.models import BlockDAO, ChainDAO, TransactionDAO
from gumptionchain.node import Node
from gumptionchain.payload import decode_subject
from gumptionchain.provenance import lookup_provenance
from gumptionchain.transaction import Transaction


class _RowPagination(SelectPagination):
    """Pagination over a compound/column select (not a single ORM entity).

    ``db.paginate`` applies ``.scalars()``, which collapses a multi-column
    leaderboard row down to its first column. This variant keeps whole
    ``Row`` objects (so templates can read ``row.subject`` / ``.total``)
    and counts without the ORM-only ``lazyload`` option.
    """

    def _query_items(self) -> list[Any]:
        select = self._query_args['select']
        select = select.limit(self.per_page).offset(self._query_offset)
        session = self._query_args['session']
        return list(session.execute(select).all())

    def _query_count(self) -> int:
        select = self._query_args['select']
        sub = select.order_by(None).subquery()
        session = self._query_args['session']
        out = session.execute(
            sa.select(sa.func.count()).select_from(sub)
        ).scalar()
        return out or 0


def paginate_rows(select: sa.sql.Select[Any]) -> _RowPagination:
    return _RowPagination(select=select, session=db.session())


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


@blueprint.route('/blocks')
def blocks_view() -> Any:
    try:
        blocks_page = db.paginate(BlockDAO.longest_chain_blocks_q())
        tx_counts = BlockDAO.transaction_counts(
            [block.id for block in blocks_page.items]
        )
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
        'blocks.html',
        title='Blocks',
        blocks_page=blocks_page,
        tx_counts=tx_counts,
    )


@blueprint.route('/subjects')
def subjects_view() -> Any:
    try:
        lc = longest_chain()
        subjects_page = (
            paginate_rows(lc.subject_leaderboard()) if lc is not None else None
        )
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
        'subjects.html', title='Subjects', subjects_page=subjects_page
    )


@blueprint.route('/subject/<subject:subject>')
def subject_view(subject: str) -> Any:
    try:
        lc = longest_chain()
        if lc is None:
            opposition = support = 0
            opposition_flows: list[Any] = []
            support_flows: list[Any] = []
        else:
            opposition = lc.opposition_balance(subject)
            support = lc.support_balance(subject)
            dao = lc.to_dao()
            opposition_flows = list(
                db.session.scalars(
                    dao.unrescinded_outflows(subject, 'opposition')
                )
            )
            support_flows = list(
                db.session.scalars(dao.unrescinded_outflows(subject, 'support'))
            )
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
        'subject.html',
        title=f'Subject: {decode_subject(subject)}',
        subject=subject,
        opposition=opposition,
        support=support,
        opposition_flows=opposition_flows,
        support_flows=support_flows,
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
    # Deliberately simpler than the authed api.TransactionProvenanceView: this
    # public read does no caching and omits `as_of_block`. The authed view's
    # cache keys on the chain tip; replicating it here is unnecessary under the
    # default NullCache and would couple this public read to the cache layer.
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


@blueprint.route('/verify')
def verify_view() -> Any:
    return render_template('verify.html', title='Verify')
