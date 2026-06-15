# Copyright 2023 Thomas Bohmbach, Jr.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from __future__ import annotations

from importlib.metadata import version as _pkg_version
from pathlib import Path as _Path
from typing import Any

import click
from flask import Flask
from flask.cli import FlaskGroup
from flask_migrate import Migrate

from gumptionchain.static_assets import (
    static_assets_blueprint as static_assets_blueprint,
)

__version__ = _pkg_version('gumptionchain')

# Package-relative migrations path so `gumptionchain init` / `gumptionchain db
# upgrade` work in any CWD (pip-installed wheel, container, dev). Computed
# from __file__ at import time rather than left to Flask-Migrate's default
# `directory='migrations'` (which resolves against the process CWD).
_MIGRATIONS_DIR = str(_Path(__file__).parent / 'migrations')


def create_app(
    app: Flask | None = None,
    config_map: dict[str, Any] | None = None,
    register_browser: bool = True,  # noqa: FBT001
) -> Flask:
    from .application import (  # noqa: PLC0415 — circular: application imports gumptionchain
        init_app,
    )
    from .cache import (  # noqa: PLC0415 — deferred alongside application for consistency
        cache,
    )
    from .config import (  # noqa: PLC0415 — deferred alongside application for consistency
        EnvAppSettings,
    )
    from .database import (  # noqa: PLC0415 — deferred alongside application for consistency
        db,
    )
    from .tasks import (  # noqa: PLC0415 — deferred alongside application for consistency
        init_tasks,
    )

    app = app or Flask(__name__)

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['CACHE_TYPE'] = 'NullCache'

    app.config.from_prefixed_env()
    app.config.from_object(EnvAppSettings.from_env())
    app.config.from_envvar('GUMPTIONCHAIN_SETTINGS', silent=True)
    if config_map is not None:
        app.config.from_mapping(config_map)

    from .api import Role  # noqa: PLC0415 — deferred (api imports app modules)

    Role.validate_config(app.config)

    init_app(app, register_browser=register_browser)

    try:
        db.init_app(app)
        Migrate(app, db, directory=_MIGRATIONS_DIR)
    except RuntimeError as e:
        app.logger.error(e)

    try:
        cache.init_app(app)
    except Exception as e:
        app.logger.error(e)

    try:
        init_tasks(app)
    except Exception as e:
        app.logger.error(e)

    @app.shell_context_processor
    def make_shell_context() -> dict[str, Any]:
        return {'app': app, 'db': db}

    return app


@click.version_option(package_name='gumptionchain')
@click.group(cls=FlaskGroup, create_app=create_app, add_version_option=False)
def cli() -> None:
    pass
