from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SIGNING_KEY_DIR = _ROOT / 'src/gumptionchain/static/sdk'
SOURCE_DIR = _ROOT / 'clients/sdk'
REQUIRED = [
    'gc-attestation.mjs',
    'gc-message.mjs',
    'gc-errors.mjs',
    'gc-signing-key.mjs',
    'gc-crypto.mjs',
    'gc-sig.mjs',
    'index.mjs',
]


def test_runtime_signing_key_modules_are_vendored():
    for name in REQUIRED:
        assert (SIGNING_KEY_DIR / name).is_file(), f'missing vendored {name}'


def test_no_test_or_cli_modules_vendored():
    for p in SIGNING_KEY_DIR.glob('*.mjs'):
        assert not p.name.endswith('.test.mjs')
        assert not p.name.endswith('-cli.mjs')


def _runtime_modules(directory: Path) -> list[Path]:
    return [
        p
        for p in directory.glob('*.mjs')
        if not p.name.endswith('.test.mjs') and not p.name.endswith('-cli.mjs')
    ]


def test_vendored_modules_match_source():
    # The served copies are vendored from clients/sdk via
    # scripts/sync_sdk.py. Guard against an unsynced source edit:
    # a stale served copy of parity-critical crypto (gc-transaction.mjs) would
    # silently produce wrong txids. Mirrors sync_sdk's copy rule.
    for src in _runtime_modules(SOURCE_DIR):
        vendored = SIGNING_KEY_DIR / src.name
        assert vendored.is_file(), (
            f'{src.name} not vendored — run scripts/sync_sdk.py'
        )
        assert vendored.read_bytes() == src.read_bytes(), (
            f'{src.name} drifted from clients/sdk — run scripts/sync_sdk.py'
        )
