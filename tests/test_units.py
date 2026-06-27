import subprocess
import sys
from decimal import Decimal

import pytest

import gumptionchain
from gumptionchain.units import (
    GRAIN_PER_GRIT,
    grains_to_grit,
    grit_to_grains,
)


def test_grain_per_grit_is_100():
    assert GRAIN_PER_GRIT == 100


@pytest.mark.parametrize(
    ('grit', 'grains'),
    [
        (1, 100),
        ('1', 100),
        (5, 500),
        (0, 0),
        ('0.07', 7),  # exact via Decimal — float 0.07*100 != 7 exactly
        (Decimal('0.01'), 1),
        (1.5, 150),
        ('12.34', 1234),
    ],
)
def test_grit_to_grains_exact(grit, grains):
    assert grit_to_grains(grit) == grains


def test_grit_to_grains_rejects_sub_grain_precision():
    # Finer than one grain (0.01) must fail loud, not silently truncate.
    with pytest.raises(ValueError, match='precision'):
        grit_to_grains('0.001')


def test_grit_to_grains_rejects_non_numeric():
    with pytest.raises(ValueError, match='not a valid GRIT amount'):
        grit_to_grains('not-a-number')


@pytest.mark.parametrize(
    ('grains', 'grit'),
    [(100, '1'), (7, '0.07'), (0, '0'), (1, '0.01'), (1234, '12.34')],
)
def test_grains_to_grit_exact(grains, grit):
    assert grains_to_grit(grains) == Decimal(grit)


def test_round_trip():
    for grains in (0, 1, 7, 100, 12345):
        assert grit_to_grains(grains_to_grit(grains)) == grains


def test_lightweight_surface_does_not_import_db_or_flask():
    # The whole point of the lightweight entry point (egu-354): a member app can
    # import the constants + client crypto + GRIT helper at module top WITHOUT
    # transitively dragging in the node DB / Flask layer. Run in a fresh
    # subprocess because the pytest process already has flask/sqlalchemy loaded.
    code = (
        'import sys\n'
        'import gumptionchain\n'
        'import gumptionchain.units\n'
        'import gumptionchain.signing_key\n'
        'from gumptionchain.units import grit_to_grains, GRAIN_PER_GRIT\n'
        'from gumptionchain.signing_key import SigningKey\n'
        "heavy = [m for m in ('sqlalchemy', 'flask', 'flask_migrate') "
        'if m in sys.modules]\n'
        "print(','.join(heavy))\n"
    )
    result = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, '-c', code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == '', (
        f'lightweight import transitively loaded: {result.stdout.strip()!r}'
    )


def test_lazy_init_still_exposes_create_app_cli_and_blueprints():
    # PEP 562 lazy __getattr__ must keep the public surface importable.
    assert callable(gumptionchain.create_app)
    assert gumptionchain.cli is not None
    assert callable(gumptionchain.node_proxy_blueprint)
    assert callable(gumptionchain.static_assets_blueprint)
    with pytest.raises(AttributeError):
        _ = gumptionchain.does_not_exist
