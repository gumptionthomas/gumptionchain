from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from flask import Blueprint, Request, Response, jsonify, request

from gumptionchain.api_client import ApiClient
from gumptionchain.chain import GRAIN_PER_GRIT
from gumptionchain.payload import encode_subject, validate_raw_subject
from gumptionchain.schema import validate_address_format


class _ProxyError(Exception):
    """A browser-facing error: status code + JSON message. Raised by the
    handlers/helpers and mapped to a JSON response by one errorhandler."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _node_error(r: Any) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text or 'node error'
    if isinstance(body, dict) and body.get('error') is not None:
        err = body['error']
        return err if isinstance(err, str) else str(err)
    return r.text or 'node error'


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke an ApiClient method with raise_for_status=False; a transport
    failure (node down/unreachable) becomes a 502."""
    try:
        return fn(*args, raise_for_status=False, **kwargs)
    except httpx.RequestError as exc:
        raise _ProxyError(502, 'node unavailable') from exc


def _ok(r: Any) -> Any:
    """Pass a <400 node response through; map node errors to proxy errors."""
    if r.status_code == 404:
        raise _ProxyError(404, _node_error(r))
    if 400 <= r.status_code < 500:
        raise _ProxyError(400, _node_error(r))
    if r.status_code >= 500:
        raise _ProxyError(502, 'node error')
    return r


def _require_subject(subject: object) -> str:
    if not isinstance(subject, str) or not validate_raw_subject(subject):
        raise _ProxyError(400, 'invalid subject (1-79 printable chars)')
    return subject


def _grit_to_grains(value: object) -> int:
    try:
        grit = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _ProxyError(400, 'amount_grit must be a number') from None
    if grit <= 0:
        raise _ProxyError(400, 'amount_grit must be positive')
    grains = grit * GRAIN_PER_GRIT
    if grains != grains.to_integral_value():
        raise _ProxyError(400, 'amount_grit precision exceeds 0.01 GRIT')
    return int(grains)


def _grit(grains: int) -> dict[str, Any]:
    return {'grit': grains / GRAIN_PER_GRIT, 'grains': grains}


