import os

from gumptionchain.config import EnvAppSettings


def test_environ_settings():
    s = EnvAppSettings.from_env()
    assert s.READER_ADDRESSES == [
        'NpT1dBMEe5eEmYnoX3JsGkwek86WYhrw2RgMqYsui8S7AHzgE',
        '2oPHNGj7eBXopvfoaZZxQxJmk6TkAWAWukFoh19xUnMqjeKP8t',
    ]


def test_flask_config(config_app):
    # `config_app` builds `create_app()` with no overrides, so
    # `SECRET_KEY` should reflect whatever `Flask.config.from_prefixed_env`
    # loaded from `FLASK_SECRET_KEY` (set in `tests/.test.env`).
    assert config_app.config.get('SECRET_KEY') == os.environ['FLASK_SECRET_KEY']


def test_max_pending_per_transactor_default(app):
    assert app.config['MAX_PENDING_PER_TRANSACTOR'] == 100
