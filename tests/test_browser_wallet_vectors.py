import json
import os
from pathlib import Path

from gumptionchain.signing import _canonical
from gumptionchain.wallet import Wallet

# A fixed, freshly-minted 2048-bit RSA wallet used solely to generate the
# golden gc-sig vectors. Distinct from conftest's WALLET_PRIVATE_KEY_B58.
VECTOR_WALLET_B58 = (
    'riiewRJm2wpE3rWTs1ikUc83so8ZXMX8vp9dUTnRgMC8GyfLr99LtkqK8gDNAd1VyihbjW'
    'opge9tGR3W9GCbpojrHtjpUqmmuEhHjpehtXGiMF1LqiZedaEE4V3X8X679uzitLTP3m8A'
    'zcNGgqbPKWtVEd7VWmVWQdan68EpojZFwqrY3LFLiRnaKSLoNryxLUEFFrxcBNjyTpjL94'
    '3Rs6YXFRgBs5UDSazh6GLPwu26oAPfLy8NNPqKjPuNHcCXoywopkMUug4bXJaKc93PxdtR'
    '6kEWWxCaiNtsCe4JkukbRXNR6NpUrAtrgNgw8uM7Pg2tMP9ThbyaHxHLcaeYs8tfdPs2MX'
    '3qfkw37cPX6P5wkFUVqcZhrL4PMzqHF2Hkf79hWYU1c3xwit8Rx2gM8HAQZgq5CGuNjj9Y'
    'Ao3JcAbkVKtTqgFKJqmwY8jVLCtxKkzyBbxAAyuHtyioNQ5LFrxBs5qzoyRATTJwqVfhjo'
    'WNEvHNpT3g97htCjtkgMssbfSBq6fmSa7mFpE3vA7dHPUhmjdiwhsdDhDVLnekVk8yxELP'
    'EZzjCvKKZ1JW4oaXVcGvgkjFB2RquXCUFX272SYie7b2sEDbB61kC7rTxmrfanLX5Ah2X4'
    'PKZK9xCaUKhEcbQsMBMve9uniasFMYecu829v9ELWRaRCjhMDKqnHhsPvvd7xMdLyUcTyC'
    'Pcbwphc76GhyULAvpqumAoxvsQqXTcWmh4AtFzjjURneezkVa1sA1yfCEvfyEjPynpYv7X'
    'WtymfciJ8NNS1SpuXBUrqdFSps2txaVD2F6JBwZE2VBA2SnVdd57cn5z7bcejheQB99mSC'
    '69UFqR2vgPQek1VGHEQFsmtQXBWR3jrCeiSKsvW4SLESB3W7vDvhN1es5mjBnQ1rExmpDP'
    'HQTAacJSVt1eiijvq8GccQcNsbLgHsJqXNBquvffYgWXTv3jY7kcZj7iCK1wjpLujhCzGX'
    'J8mzTn7h6hu7ouAr3J54317eC4XeyZo7AQgiVSK7YrEyxw61fbsFdqMFMKi3ChmxfckTUk'
    '3gNrTK8bXyhNrWpGN4masjADciLRYS7UD5M96nquMJop8hyftXEd2vmJVwqnzsorbePGu7'
    'iAdFh26jKfYtDS1rNycLVCxKYR7nB9o6xDPVvEqnnTUUDrPNKf9wE8EbaHoysTeh5FqK7d'
    'HupxRJCNp4h46dUnQpUCGtp5QzEZfRa6MLMoecPQ5icwfMMZuzraLpbLfSVdniZyuomZwd'
    'W5rPDSqeZwaaWRTBizLaeeJKsfbb4gU27THHzCSxtd8S1pn4obmD5UHLXzBgi5nBCWuwmD'
    'duz8mVm2ytQnTYGkAq6LJig8eGtYFtXMzhWuHUfEsj4o6k6rP1hLvVr7bhpp8r59a6m9Zi'
    'tUCsCiEKxJC1b1TAy2EGLHoC6bwyUXUQe69dPTR981QsuW2CNmUrgtCduFfuGhqwvavxpP'
    'NKPXuWVnuYYE2nzVNHZ7Bh2cicnQGF48iFnptWTWN7Nj6v8w6bHE4j9iosu13skYeN63y2'
    'j4swwegX36KuEEfbutTBLJ7hq9GpSAqMgACbMKKEVhE7Zz3uzDTEFWHFGobWUYT8hzA9iy'
    'fyhVv74B6CX4DS3biGzgPeBK81x6Yn83NSxXCh97wWTd1Q4ewdpwk'
)
VECTORS_PATH = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'wallet'
    / 'testdata'
    / 'gc-sig-vectors.json'
)

_CASES = [
    {
        'method': 'GET',
        'path': '/api/blocks',
        'query': '',
        'body': '',
        'node_host': 'node.example',
        'timestamp': '1700000000',
    },
    {
        'method': 'POST',
        'path': '/api/transactions',
        'query': 'foo=bar',
        'body': '{"hello":"world"}',
        'node_host': 'node.example',
        'timestamp': '1700000001',
    },
]


def _build_vectors() -> dict[str, object]:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    cases = []
    for c in _CASES:
        canonical = _canonical(
            method=c['method'],
            path=c['path'],
            query=c['query'],
            body=c['body'].encode(),
            node_host=c['node_host'],
            timestamp=c['timestamp'],
            address=w.address,
        )
        cases.append(
            {
                **c,
                'canonical': canonical.decode(),
                'signature': w.sign(canonical),
            }
        )
    return {
        'private_key_b58': VECTOR_WALLET_B58,
        'public_key_b64': w.public_key_b64,
        'address': w.address,
        'cases': cases,
    }


def test_vectors_committed_and_self_consistent() -> None:
    fresh = _build_vectors()
    # Default run is READ-ONLY: fail loud if the committed oracle is missing,
    # rather than silently regenerating (which would let an accidental deletion
    # pass CI). Regenerate intentionally with GC_REGEN_VECTORS=1.
    if os.environ.get('GC_REGEN_VECTORS'):
        VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        VECTORS_PATH.write_text(json.dumps(fresh, indent=2) + '\n')
    assert VECTORS_PATH.exists(), (
        'gc-sig-vectors.json missing; regenerate with GC_REGEN_VECTORS=1'
    )
    committed = json.loads(VECTORS_PATH.read_text())
    assert committed == fresh, 'gc-sig-vectors.json drifted; regenerate'

    w = Wallet(b58ks=committed['private_key_b58'])
    for case in committed['cases']:
        assert w.validate_signature(
            case['canonical'].encode(), case['signature']
        )
