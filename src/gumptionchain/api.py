from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Annotated, Any, NoReturn, cast

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    make_response,
    request,
)
from flask.views import MethodView
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
)

from gumptionchain import signing
from gumptionchain.api_client import PEER_HOST_HEADER
from gumptionchain.block import Block, expiry_cutoff
from gumptionchain.cache import cache
from gumptionchain.chain import Chain
from gumptionchain.exceptions import (
    EmptyChainError,
    GCError,
    InvalidRoleConfigError,
    MempoolFullError,
    MissingBlockError,
)
from gumptionchain.models import ChainDAO
from gumptionchain.node import Node
from gumptionchain.payload import (
    StakeKind,
    encode_subject,
    validate_raw_subject,
)
from gumptionchain.schema import (
    AddressType,
    PublicKeyType,
    pydantic_errors_to_messages,
    truncate,
    validate_address_format,
)
from gumptionchain.signals import http_post as http_post_signal
from gumptionchain.tasks import post_process
from gumptionchain.util import ciso_2_dt, host_address, now, now_iso
from gumptionchain.wallet import Wallet

blueprint = Blueprint('api', __name__)


class _AdaptedValidationError(Exception):
    """Adapter that satisfies make_error_response's ``.messages`` contract.

    make_error_response expects a Marshmallow-style ValidationError with
    a .messages attribute (nested dict shape). Instantiate this once per
    Pydantic ValidationError instead of building a new Exception subclass
    on every failure.
    """

    def __init__(self, messages: dict[str, Any]) -> None:
        super().__init__()
        self.messages = messages


def _pydantic_validation_error(e: ValidationError) -> _AdaptedValidationError:
    return _AdaptedValidationError(pydantic_errors_to_messages(e))


def node_lc_dao() -> tuple[Node, Chain | None, Any]:
    node = Node(
        host=current_app.config['NODE_HOST'],
        peers=current_app.config['PEERS'],
        clients=current_app.clients,  # type: ignore[attr-defined]
        logger=current_app.logger,
    )
    lc = node.longest_chain
    return node, lc, lc.to_dao() if lc is not None else None


def visited_hosts() -> list[str] | None:
    hosts = None
    if peer_hosts := request.headers.get(PEER_HOST_HEADER, None):
        hosts = [v.strip() for v in peer_hosts.split(',') if v]
    return hosts


def queue_post_process(
    path: str,
    data: str | bytes | None,
    vhosts: list[str] | None,
) -> None:
    host, address = host_address(current_app.config['NODE_HOST'])
    if address is None:
        # NODE_HOST carries no embedded wallet address (e.g. just
        # http://host:port), so we can't determine which local wallet
        # signs outbound peer requests. Async post-processing needs
        # NODE_HOST in http(s)://<address>@host form.
        current_app.logger.warning(
            'queue_post_process: NODE_HOST %r has no embedded wallet address '
            '(expected http(s)://<address>@host); cannot sign async '
            'post-processing — skipping',
            current_app.config['NODE_HOST'],
        )
        return
    wallet: Wallet | None = current_app.wallets.get(address)  # type: ignore[attr-defined]
    if wallet is None:
        # No local wallet held for this node's NODE_HOST address means we
        # can't sign an outbound peer request. Log and skip rather than
        # fail later.
        current_app.logger.warning(
            'queue_post_process: no local wallet for node address %s; skipping',
            address,
        )
        return
    http_post_signal.send(
        current_app._get_current_object(),  # type: ignore[attr-defined]
        host=host,
        address=address,
        path=path,
        data=data,
        vhosts=vhosts,
    )


def queue_block_post_process(block: Block, vhosts: list[str] | None) -> None:
    queue_post_process(
        f'/api/block/{block.block_hash}/process', block.to_json(), vhosts
    )


def queue_txn_post_process(txn: Any, vhosts: list[str] | None) -> None:
    queue_post_process(
        f'/api/transaction/{txn.txid}/process', txn.to_json(), vhosts
    )


def handle_http_post(
    sender: Any,
    host: str | None = None,
    address: str | None = None,
    path: str | None = None,
    data: str | bytes | None = None,
    vhosts: list[str] | None = None,
) -> None:
    if current_app.config.get('CELERY_BROKER_URL'):
        post_process.delay(host, address, path, data, vhosts)
    else:
        current_app.logger.warning(
            'handle_http_post: GC_API_ASYNC_PROCESSING is enabled but '
            'CELERY_BROKER_URL is unset; dropping async post-processing '
            'of %s',
            path,
        )


@blueprint.record
def connect_signals(state: Any) -> None:
    http_post_signal.connect(handle_http_post)


def make_json_response(json_data: Any, status_code: int = 200) -> Response:
    if not isinstance(json_data, (str, bytes)):
        json_data = json.dumps(json_data)
    response = make_response(json_data, status_code)
    response.headers['Content-Type'] = 'application/json'
    return response


