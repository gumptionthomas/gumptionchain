import json
import re
from enum import Enum
from functools import wraps
from urllib.parse import urljoin

import jwt
from flask import Blueprint, abort, current_app, make_response, request
from flask.views import MethodView
from marshmallow import Schema, ValidationError, fields, validate

from cancelchain.api_client import PEER_HOST_HEADER, ApiClient
from cancelchain.block import TXN_TIMEOUT, Block
from cancelchain.cache import cache
from cancelchain.exceptions import CCError, EmptyChainError, MissingBlockError
from cancelchain.models import ApiToken
from cancelchain.node import Node
from cancelchain.payload import encode_subject, validate_raw_subject
from cancelchain.schema import validate_address_format, validate_public_key
from cancelchain.signals import http_post as http_post_signal
from cancelchain.tasks import post_process
from cancelchain.util import ciso_2_dt, dt_2_ciso, host_address, now, now_iso
from cancelchain.wallet import Wallet

API_TOKEN_SECONDS = 60 * 60 * 4

blueprint = Blueprint('api', __name__)


def node_lc_dao():
    node = Node(
        host=current_app.config['NODE_HOST'],
        peers=current_app.config['PEERS'],
        clients=current_app.clients,
        logger=current_app.logger,
    )
    lc = node.longest_chain
    return node, lc, lc.to_dao() if lc is not None else None


def visited_hosts():
    hosts = None
    if peer_hosts := request.headers.get(PEER_HOST_HEADER, None):
        hosts = [v.strip() for v in peer_hosts.split(',') if v]
    return hosts


def queue_post_process(path, data, visited_hosts):
    host, address = host_address(current_app.config['NODE_HOST'])
    wallet = current_app.wallets.get(address)
    headers = None
    if visited_hosts:
        headers = {PEER_HOST_HEADER: ','.join(visited_hosts)}
    headers = ApiClient(host, wallet).auth_header(headers=headers)
    url = urljoin(host, path)
    http_post_signal.send(
        current_app._get_current_object(), url=url, data=data, headers=headers
    )


def queue_block_post_process(block, visited_hosts):
    queue_post_process(
        f'/api/block/{block.block_hash}/process', block.to_json(), visited_hosts
    )


def queue_txn_post_process(txn, visited_hosts):
    queue_post_process(
        f'/api/transaction/{txn.txid}/process', txn.to_json(), visited_hosts
    )


def handle_http_post(sender, url=None, data=None, headers=None):
    if current_app.config.get('CELERY_BROKER_URL'):
        post_process.delay(url, data, headers=headers)


@blueprint.record
def connect_signals(state):
    http_post_signal.connect(handle_http_post)


def make_json_response(json_data, status_code=200):
    if not isinstance(json_data, (str, bytes)):
        json_data = json.dumps(json_data)
    response = make_response(json_data, status_code)
    response.headers['Content-Type'] = 'application/json'
    return response


def make_error_response(e):
    return make_json_response({'error': e.messages}, 400)


def exception_response(e):
    current_app.logger.exception(e)
    abort(500)


class Role(Enum):
    READER = 1
    TRANSACTOR = 2
    MILLER = 3
    ADMIN = 4

    def addresses(self):
        return current_app.config.get(f'{self.name}_ADDRESSES')

    @classmethod
    def address_roles(cls, address):
        return [
            role
            for role in Role
            if any(re.fullmatch(x, address) for x in role.addresses())
        ]

    @classmethod
    def address_role(cls, address):
        roles = cls.address_roles(address)
        return roles[-1] if roles else None


class TokenView(MethodView):
    def get(self, address):
        api_token = ApiToken.get(address)
        if not api_token:
            if not (wallet := current_app.wallets.get(address)):
                _, _, lc_dao = node_lc_dao()
                if lc_dao is None:
                    abort(401)
                if txn := lc_dao.address_transactions(address).first():
                    wallet = Wallet(b64ks=txn.public_key)
            if not wallet:
                abort(401)
            api_token = ApiToken.create(wallet)
        return make_json_response({'cipher': api_token.refreshed_cipher()})

    def post(self, address):
        if (api_token := ApiToken.get(address)) is None:
            abort(401)
        if not api_token.verify(request.json.get('challenge')):
            abort(401)
        api_token.reset()
        if (role := Role.address_role(address)) is None:
            abort(403)
        token = jwt.encode(
            {
                'sub': address,
                'rol': str(role.name),
                'exp': now().timestamp() + API_TOKEN_SECONDS,
            },
            current_app.config['SECRET_KEY'],
            algorithm='HS256',
        )
        return make_json_response({'token': token})


blueprint.add_url_rule(
    '/token/<address:address>',
    view_func=TokenView.as_view('token'),
    methods=['GET', 'POST'],
)


