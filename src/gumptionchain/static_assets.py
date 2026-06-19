from __future__ import annotations

from importlib.resources import files

from flask import Blueprint


def static_assets_blueprint(
    url_path: str = '/static/gumptionchain',
    name: str = 'gumptionchain_static',
) -> Blueprint:
    """A static-only blueprint serving base's browser assets (the SDK
    ESM modules + JS glue) for consumers that embed the ``gumptionchain``
    package but do NOT register the full ``browser`` blueprint (chain explorer
    + DB).

    The default ``url_path`` matches the ``browser`` blueprint's, so module
    URLs are identical whether a consumer mounts the explorer or only assets.
    """
    static_folder = str(files('gumptionchain') / 'static')
    return Blueprint(
        name,
        __name__,
        static_folder=static_folder,
        static_url_path=url_path,
    )
