import datetime
import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch

from gumptionchain.application import create_clients
from gumptionchain.block import Block
from gumptionchain.chain import GRAIN_PER_GRIT, REWARD
from gumptionchain.database import db
from gumptionchain.miller import Miller
from gumptionchain.node import Node
from gumptionchain.util import now
from gumptionchain.wallet import Wallet

REWARD_GRIT = int(REWARD / GRAIN_PER_GRIT)
SUBJECT_GRIT = 2


def get_wallet_file(address, walletdir=None, app=None):
    if walletdir is None and app is not None:
        walletdir = app.config.get('WALLET_DIR')
    fn = f'{address}.pem'
    if walletdir:
        fn = os.path.join(walletdir, fn)
    return fn


def test_init(app, runner):
    # The `app` fixture pre-creates tables via db.create_all(); drop them
    # first so the `init` command (now backed by flask_migrate.upgrade())
    # runs against an empty DB, matching the prod-shape contract.
    with app.app_context():
        db.drop_all()
        result = runner.invoke(args=['init'])
        assert 'Initialized the database.' in result.output


def _mill_peer_chain(remote_app, miller_2_wallet, count, time_stepper):
    """Mill `count` blocks on remote_app's chain, returning the tip."""
    time_step = time_stepper(start=now() - datetime.timedelta(hours=2))
    with remote_app.app_context():
        m = Miller(milling_wallet=miller_2_wallet)
        last = None
        for _ in range(count):
            next(time_step)
            b = m.create_block()
            m.mill_block(b)
            last = b
        return last


def _rebuild_local_clients_to_peer(app):
    """Under the active remote_requests_proxy patch, rebuild app.clients so
    the local node's peer ApiClient routes into remote_app (the patched
    _make_client ignores base_url and hits remote_app)."""
    for c in list(app.clients.values()):
        c.close()
    app.clients = create_clients(app)


def test_sync_catches_up_to_peer_ahead(
    app,
    remote_app,
    remote_requests_proxy,
    runner,
    miller_2_wallet,
    time_machine,
    time_stepper,
):
    """`sync` forward-syncs the local node up to a peer that is ahead."""
    tip = _mill_peer_chain(remote_app, miller_2_wallet, 4, time_stepper)
    assert tip.idx == 3
    with app.app_context():
        _rebuild_local_clients_to_peer(app)
        node = Node(
            host=app.config['NODE_HOST'],
            peers=app.config['PEERS'],
            clients=app.clients,
            logger=app.logger,
        )
        assert node.longest_chain is None
        runner.invoke(args=['sync'])
        lc = node.longest_chain
        assert lc is not None and lc.last_block is not None
        assert lc.last_block.idx == 3


def test_sync_noop_when_peer_not_ahead(
    app,
    remote_app,
    remote_requests_proxy,
    runner,
    miller_2_wallet,
    wallet,
    time_machine,
    time_stepper,
):
    """A peer no further ahead than the local tip is a no-op: sync_forward
    is never invoked for that peer."""
    # Peer has a 2-block chain (tip idx 1).
    _mill_peer_chain(remote_app, miller_2_wallet, 2, time_stepper)
    with app.app_context():
        # Local node already has a chain whose tip idx (1) >= peer tip idx.
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        next(time_stepper(start=now() + datetime.timedelta(seconds=120)))
        b1 = m.create_block()
        m.mill_block(b1)
        _rebuild_local_clients_to_peer(app)
        node = Node(
            host=app.config['NODE_HOST'],
            peers=app.config['PEERS'],
            clients=app.clients,
            logger=app.logger,
        )
        local_tip = node.longest_chain.last_block.block_hash
        with patch.object(Node, 'sync_forward') as spy:
            runner.invoke(args=['sync'])
            spy.assert_not_called()
        # Local tip unchanged.
        assert node.longest_chain.last_block.block_hash == local_tip


def test_sync_miller_path_still_uses_fill_chain(
    app, mill_block, wallet, time_machine
):
    """Regression guard: Miller.poll_latest_blocks (the gossip short-fill
    path) still delegates to fill_chain — NOT sync_forward — for a
    peer-block not yet in the DB."""
    with app.app_context():
        now_dt = now()
        time_machine.move_to(now_dt - datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)
        g = m.create_block()
        m.mill_block(g)

        # A peer-block whose from_db is forced to None below, so
        # poll_latest_blocks takes the fill_chain (gossip short-fill) branch.
        time_machine.move_to(now_dt)
        peer = 'http://peer.host:8000'
        fresh = Block.from_dict(g.to_dict())
        assert fresh.block_hash is not None

        with (
            patch.object(
                Miller,
                'request_latest_blocks',
                return_value=iter([(fresh, peer)]),
            ),
            patch.object(Block, 'from_db', return_value=None),
            patch.object(Miller, 'send_block'),
            patch.object(Miller, 'fill_chain') as fill_spy,
            patch.object(Miller, 'sync_forward') as fwd_spy,
        ):
            m.poll_latest_blocks()
        fill_spy.assert_called_once()
        fwd_spy.assert_not_called()


