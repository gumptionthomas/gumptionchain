import datetime
import os

from werkzeug.routing import BaseConverter, ValidationError

from cancelchain import __version__, api, browser, command
from cancelchain.api_client import ApiClient
from cancelchain.payload import decode_subject, validate_subject
from cancelchain.schema import validate_address_format, validate_base64
from cancelchain.util import host_address
from cancelchain.wallet import Wallet


def init_app(app, register_browser=True):
    app.wallets = read_wallets(app)
    app.clients = create_clients(app)

    app.url_map.converters['address'] = AddressConverter
    app.url_map.converters['mill_hash'] = MillHashConverter
    app.url_map.converters['subject'] = SubjectConverter

    app.register_blueprint(api.blueprint, url_prefix='/api')
    if register_browser:
        app.register_blueprint(browser.blueprint, url_prefix='/')
    app.cli.add_command(command.init_db_command)
    app.cli.add_command(command.sync_blocks_command)
    app.cli.add_command(command.validate_chain_command)
    app.cli.add_command(command.export_blocks_command)
    app.cli.add_command(command.import_blocks_command)
    app.cli.add_command(command.mill_command)
    app.cli.add_command(command.txn_cli)
    app.cli.add_command(command.wallet_cli)
    app.cli.add_command(command.subject_cli)

    @app.context_processor
    def inject_cc_version():
        return {'cc_version': __version__}

    @app.template_filter('utc_datetime')
    def utc_datetime(value, fmt='%a %b %d %H:%M:%S %Z'):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        value = value.astimezone(datetime.timezone.utc)
        return value.strftime(fmt) if value is not None else None

    @app.template_filter('human_subject')
    def human_subject(value):
        return decode_subject(value) if value is not None else None


def read_wallets(app):
    walletdir = app.config.get('WALLET_DIR')
    wallets = {}
    if walletdir and os.path.isdir(walletdir):
        for dirpath, _, filenames in os.walk(walletdir):
            for filename in filenames:
                if filename.endswith('.pem'):
                    try:
                        w = Wallet.from_file(os.path.join(dirpath, filename))
                        wallets[w.address] = w
                    except Exception as e:
                        app.logger.error(
                            f'Error reading {os.path.join(dirpath, filename)}'
                        )
                        app.logger.exception(e)
    return wallets


def create_clients(app):
    clients = {}
    timeout = app.config.get('API_CLIENT_TIMEOUT')
    for peer in app.config.get('PEERS'):
        host, address = host_address(peer)
        if wallet := app.wallets.get(address):
            clients[peer] = ApiClient(peer, wallet, timeout=timeout)
        else:
            app.logger.warning(
                f'Peer client wallet {address} for {host} not found'
            )
    return clients


class AddressConverter(BaseConverter):
    def to_python(self, value):
        if not validate_address_format(value):
            raise ValidationError
        return value


class MillHashConverter(BaseConverter):
    def to_python(self, value):
        if len(value) != 64 or not validate_base64(value):
            raise ValidationError
        return value


class SubjectConverter(BaseConverter):
    def to_python(self, value):
        if not validate_subject(value):
            raise ValidationError
        return value
