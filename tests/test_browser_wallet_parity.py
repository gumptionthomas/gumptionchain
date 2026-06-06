import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.signing import _canonical
from gumptionchain.wallet import Wallet

CLI = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'wallet'
    / 'sign-cli.mjs'
)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_js_signature_verifies_in_python() -> None:
    req = {
        'private_key_b58': VECTOR_WALLET_B58,
        'method': 'POST',
        'path': '/api/transactions',
        'query': 'a=1',
        'body': '{"x":1}',
        'node_host': 'node.example',
        'timestamp': '1700000002',
    }
    out = subprocess.run(  # noqa: S603
        ['node', str(CLI), json.dumps(req)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(out.stdout)

    w = Wallet(b58ks=VECTOR_WALLET_B58)
    assert result['address'] == w.address
    canonical = _canonical(
        method=req['method'],
        path=req['path'],
        query=req['query'],
        body=req['body'].encode(),
        node_host=req['node_host'],
        timestamp=req['timestamp'],
        address=w.address,
    )
    assert w.validate_signature(canonical, result['signature'])