def make_error_response(e: Any) -> Response:
    return make_json_response({'error': e.messages}, 400)


def exception_response(e: Exception) -> NoReturn:
    current_app.logger.exception(e)
    abort(500)


class Role(Enum):
    READER = 1
    TRANSACTOR = 2
    MILLER = 3
    ADMIN = 4

    def addresses(self) -> list[str]:
        return current_app.config.get(f'{self.name}_ADDRESSES')  # type: ignore[return-value]

    @classmethod
    def address_roles(cls, address: str) -> list[Role]:
        # Fail closed if a list is not configured (None / a stray string):
        # `isinstance` excludes both, avoiding silent substring semantics.
        # The '*' match-all sentinel is honored only for READER or TRANSACTOR
        # at match time too (defense-in-depth: startup validation forbids it in
        # higher tiers, but a runtime config mutation must not escalate).
        return [
            role
            for role in Role
            if isinstance(addrs := role.addresses(), (list, tuple))
            and (
                address in addrs
                or (role in (cls.READER, cls.TRANSACTOR) and '*' in addrs)
            )
        ]

    @classmethod
    def address_role(cls, address: str) -> Role | None:
        roles = cls.address_roles(address)
        return roles[-1] if roles else None

    @classmethod
    def validate_config(cls, config: Mapping[str, Any]) -> None:
        """Reject malformed role allowlists at startup.

        Each *_ADDRESSES entry must be a valid gumptionchain address,
        except the '*' match-all sentinel which is permitted only in
        READER_ADDRESSES or TRANSACTOR_ADDRESSES. Raises
        InvalidRoleConfigError on any violation.
        """
        for role in cls:
            entries = config.get(f'{role.name}_ADDRESSES', []) or []
            if not isinstance(entries, (list, tuple)):
                msg = (
                    f'{role.name}_ADDRESSES must be a JSON list of '
                    f'addresses, got {type(entries).__name__}'
                )
                raise InvalidRoleConfigError(msg)
            for entry in entries:
                if entry == '*':
                    if role not in (cls.READER, cls.TRANSACTOR):
                        msg = (
                            f'{role.name}_ADDRESSES contains "*" '
                            '(match-all is permitted only in '
                            'READER_ADDRESSES or TRANSACTOR_ADDRESSES)'
                        )
                        raise InvalidRoleConfigError(msg)
                elif not validate_address_format(entry):
                    msg = (
                        f'{role.name}_ADDRESSES entry {entry!r} '
                        'is not a valid gumptionchain address'
                    )
                    raise InvalidRoleConfigError(msg)


