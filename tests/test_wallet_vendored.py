from pathlib import Path

WALLET_DIR = (
    Path(__file__).resolve().parent.parent / 'src/gumptionchain/static/wallet'
)
REQUIRED = [
    'gc-attestation.mjs',
    'gc-message.mjs',
    'gc-errors.mjs',
    'gc-wallet.mjs',
    'gc-crypto.mjs',
    'gc-sig.mjs',
    'index.mjs',
]


def test_runtime_wallet_modules_are_vendored():
    for name in REQUIRED:
        assert (WALLET_DIR / name).is_file(), f'missing vendored {name}'


def test_no_test_or_cli_modules_vendored():
    for p in WALLET_DIR.glob('*.mjs'):
        assert not p.name.endswith('.test.mjs')
        assert not p.name.endswith('-cli.mjs')
