from __future__ import annotations

# mypy: disable-error-code="import-untyped,no-untyped-def,name-defined,misc"
# mypy: disable-error-code="untyped-decorator"
from typing import Any

import httpx
from celery import Celery
from flask import Flask

celery = Celery(__name__)


def init_tasks(app: Flask) -> Celery:
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


@celery.task()
def post_process(
    url: str, data: str | bytes | None, headers: dict[str, str] | None = None
) -> None:
    r = httpx.post(url, headers=headers, content=data, timeout=360)
    r.raise_for_status()