def authorize(
    required_role: Role = Role.READER,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def _authorize(
        func: Callable[..., Any],
    ) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                node_host = host_address(current_app.config['NODE_HOST'])[0]
                # request.headers is Werkzeug's case-insensitive Headers (not
                # a Mapping subtype, but exposes the .get() verify() uses);
                # case-insensitivity matters because HTTP transit normalizes
                # `GC-Sig-Version` to `Gc-Sig-Version`.
                address = signing.verify(
                    cast('Mapping[str, Any]', request.headers),
                    method=request.method,
                    path=request.path,
                    query=request.query_string.decode(),
                    body=request.get_data(),
                    node_host=node_host,
                )
            except signing.SignatureError:
                abort(401)
            except Exception as e:
                current_app.logger.exception(e)
                abort(401)
            # Live config is the authority for authorization. Re-checking
            # Role.address_role on every request closes the forged-claim
            # (A3.a) and stale-role-after-revocation (A5.b) gaps.
            role = Role.address_role(address)
            if role is None or role.value < required_role.value:
                abort(403)
            kwargs['_address'] = address
            kwargs['_role'] = role
            return func(*args, **kwargs)

        return wrapper

    return _authorize


authorize_reader = authorize(required_role=Role.READER)
authorize_transactor = authorize(required_role=Role.TRANSACTOR)
authorize_miller = authorize(required_role=Role.MILLER)
authorize_admin = authorize(required_role=Role.ADMIN)


class BlockView(MethodView):
    def get(self, block_hash: str | None = None, **kwargs: Any) -> Response:
        try:
            _, lc, _ = node_lc_dao()
            block = None
            if not block_hash and lc is not None:
                block = lc.last_block if lc else None
                block_hash = block.block_hash if block else None
            if block_hash:
                key = f'{block_hash}.block-json'
                if (block_json := cache.get(key)) is None:
                    block = block or Block.from_db(block_hash)
                    if block is not None:
                        block_json = block.to_json()
                        cache.set(key, block_json)
                if block_json:
                    return make_json_response(block_json)
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
        abort(404)

    def post(
        self,
        block_hash: str,
        process: str | bool = False,  # noqa: FBT001
        **kwargs: Any,
    ) -> Response:
        try:
            process = process == 'process'
            if not process:
                process = not current_app.config.get('API_ASYNC_PROCESSING')
            node, _, _ = node_lc_dao()
            vhosts = visited_hosts()
            received = now_iso()
            block = node.receive_block(
                request.data,
                block_hash=block_hash,
                visited_hosts=vhosts,
                process=process,
            )
            if process is False and block is not None:
                queue_block_post_process(block, vhosts)
        except MissingBlockError:
            abort(404)
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
        status_code = 200 if block is None else 201 if process else 202
        return make_json_response(
            {'received': received}, status_code=status_code
        )


reader_block_view = authorize_reader(BlockView.as_view('block_reader'))
miller_block_view = authorize_miller(BlockView.as_view('block_miller'))

blueprint.add_url_rule('/block', view_func=reader_block_view, methods=['GET'])

blueprint.add_url_rule(
    '/block/<mill_hash:block_hash>',
    view_func=reader_block_view,
    methods=['GET'],
)

blueprint.add_url_rule(
    '/block/<mill_hash:block_hash>',
    view_func=miller_block_view,
    methods=['POST'],
)

blueprint.add_url_rule(
    '/block/<mill_hash:block_hash>/<process>',
    view_func=miller_block_view,
    methods=['POST'],
)


class TxnView(MethodView):
    def post(
        self,
        txid: str,
        process: str | bool = False,  # noqa: FBT001
        **kwargs: Any,
    ) -> Response:
        try:
            process = process == 'process'
            if not process:
                process = not current_app.config.get('API_ASYNC_PROCESSING')
            node, _, _ = node_lc_dao()
            vhosts = visited_hosts()
            received = now_iso()
            txn = node.receive_transaction(
                txid, request.data, visited_hosts=vhosts, process=process
            )
            if process is False and txn is not None:
                queue_txn_post_process(txn, vhosts)
        except MempoolFullError:
            return make_json_response({'error': 'mempool full'}, 503)
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
        status_code = 200 if txn is None else 201 if process else 202
        return make_json_response(
            {'received': received}, status_code=status_code
        )


transactor_txn_view = authorize_transactor(TxnView.as_view('txn_transactor'))

blueprint.add_url_rule(
    '/transaction/<mill_hash:txid>',
    view_func=transactor_txn_view,
    methods=['POST'],
)
blueprint.add_url_rule(
    '/transaction/<mill_hash:txid>/<process>',
    view_func=transactor_txn_view,
    methods=['POST'],
)


class TransactionProvenanceView(MethodView):
    def get(self, txid: str, **kwargs: Any) -> Response:
        try:
            _, lc, _ = node_lc_dao()
            tip = lc.block_hash if lc is not None else None
            key = f'{tip}.{txid}.txn-provenance'
            if (prov := cache.get(key)) is None:
                prov = (
                    lc.transaction_provenance(txid)
                    if lc is not None
                    else ChainDAO.pending_provenance(txid)
                )
                if prov is not None:
                    cache.set(key, prov)
            if prov is None:
                return make_json_response(
                    {'error': 'transaction not found'}, 404
                )
            # deepcopy: prov is the cached object; its nested `outflows` list
            # would otherwise be shared by reference with the cache entry.
            return make_json_response(
                {'txid': txid, **copy.deepcopy(prov), 'as_of_block': tip}
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/<mill_hash:txid>',
    view_func=authorize_reader(
        TransactionProvenanceView.as_view('transaction_provenance_reader')
    ),
    methods=['GET'],
)


class TransferTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    public_key: PublicKeyType
    amount: int = Field(ge=1)
    address: AddressType


class TransferTxnView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            model = TransferTxnQueryModel.model_validate(
                request.args.to_dict(flat=True)
            )
        except ValidationError as e:
            return make_error_response(_pydantic_validation_error(e))
        try:
            args = model.model_dump(exclude_none=True)
            public_key_b64 = args['public_key']
            amount = args['amount']
            dest_address = args['address']
            wallet = Wallet(b64ks=public_key_b64)
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            return make_json_response(
                lc.create_transfer(wallet, amount, dest_address).to_json()
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/transfer',
    view_func=authorize_transactor(
        TransferTxnView.as_view('txn_transfer_transactor')
    ),
    methods=['GET'],
)


def _check_raw_subject(s: str) -> str:
    if not validate_raw_subject(s):
        msg = f'Invalid raw subject: {truncate(s)!r}'
        raise ValueError(msg)
    return s


_RawSubjectField = Annotated[str, AfterValidator(_check_raw_subject)]


class SubjectTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    public_key: PublicKeyType
    amount: int = Field(ge=1)
    subject: _RawSubjectField


class RescindTxnQueryModel(SubjectTxnQueryModel):
    kind: StakeKind


class OppositionTxnView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            model = SubjectTxnQueryModel.model_validate(
                request.args.to_dict(flat=True)
            )
        except ValidationError as e:
            return make_error_response(_pydantic_validation_error(e))
        try:
            args = model.model_dump(exclude_none=True)
            public_key_b64 = args['public_key']
            amount = args['amount']
            subject = encode_subject(args['subject'])
            wallet = Wallet(b64ks=public_key_b64)
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            return make_json_response(
                lc.create_opposition(wallet, amount, subject).to_json()
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/opposition',
    view_func=authorize_transactor(
        OppositionTxnView.as_view('txn_opposition_transactor')
    ),
    methods=['GET'],
)


class RescindTxnView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            model = RescindTxnQueryModel.model_validate(
                request.args.to_dict(flat=True)
            )
        except ValidationError as e:
            return make_error_response(_pydantic_validation_error(e))
        try:
            args = model.model_dump(exclude_none=True)
            public_key_b64 = args['public_key']
            amount = args['amount']
            subject = encode_subject(args['subject'])
            kind = args['kind']
            wallet = Wallet(b64ks=public_key_b64)
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            return make_json_response(
                lc.create_rescind(wallet, amount, subject, kind).to_json()
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/rescind',
    view_func=authorize_transactor(
        RescindTxnView.as_view('txn_rescind_transactor')
    ),
    methods=['GET'],
)


class SupportTxnView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            model = SubjectTxnQueryModel.model_validate(
                request.args.to_dict(flat=True)
            )
        except ValidationError as e:
            return make_error_response(_pydantic_validation_error(e))
        try:
            args = model.model_dump(exclude_none=True)
            public_key_b64 = args['public_key']
            amount = args['amount']
            subject = encode_subject(args['subject'])
            wallet = Wallet(b64ks=public_key_b64)
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            return make_json_response(
                lc.create_support(wallet, amount, subject).to_json()
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/support',
    view_func=authorize_transactor(
        SupportTxnView.as_view('txn_support_transactor')
    ),
    methods=['GET'],
)


_CisoTimestamp = Annotated[
    datetime,
    BeforeValidator(lambda v: ciso_2_dt(v) if isinstance(v, str) else v),
]


class PendingTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    earliest: _CisoTimestamp | None = None


class PendingTxnView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            model = PendingTxnQueryModel.model_validate(
                request.args.to_dict(flat=True)
            )
        except ValidationError as e:
            return make_error_response(_pydantic_validation_error(e))
        try:
            args = model.model_dump(exclude_none=True)
            node, _, _ = node_lc_dao()
            node.discard_expired_pending_txns()
            earliest = args.get('earliest')
            expired = expiry_cutoff(now())
            pending_json = node.pending_txns.query_json(
                earliest=earliest, expired=expired
            )
            return make_json_response([json.loads(j) for j in pending_json])
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/pending',
    view_func=authorize_reader(PendingTxnView.as_view('txn_pending_reader')),
    methods=['GET'],
)


