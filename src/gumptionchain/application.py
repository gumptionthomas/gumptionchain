from __future__ import annotations

import contextlib
import datetime
import os
import weakref
from typing import Any

from flask import Flask, Response
from werkzeug.routing import BaseConverter, ValidationError

from gumptionchain import __version__, api, browser, command
from gumptionchain.api_client import ApiClient
from gumptionchain.chain import GRAIN_PER_GRIT
from gumptionchain.payload import decode_subject, validate_subject
from gumptionchain.schema import validate_address_format, validate_base64
from gumptionchain.signing_key import SigningKey
from gumptionchain.util import host_address


def close_clients(clients: dict[str, ApiClient]) -> None:
    """Close every ApiClient's wrapped httpx.Client. Swallows errors so a
    single bad client can't block shutdown of the others — logging is
    not guaranteed to work at process-exit / finalizer time.
    """
    for client in clients.values():
        with contextlib.suppress(Exception):
            client.close()


def init_app(
    app: Flask,
    register_browser: bool = True,  # noqa: FBT001
) -> None:
    app.signing_keys = read_signing_keys(app)  # type: ignore[attr-defined]
    app.clients = create_clients(app)  # type: ignore[attr-defined]
    # Close pooled httpx.Clients when the app is garbage-collected or
    # at process exit. Refcount-based collection fires promptly on the
    # last reference drop for acyclic objects; cycles defer to the gc
    # cycle collector, which runs eventually. weakref.finalize is
    # preferred over plain atexit.register so tests don't accumulate
    # one handler per app fixture for the life of the pytest process.
    weakref.finalize(app, close_clients, app.clients)  # type: ignore[attr-defined]

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
    app.cli.add_command(command.signing_key_cli)
    app.cli.add_command(command.subject_cli)

    @app.context_processor
    def inject_gc_version() -> dict[str, str]:
        return {'gc_version': __version__}

    @app.template_filter('utc_datetime')
    def utc_datetime(
        value: datetime.datetime | None, fmt: str = '%a %b %d %H:%M:%S %Z'
    ) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            value = value.replace(tzinfo=datetime.UTC)
        value = value.astimezone(datetime.UTC)
        return value.strftime(fmt)

    @app.template_filter('human_subject')
    def human_subject(value: str | None) -> str | None:
        return decode_subject(value) if value is not None else None

    @app.template_filter('grit')
    def grit(grains: int | None) -> str:
        # grains -> GRIT with a fixed 2 decimal places (1 GRIT = 100 grains).
        return f'{(grains or 0) / GRAIN_PER_GRIT:.2f}'

    @app.after_request
    def set_security_headers(response: Response) -> Response:
        # Response-hardening headers on every response (audit WEB1). The CSP
        # carries 'unsafe-inline' because the templates use inline onclick
        # handlers and style attributes (e.g. block.html:65,
        # transaction.html:56); a stricter policy would require refactoring
        # those out. XSS is already prevented by Jinja autoescape, so the CSP
        # here is defense-in-depth (it still pins source origins, frame
        # ancestors, base-uri, and object-src). HSTS is set unconditionally:
        # browsers ignore it over plain HTTP (RFC 6797) and honor it over
        # HTTPS, so it works even behind a TLS-terminating reverse proxy where
        # this app sees an http request (gating on request.is_secure would
        # silently drop HSTS in that common deployment). setdefault() never
        # overrides a header a view already set.
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            'https://code.jquery.com; '
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            'https://fonts.googleapis.com; '
            "font-src 'self' https://cdn.jsdelivr.net "
            'https://fonts.gstatic.com; '
            "img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
        )
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains',
        )
        return response


def read_signing_keys(app: Flask) -> dict[str, SigningKey]:
    signing_keys: dict[str, SigningKey] = {}
    # Inline single-key identity (env GC_SIGNING_KEY) wins over the *.pem dir:
    # cloud platforms inject secrets as env vars, not files. Only the derived
    # address is logged — never the secret. A malformed secret logs and falls
    # back to the dir scan rather than crashing startup.
    inline_secret = app.config.get('SIGNING_KEY')
    if inline_secret:
        try:
            key = SigningKey.from_secret(inline_secret)
        except Exception as e:
            app.logger.error('Error loading inline GC_SIGNING_KEY')
            app.logger.exception(e)
        else:
            app.logger.info(
                f'Loaded node signing key {key.address} from GC_SIGNING_KEY'
            )
            return {key.address: key}
    signing_keydir = app.config.get('SIGNING_KEY_DIR')
    if signing_keydir and os.path.isdir(signing_keydir):
        for dirpath, _, filenames in os.walk(signing_keydir):
            for filename in filenames:
                if filename.endswith('.pem'):
                    try:
                        w = SigningKey.from_file(
                            os.path.join(dirpath, filename)
                        )
                        signing_keys[w.address] = w
                    except Exception as e:
                        app.logger.error(
                            f'Error reading {os.path.join(dirpath, filename)}'
                        )
                        app.logger.exception(e)
    return signing_keys


def create_clients(app: Flask) -> dict[str, ApiClient]:
    clients: dict[str, ApiClient] = {}
    timeout: Any = app.config.get('API_CLIENT_TIMEOUT')
    for peer in app.config.get('PEERS') or []:
        host, address = host_address(peer)
        if signing_key := app.signing_keys.get(address):  # type: ignore[attr-defined]
            clients[peer] = ApiClient(peer, signing_key, timeout=timeout)
        else:
            app.logger.warning(
                f'Peer client signing_key {address} for {host} not found'
            )
    return clients


class AddressConverter(BaseConverter):
    def to_python(self, value: str) -> str:
        if not validate_address_format(value):
            raise ValidationError
        return value


class MillHashConverter(BaseConverter):
    def to_python(self, value: str) -> str:
        if len(value) != 64 or not validate_base64(value):
            raise ValidationError
        return value


class SubjectConverter(BaseConverter):
    def to_python(self, value: str) -> str:
        if not validate_subject(value):
            raise ValidationError
        return value
