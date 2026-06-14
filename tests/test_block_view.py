from gumptionchain.database import db
from gumptionchain.models import BlockDAO


def _block_at(idx):
    return db.session.scalar(db.select(BlockDAO).where(BlockDAO.idx == idx))


def test_genesis_prev_hash_is_not_a_dead_link(
    app, host, mill_block, requests_proxy, signing_key
):
    # The genesis block's prev_hash is the GENESIS_HASH sentinel, which has no
    # block row — it must render unlinked (no dead /block/<sentinel> link).
    with app.app_context():
        mill_block(signing_key)
        mill_block(signing_key)
        genesis = _block_at(0)
        page = (
            app.test_client()
            .get(f'/block/{genesis.block_hash}')
            .get_data(as_text=True)
        )
        assert f'/block/{genesis.prev_hash}' not in page  # no dead link
        assert '(genesis)' in page
        # breadcrumb back to the Blocks index
        assert '&larr; Blocks' in page


def test_non_genesis_prev_hash_links_to_parent(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        mill_block(signing_key)
        genesis = _block_at(0)
        block1 = _block_at(1)
        page = (
            app.test_client()
            .get(f'/block/{block1.block_hash}')
            .get_data(as_text=True)
        )
        # block 1's previous-block link resolves to the genesis block
        assert f'/block/{genesis.block_hash}' in page
