from __future__ import annotations

# mypy: disable-error-code="import-untyped,no-untyped-def,name-defined,misc"
# mypy: disable-error-code="untyped-decorator"
from typing import Any

from celery import Celery
from flask import Flask, current_app

from cancelchain.api_client import PEER_HOST_HEADER, ApiClient

celery = Celery(__name__)


def init_tasks(app: Flask) -> Celery:
    # Bounded publish defaults (audit N4): on a down/slow broker, the
    # synchronous post_process.delay() publish must fail fast (~2s) rather
    # than stalling the web-request thread ~16s on Celery's default
    # publish-retry policy. Applied before app.config so an operator can
    # still override.
    celery.conf.update(
        task_publish_retry=False,
        broker_connection_timeout=2.0,
        broker_connection_max_retries=0,
    )
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


@celery.task()
def post_process(
    host: str,
    address: str,
    path: str,
    data: str | bytes | None = None,
    vhosts: list[str] | None = None,
) -> None:
    wallet = current_app.wallets.get(address)  # type: ignore[attr-defined]
    if wallet is None:
        current_app.logger.warning(
            'post_process: no wallet for address %s; '
            'dropping post-processing of %s',
            address,
            path,
        )
        return
    headers = {PEER_HOST_HEADER: ','.join(vhosts)} if vhosts else None
    with ApiClient(host, wallet) as c:
        c.post(path, data=data, headers=headers)