def test_validate(app, mill_block, runner, wallet):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['validate'])
        assert '100%' in result.output


def test_export_import(app, mill_block, runner, wallet):
    with app.app_context():
        mill_block(wallet)
        with NamedTemporaryFile(suffix='.jsonl') as f:
            result = runner.invoke(args=['export', f.name])
            assert '100%' in result.output
            result = runner.invoke(args=['import', f.name])
            assert '100%' in result.output
            result = runner.invoke(args=['export', f.name])
            assert '100%' in result.output


def run_txn_transfer(
    runner, from_wallet, to_wallet, from_wallet_file, confirm=True
):
    return runner.invoke(
        args=[
            'txn',
            'transfer',
            from_wallet.address,
            '2',
            to_wallet.address,
            '--txn-wallet',
            from_wallet_file,
        ],
        input='Y' if confirm else 'n',
    )


def test_transfer(app, mill_block, runner, requests_proxy, wallet):
    with app.app_context():
        from_wallet = Wallet()
        fwf = from_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        to_wallet = Wallet()
        m, _ = mill_block(from_wallet)
        result = run_txn_transfer(
            runner, from_wallet, to_wallet, fwf, confirm=False
        )
        assert 'Transfer aborted.' in result.output
        assert len(m.pending_txns) == 0
        result = run_txn_transfer(runner, from_wallet, to_wallet, fwf)
        assert 'Transfer created.' in result.output
        assert len(m.pending_txns) == 1


def test_invalid_transfer(app, mill_block, runner, requests_proxy, wallet):
    with app.app_context():
        from_wallet = Wallet()
        fwf = from_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        to_wallet = Wallet()
        m, _ = mill_block(wallet)
        result = run_txn_transfer(runner, from_wallet, to_wallet, fwf)
        assert 'Transfer failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def run_txn_opposition(
    runner, subject, txn_wallet, txn_wallet_file, confirm=True
):
    return runner.invoke(
        args=[
            'txn',
            'opposition',
            txn_wallet.address,
            str(SUBJECT_GRIT),
            subject,
            '--txn-wallet',
            txn_wallet_file,
        ],
        input='Y' if confirm else 'n',
    )


