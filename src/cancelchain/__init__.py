# Copyright 2023 Thomas Bohmbach, Jr.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from importlib.metadata import version as _pkg_version

import click
from flask import Flask
from flask.cli import FlaskGroup

__version__ = _pkg_version('cancelchain')


def create_app(app=None, config_map=None, register_browser=True):
    from .application import init_app
    from .cache import cache
    from .config import EnvAppSettings
    from .database import db
    from .tasks import init_tasks

    app = app or Flask(__name__)

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['CACHE_TYPE'] = 'NullCache'

    app.config.from_prefixed_env()
    app.config.from_object(EnvAppSettings.from_env())
    app.config.from_envvar('CANCELCHAIN_SETTINGS', silent=True)
    if config_map:
        app.config.from_mapping(config_map)

    init_app(app, register_browser=register_browser)

    try:
        db.init_app(app)
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
    def make_shell_context():
        return {'app': app, 'db': db}

    return app


@click.version_option(package_name='cancelchain')
@click.group(cls=FlaskGroup, create_app=create_app, add_version_option=False)
def cli():
    pass