class WalletBalanceView(MethodView):
    def get(self, address: str, **kwargs: Any) -> Response:
        try:
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            block_hash = lc.block_hash
            key = f'{block_hash}.{address}.wallet-balance'
            if (balance := cache.get(key)) is None:
                balance = lc.balance(address)
                cache.set(key, balance)
            return make_json_response(
                {'balance': balance, 'as_of_block': block_hash}
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/wallet/<address:address>/balance',
    view_func=authorize_reader(
        WalletBalanceView.as_view('wallet_balance_transactor')
    ),
    methods=['GET'],
)


class OppositionBalanceView(MethodView):
    def get(self, subject: str, **kwargs: Any) -> Response:
        try:
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            block_hash = lc.block_hash
            key = f'{block_hash}.{subject}.opposition'
            if (balance := cache.get(key)) is None:
                balance = lc.opposition_balance(subject)
                cache.set(key, balance)
            return make_json_response(
                {'balance': balance, 'as_of_block': block_hash}
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/subject/<subject:subject>/opposition',
    view_func=authorize_reader(
        OppositionBalanceView.as_view('opposition_balance_transactor')
    ),
    methods=['GET'],
)


class SubjectSupportView(MethodView):
    def get(self, subject: str, **kwargs: Any) -> Response:
        try:
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            block_hash = lc.block_hash
            key = f'{block_hash}.{subject}.support'
            if (support := cache.get(key)) is None:
                support = lc.support_balance(subject)
                cache.set(key, support)
            return make_json_response(
                {'support': support, 'as_of_block': block_hash}
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/subject/<subject:subject>/support',
    view_func=authorize_reader(
        SubjectSupportView.as_view('subject_support_transactor')
    ),
    methods=['GET'],
)