def test_opposition(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        result = run_txn_opposition(
            runner, subject_raw, txn_wallet, txnwf, confirm=False
        )
        assert 'Opposition aborted' in result.output
        assert len(m.pending_txns) == 0
        result = run_txn_opposition(runner, subject_raw, txn_wallet, txnwf)
        assert 'Opposition created' in result.output
        assert len(m.pending_txns) == 1


def test_invalid_opposition(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(wallet)
        result = run_txn_opposition(runner, subject_raw, txn_wallet, txnwf)
        assert 'Opposition failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def test_empty_chain(app, runner, requests_proxy, subject_raw, wallet):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        result = run_txn_opposition(runner, subject_raw, txn_wallet, txnwf)
        assert 'Opposition failed: EmptyChainError' in result.output


def run_txn_rescind(
    runner,
    subject,
    txn_wallet,
    txn_wallet_file,
    confirm=True,
    kind='opposition',
):
    return runner.invoke(
        args=[
            'txn',
            'rescind',
            txn_wallet.address,
            str(SUBJECT_GRIT),
            subject,
            '--kind',
            kind,
            '--txn-wallet',
            txn_wallet_file,
        ],
        input='Y' if confirm else 'n',
    )


def test_rescind(
    app, mill_block, runner, requests_proxy, subject_raw, time_stepper, wallet
):
    with app.app_context():
        time_step = time_stepper()
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        _ = next(time_step)
        result = run_txn_opposition(runner, subject_raw, txn_wallet, txnwf)
        assert len(m.pending_txns) == 1
        m, _ = mill_block(txn_wallet)
        result = run_txn_rescind(
            runner, subject_raw, txn_wallet, txnwf, confirm=False
        )
        assert 'Rescind aborted' in result.output
        assert len(m.pending_txns) == 1
        result = run_txn_rescind(runner, subject_raw, txn_wallet, txnwf)
        assert 'Rescind created' in result.output
        assert len(m.pending_txns) == 2


def test_invalid_rescind(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        result = run_txn_rescind(runner, subject_raw, txn_wallet, txnwf)
        assert 'Rescind failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def run_txn_support(runner, subject, txn_wallet, txn_wallet_file, confirm=True):
    return runner.invoke(
        args=[
            'txn',
            'support',
            txn_wallet.address,
            str(SUBJECT_GRIT),
            subject,
            '--txn-wallet',
            txn_wallet_file,
        ],
        input='Y' if confirm else 'n',
    )


def test_support(app, mill_block, runner, requests_proxy, subject_raw, wallet):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        result = run_txn_support(
            runner, subject_raw, txn_wallet, txnwf, confirm=False
        )
        assert 'Support aborted' in result.output
        assert len(m.pending_txns) == 0
        result = run_txn_support(runner, subject_raw, txn_wallet, txnwf)
        assert 'Support created' in result.output
        assert len(m.pending_txns) == 1


def test_invalid_support(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(wallet)
        result = run_txn_support(runner, subject_raw, txn_wallet, txnwf)
        assert 'Support failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def test_rescind_support_kind(
    app,
    mill_block,
    runner,
    requests_proxy,
    subject_raw,
    time_stepper,
    wallet,
):
    """txn rescind --kind support creates a support rescind transaction."""
    with app.app_context():
        time_step = time_stepper()
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        _ = next(time_step)
        result = run_txn_support(runner, subject_raw, txn_wallet, txnwf)
        assert 'Support created' in result.output
        assert len(m.pending_txns) == 1
        m, _ = mill_block(txn_wallet)
        result = run_txn_rescind(
            runner,
            subject_raw,
            txn_wallet,
            txnwf,
            confirm=False,
            kind='support',
        )
        assert 'Rescind aborted' in result.output
        assert len(m.pending_txns) == 1
        result = run_txn_rescind(
            runner, subject_raw, txn_wallet, txnwf, kind='support'
        )
        assert 'Rescind created' in result.output
        assert len(m.pending_txns) == 2


def test_create_wallet(app, runner):
    with app.app_context(), TemporaryDirectory() as walletdir:
        result = runner.invoke(
            args=['wallet', 'create', '--walletdir', walletdir]
        )
        assert result.exit_code == 0
        assert 'Created' in result.output
        pem_files = list(Path(walletdir).glob('*.pem'))
        assert len(pem_files) == 1
        assert Wallet.from_file(str(pem_files[0])) is not None


def test_wallet_balance(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['wallet', 'balance', wallet.address])
        assert f'{REWARD_GRIT} GRIT' in result.output
        wf = get_wallet_file(wallet.address, app=app)
        run_txn_opposition(runner, subject_raw, wallet, wf)
        w = Wallet()
        mill_block(w)
        result = runner.invoke(args=['wallet', 'balance', wallet.address])
        assert f'{REWARD_GRIT - SUBJECT_GRIT} GRIT' in result.output
        to_wallet = Wallet()
        run_txn_transfer(runner, wallet, to_wallet, wf)
        mill_block(w)
        result = runner.invoke(args=['wallet', 'balance', wallet.address])
        assert f'{REWARD_GRIT - 2 * SUBJECT_GRIT} GRIT' in result.output
        result = runner.invoke(args=['wallet', 'balance', to_wallet.address])
        assert f'{SUBJECT_GRIT} GRIT' in result.output
        result = runner.invoke(args=['wallet', 'balance', w.address])
        expected = int(2 * REWARD_GRIT + 0.5 * SUBJECT_GRIT)
        assert f'{expected} GRIT' in result.output
        result = runner.invoke(args=['wallet', 'balance', 'foo'])
        assert 'Not Found' in result.output


def test_subject_opposition(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'opposition', subject_raw])
        assert '0 GRIT' in result.output
        wf = get_wallet_file(wallet.address, app=app)
        run_txn_opposition(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'opposition', subject_raw])
        assert f'{SUBJECT_GRIT} GRIT' in result.output
        run_txn_opposition(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'opposition', subject_raw])
        assert f'{2 * SUBJECT_GRIT} GRIT' in result.output


def test_subject_support(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'support', subject_raw])
        assert '0 GRIT' in result.output
        wf = get_wallet_file(wallet.address, app=app)
        run_txn_support(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'support', subject_raw])
        assert f'{SUBJECT_GRIT} GRIT' in result.output
        run_txn_support(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'support', subject_raw])
        assert f'{2 * SUBJECT_GRIT} GRIT' in result.output


def test_mill(app, runner, wallet):
    with app.app_context():
        result = runner.invoke(args=['mill', wallet.address, '--blocks', 2])
        assert 'GENESIS' in result.output
        assert 'Block │ 0' in result.output
        assert 'Block │ 1' in result.output
        result = runner.invoke(args=['mill', wallet.address, '--blocks', 2])
        assert 'Block │ 2' in result.output
        assert 'Block │ 3' in result.output
