from gumptionchain.util import host_address


def test_host_address(wallet):
    uri = f'https://{wallet.address}@magrathea.com:5000'
    assert host_address(uri) == ('https://magrathea.com:5000', wallet.address)
