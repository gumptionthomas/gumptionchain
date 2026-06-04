import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from gumptionchain.chain import GRAIN_PER_GRIT, REWARD
from gumptionchain.database import db
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


# def test_sync(
#     app, remote_app, remote_requests_proxy, runner, remote_chain
# ):
#     with app.app_context():
#         from gumptionchain.miller import Miller
#         m = Miller(milling_wallet=Wallet())
#         assert m.longest_chain is None
#     with remote_app.app_context():
#         result = runner.invoke(args=['sync'])
#         assert 'Synchronized the block chain.' in result.output
#     with app.app_context():
#         from gumptionchain.miller import Miller
#         m = Miller(milling_wallet=Wallet())
#         assert m.longest_chain.block_hash == remote_chain.block_hash


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


def run_txn_rescind(runner, subject, txn_wallet, txn_wallet_file, confirm=True):
    return runner.invoke(
        args=[
            'txn',
            'rescind',
            txn_wallet.address,
            str(SUBJECT_GRIT),
            subject,
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