def node_proxy_blueprint(
    make_client: Callable[[], ApiClient],
    *,
    url_path: str = '/api/node',
    rate_limit: Callable[[Request], bool] | None = None,
    max_body_bytes: int = 65536,
    name: str = 'gumptionchain_node_proxy',
) -> Blueprint:
    """A browser-facing JSON relay over ``ApiClient`` for GRIT support/oppose
    spending, keeping the node host server-side. ``make_client`` supplies a
    configured client (node host + a TRANSACTOR/READER key). The relay holds no
    key; it converts GRIT<->grains, validates subjects, and maps errors."""
    bp = Blueprint(name, __name__, url_prefix=url_path)

    @bp.errorhandler(_ProxyError)
    def _handle(exc: _ProxyError) -> tuple[Response, int]:
        return jsonify({'error': exc.message}), exc.status

    @bp.before_request
    def _guard() -> tuple[Response, int] | None:
        # Return (not raise) to short-circuit — the documented before_request
        # contract — so this never depends on errorhandler timing.
        if rate_limit is not None and not rate_limit(request):
            return jsonify({'error': 'rate limited'}), 429
        length = request.content_length
        if length is not None and length > max_body_bytes:
            return jsonify({'error': 'request too large'}), 413
        return None

    @bp.get('/balance/<address>')
    def balance(address: str) -> Response:
        r = _ok(_call(make_client().get_signing_key_balance, address))
        body = r.json()
        return jsonify(
            {
                **_grit(int(body['balance'])),
                'as_of_block': body.get('as_of_block'),
            }
        )

    @bp.get('/subject/balances')
    def subject_balances() -> Response:
        raw = _require_subject(request.args.get('subject'))
        enc = encode_subject(raw)
        client = make_client()
        support = int(
            _ok(_call(client.get_support_balance, enc)).json()['support']
        )
        opp = int(
            _ok(_call(client.get_opposition_balance, enc)).json()['opposition']
        )
        return jsonify(
            {
                'subject': raw,
                'support': _grit(support),
                'opposition': _grit(opp),
            }
        )

    @bp.get('/subject/search')
    def subject_search() -> Response:
        q = request.args.get('q', '')
        limit = request.args.get('limit', '8')
        r = _ok(_call(make_client().get_subject_search, q, limit))
        body = r.json()
        subjects = [
            {
                'subject': row['subject'],
                'support': _grit(int(row['support'])),
                'opposition': _grit(int(row['opposition'])),
            }
            for row in body.get('subjects', [])
        ]
        return jsonify({'subjects': subjects})

    def _build(method_name: str) -> Response:
        data = request.get_json(silent=True) or {}
        public_key = data.get('public_key')
        if not isinstance(public_key, str) or not public_key:
            raise _ProxyError(400, 'public_key required')
        subject = _require_subject(data.get('subject'))
        grains = _grit_to_grains(data.get('amount_grit'))
        method = getattr(make_client(), method_name)
        return jsonify(_ok(_call(method, public_key, grains, subject)).json())

    @bp.post('/txn/support')
    def txn_support() -> Response:
        return _build('get_support_transaction')

    @bp.post('/txn/oppose')
    def txn_oppose() -> Response:
        return _build('get_opposition_transaction')

    @bp.post('/txn/transfer')
    def txn_transfer() -> Response:
        # Build an unsigned player->address GRIT transfer. Same shape as the
        # support/oppose build, but the destination is an address (validated
        # here for a clean 400; the node validates it as AddressType too).
        data = request.get_json(silent=True) or {}
        public_key = data.get('public_key')
        if not isinstance(public_key, str) or not public_key:
            raise _ProxyError(400, 'public_key required')
        to_address = data.get('to_address')
        if not isinstance(to_address, str) or not validate_address_format(
            to_address
        ):
            raise _ProxyError(400, 'invalid to_address')
        grains = _grit_to_grains(data.get('amount_grit'))
        return jsonify(
            _ok(
                _call(
                    make_client().get_transfer_transaction,
                    public_key,
                    grains,
                    to_address,
                )
            ).json()
        )

    @bp.post('/txn/split')
    def txn_split() -> Response:
        # Build an unsigned self-split: mint `count` chips of denomination_grit
        # each (back to the signer's own address). Client signs + submits.
        data = request.get_json(silent=True) or {}
        public_key = data.get('public_key')
        if not isinstance(public_key, str) or not public_key:
            raise _ProxyError(400, 'public_key required')
        denomination = _grit_to_grains(data.get('denomination_grit'))
        count = data.get('count')
        # bool is an int subclass — reject it explicitly so a JSON `true`
        # doesn't slip through as count == 1.
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise _ProxyError(400, 'count must be a positive integer')
        return jsonify(
            _ok(
                _call(
                    make_client().get_split_transaction,
                    public_key,
                    denomination,
                    count,
                )
            ).json()
        )

    @bp.post('/txn/submit')
    def txn_submit() -> Response:
        data = request.get_json(silent=True) or {}
        signed = data.get('signed')
        if not isinstance(signed, dict):
            raise _ProxyError(400, 'signed txn object required')
        txid = signed.get('txid')
        if not isinstance(txid, str) or not isinstance(
            signed.get('signature'), str
        ):
            raise _ProxyError(400, 'signed txn must have a txid and signature')
        _ok(
            _call(
                make_client().post,
                f'/api/transaction/{txid}',
                data=json.dumps(signed),
                headers={'Content-Type': 'application/json'},
            )
        )
        return jsonify({'txid': txid})

    @bp.get('/txn/<txid>/status')
    def txn_status(txid: str) -> Response:
        r = _call(make_client().get, f'/api/transaction/{txid}')
        if r.status_code == 404:
            raise _ProxyError(404, 'unknown txid')
        _ok(r)
        body = r.json()
        if body.get('status') == 'canonical':
            return jsonify(
                {
                    'state': 'milled',
                    'block': body.get('block_hash'),
                    'confirmations': body.get('confirmations'),
                }
            )
        return jsonify({'state': 'pending'})

    return bp