def authorize(required_role=Role.READER):
    def _authorize(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            authorized = False
            address = None
            role = None
            try:
                token = request.headers.get('Authorization')
                if token and token.startswith('Bearer '):
                    token = token[7:]
                else:
                    token = None
                if token:
                    data = jwt.decode(
                        token,
                        current_app.config['SECRET_KEY'],
                        algorithms=['HS256'],
                    )
                    address = data['sub']
                    role = Role[data['rol']]
                    if address and role.value >= required_role.value:
                        authorized = True
            except jwt.exceptions.ExpiredSignatureError:
                abort(401)
            except Exception as e:
                current_app.logger.exception(e)
                abort(401)
            if authorized:
                kwargs['_address'] = address
                kwargs['_role'] = role
                return func(*args, **kwargs)
            abort(401)

        return wrapper

    return _authorize


authorize_reader = authorize(required_role=Role.READER)
authorize_transactor = authorize(required_role=Role.TRANSACTOR)
authorize_miller = authorize(required_role=Role.MILLER)
authorize_admin = authorize(required_role=Role.ADMIN)


class BlockView(MethodView):
    def get(self, block_hash=None, **kwargs):
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
        except CCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
        abort(404)

    def post(self, block_hash, process=False, **kwargs):
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
        except CCError as err:
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
    def post(self, txid, process=False, **kwargs):
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
        except CCError as err:
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


class TransferTxnQuerySchema(Schema):
    public_key = fields.String(required=True, validate=validate_public_key)
    amount = fields.Integer(required=True, validate=validate.Range(min=1))
    address = fields.String(required=True, validate=validate_address_format)


class TransferTxnView(MethodView):
    def get(self, **kwargs):
        try:
            args = TransferTxnQuerySchema().load(request.args)
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
        except (ValidationError, CCError) as err:
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


class SubjectTxnQuerySchema(Schema):
    public_key = fields.String(required=True, validate=validate_public_key)
    amount = fields.Integer(required=True, validate=validate.Range(min=1))
    subject = fields.String(required=True, validate=validate_raw_subject)


class SubjectTxnView(MethodView):
    def get(self, **kwargs):
        try:
            args = SubjectTxnQuerySchema().load(request.args)
            public_key_b64 = args['public_key']
            amount = args['amount']
            subject = encode_subject(args['subject'])
            wallet = Wallet(b64ks=public_key_b64)
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            return make_json_response(
                lc.create_subject(wallet, amount, subject).to_json()
            )
        except (ValidationError, CCError) as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/subject',
    view_func=authorize_transactor(
        SubjectTxnView.as_view('txn_subject_transactor')
    ),
    methods=['GET'],
)


class ForgiveTxnView(MethodView):
    def get(self, **kwargs):
        try:
            args = SubjectTxnQuerySchema().load(request.args)
            public_key_b64 = args['public_key']
            amount = args['amount']
            subject = encode_subject(args['subject'])
            wallet = Wallet(b64ks=public_key_b64)
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            return make_json_response(
                lc.create_forgive(wallet, amount, subject).to_json()
            )
        except (ValidationError, CCError) as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/forgive',
    view_func=authorize_transactor(
        ForgiveTxnView.as_view('txn_forgive_transactor')
    ),
    methods=['GET'],
)


class SupportTxnView(MethodView):
    def get(self, **kwargs):
        try:
            args = SubjectTxnQuerySchema().load(request.args)
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
        except (ValidationError, CCError) as err:
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


class PendingTxnQuerySchema(Schema):
    earliest = fields.Function(
        lambda obj: dt_2_ciso(obj.earliest),
        deserialize=lambda v: ciso_2_dt(v),
        required=False,
    )


class PendingTxnView(MethodView):
    def get(self, **kwargs):
        try:
            node, _, _ = node_lc_dao()
            node.discard_expired_pending_txns()
            args = PendingTxnQuerySchema().load(request.args)
            earliest = args.get('earliest')
            expired = now() - TXN_TIMEOUT
            pending_json = node.pending_txns.query_json(
                earliest=earliest, expired=expired
            )
            return make_json_response([json.loads(j) for j in pending_json])
        except (ValidationError, CCError) as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/pending',
    view_func=authorize_reader(PendingTxnView.as_view('txn_pending_reader')),
    methods=['GET'],
)


class WalletBalanceView(MethodView):
    def get(self, address, **kwargs):
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
        except CCError as err:
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


class SubjectBalanceView(MethodView):
    def get(self, subject, **kwargs):
        try:
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            block_hash = lc.block_hash
            key = f'{block_hash}.{subject}.balance'
            if (balance := cache.get(key)) is None:
                balance = lc.subject_balance(subject)
                cache.set(key, balance)
            return make_json_response(
                {'balance': balance, 'as_of_block': block_hash}
            )
        except CCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/subject/<subject:subject>/balance',
    view_func=authorize_reader(
        SubjectBalanceView.as_view('subject_balance_transactor')
    ),
    methods=['GET'],
)


class SubjectSupportView(MethodView):
    def get(self, subject, **kwargs):
        try:
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            block_hash = lc.block_hash
            key = f'{block_hash}.{subject}.support'
            if (support := cache.get(key)) is None:
                support = lc.subject_support(subject)
                cache.set(key, support)
            return make_json_response(
                {'support': support, 'as_of_block': block_hash}
            )
        except CCError as err:
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
