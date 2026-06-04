from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import timedelta
from http.client import responses
from typing import Any, Literal, cast

import click
import httpx
from flask import current_app
from flask.cli import AppGroup, with_appcontext
from flask_migrate import upgrade as flask_migrate_upgrade
from humanfriendly import format_timespan
from millify import millify
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from gumptionchain.api_client import ApiClient
from gumptionchain.block import Block
from gumptionchain.chain import GRAIN_PER_GRIT
from gumptionchain.console import console
from gumptionchain.database import db
from gumptionchain.miller import Miller
from gumptionchain.node import Node
from gumptionchain.payload import encode_subject
from gumptionchain.transaction import Transaction
from gumptionchain.util import host_address, now_iso
from gumptionchain.wallet import Wallet

REFRESH_PER_SECOND = 8
CHAIN_MISMATCH_MSG = 'Chain/file mismatch'
# Cap a single JSONL line during `import` so a crafted file with one enormous
# line can't OOM the import (audit CLI4). 4 MiB is far larger than any
# legitimate block (~100 transactions), so real data is never rejected. The
# import opens the file in text mode, so this is enforced as a character count
# (== bytes for the ASCII-dominant block JSON; at most ~4x bytes for
# pathological all-multibyte-UTF-8 input — still bounded, well short of the
# GB-scale exhaustion this guards against).
MAX_IMPORT_LINE_BYTES = 4 * 1024 * 1024


def grit_to_grains(grit: float) -> int:
    return int(GRAIN_PER_GRIT * float(grit))


def human_grains(grains: int | float) -> str:
    balance = int(grains) / GRAIN_PER_GRIT
    return f'{balance:.2f}'.rstrip('0').rstrip('.')


def human_bignum(num: int | float) -> str:
    return str(millify(num, precision=2, drop_nulls=False))


def human_timespan(secs: float) -> str:
    return str(format_timespan(secs))


def http_error_message(e: httpx.HTTPStatusError) -> str | None:
    try:
        msg = e.response.json().get('error')
        if msg:
            if isinstance(msg, dict):
                return ','.join([f'{k} => {v}' for k, v in msg.items()])
            elif isinstance(msg, list):
                return ','.join(msg)
            else:
                return str(msg)
        else:
            return e.response.text
    except (AttributeError, json.JSONDecodeError):
        return responses.get(e.response.status_code)


def host_api_client(
    host: str | None = None, wallet_file: str | None = None
) -> ApiClient:
    if not host:
        host = current_app.config.get('DEFAULT_COMMAND_HOST')
    if not host:
        msg = (
            'No host configured: pass --host or set '
            'GC_DEFAULT_COMMAND_HOST in the environment.'
        )
        raise click.UsageError(msg)
    if wallet_file:
        wallet = Wallet.from_file(wallet_file)
    else:
        host, address = host_address(host)
        wallet = current_app.wallets.get(address)  # type: ignore[attr-defined]
    if wallet is None:
        msg = (
            f'No wallet available for host {host}: pass --wallet-file or '
            f'load a *.pem matching {address!r} into WALLET_DIR.'
        )
        raise click.UsageError(msg)
    return ApiClient(
        host, wallet, timeout=current_app.config.get('API_CLIENT_TIMEOUT')
    )


def address_wallet(address: str, wallet_file: str | None = None) -> Wallet:
    if wallet_file:
        wallet = Wallet.from_file(wallet_file)
    else:
        wallet = current_app.wallets.get(address)  # type: ignore[attr-defined]
    if wallet is None or address != wallet.address:
        msg = f'No wallet for {address}'
        raise Exception(msg)
    return wallet


def bounded_lines(
    f: Any, max_bytes: int = MAX_IMPORT_LINE_BYTES
) -> Generator[str, None, None]:
    """Yield lines from `f`, refusing any line longer than `max_bytes`.

    `f.readline(max_bytes + 1)` reads at most `max_bytes + 1` characters, so a
    single unbounded line is never buffered whole; a line over the cap aborts
    with a clear error instead of exhausting memory (audit CLI4).
    """
    for line in iter(lambda: f.readline(max_bytes + 1), ''):
        if len(line) > max_bytes:
            msg = f'Import line exceeds the {max_bytes}-character limit'
            raise ValueError(msg)
        yield line


def read_last_line(file: str) -> str:
    with open(file, 'rb') as f:
        try:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
        except OSError:  # catch OSError in case of one line file
            f.seek(0)
        return f.readline().decode()


class ProgressBar:
    def __init__(
        self,
        title: str,
        console: Any = None,
        total: int | None = None,
        completed: int = 0,
    ) -> None:
        self.progress = Progress(
            BarColumn(),
            TextColumn('{task.completed}/{task.total}'),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']'),
            console=console,
        )
        self.task_id = self.progress.add_task(
            title, total=total, completed=completed
        )
        self.panel = Panel.fit(self.progress, title=title)
        self.live = Live(
            self.panel, console=console, refresh_per_second=REFRESH_PER_SECOND
        )

    @property
    def console(self) -> Any:
        return self.live.console

    def next(self, n: int = 1) -> None:
        self.progress.advance(self.task_id, advance=n)

    def __enter__(self) -> ProgressBar:
        self.live.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.live.__exit__(exc_type, exc_val, exc_tb)


class BlockSyncProgress:
    def __init__(self, peer: str | None = None, console: Any = None) -> None:
        self.find_progress = Progress(
            SpinnerColumn(spinner_name='aesthetic', style='none'),
            TextColumn('{task.completed} Blocks'),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']'),
        )
        self.find_task_id = self.find_progress.add_task('Finding', total=None)
        self.load_title_text = TextColumn('Waiting...')
        self.load_count_text = TextColumn('')
        self.load_progress = Progress(
            self.load_title_text,
            BarColumn(),
            self.load_count_text,
            TaskProgressColumn(),
            TimeRemainingColumn(),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']'),
        )
        self.load_task_id = self.load_progress.add_task(
            'Loading', total=None, start=False
        )
        self.finding_panel = Panel.fit(
            self.find_progress, title='Finding Blocks'
        )
        self.loading_panel = Panel.fit(
            self.load_progress, title='Loading Blocks', border_style='dim'
        )
        progress_table = Table.grid()
        progress_table.add_row(self.finding_panel, self.loading_panel)
        self.live = Live(
            progress_table,
            console=console,
            refresh_per_second=REFRESH_PER_SECOND,
        )
        self.progress: Progress = self.find_progress
        self.task_id = self.find_task_id
        self.console.print(
            Rule(title=f'Synchronizing with peer [bold]{peer}', align='left')
        )

    @property
    def console(self) -> Any:
        return self.live.console

    def next(self, n: int = 1) -> None:
        self.progress.advance(self.task_id, advance=n)

    def complete_find(self) -> int:
        block_count = self.find_progress.tasks[0].completed
        self.find_progress.update(self.find_task_id, total=block_count)
        self.load_title_text.text_format = ''
        return int(block_count)

    def switch(self) -> None:
        block_count = self.complete_find()
        self.loading_panel.border_style = 'none'
        self.load_count_text.text_format = '{task.completed}/{task.total}'
        self.load_progress.update(self.load_task_id, total=block_count)
        self.load_progress.start_task(self.load_task_id)
        self.progress = self.load_progress
        self.task_id = self.load_task_id

    def finish(self) -> None:
        self.complete_find()

    def __enter__(self) -> BlockSyncProgress:
        self.live.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.live.__exit__(exc_type, exc_val, exc_tb)


class MillingProgress:
    def __init__(self, console: Any = None) -> None:
        self.block: Block | None = None
        self.chain: Any = None
        self.progress = Progress(
            SpinnerColumn(spinner_name='aesthetic', style='milling'),
            TextColumn('{task.fields[hash_count]}h @'),
            TextColumn('{task.fields[hps]} hps'),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']'),
        )
        self.task_id = self.progress.add_task(
            'Milling', total=None, hash_count=0, hps=0
        )
        self.task = self.progress.tasks[0]
        self.panel = Panel.fit(
            self.progress, title='Milling', border_style='milling'
        )
        self.live = Live(
            self.panel, console=console, refresh_per_second=REFRESH_PER_SECOND
        )

    @property
    def hash_count(self) -> str:
        return str(human_bignum(self.task.completed))

    @property
    def hps(self) -> str:
        if self.task.elapsed:
            return human_bignum(self.task.completed / self.task.elapsed)
        else:
            return human_bignum(0)

    @property
    def console(self) -> Any:
        return self.live.console

    @property
    def elapsed(self) -> Text:
        if self.task.elapsed is None:
            return Text('-:--:--', style='progress.elapsed')
        delta = timedelta(seconds=int(self.task.elapsed))
        return Text(str(delta), style='progress.elapsed')

    def next(self, n: int = 1) -> None:
        self.progress.update(
            self.task_id, advance=n, hash_count=self.hash_count, hps=self.hps
        )

    def next_block(self, block: Block, chain: Any) -> None:
        self.block = block
        self.chain = chain
        self.progress.reset(self.task_id)

    def __enter__(self) -> MillingProgress:
        self.live.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.live.__exit__(exc_type, exc_val, exc_tb)

    def print_start(self) -> None:
        start_table = Table(show_header=False, border_style='milling')
        start_table.add_column('key', justify='right')
        start_table.add_column('value', justify='left')
        start_table.add_row(
            'Block', str(self.block.idx) if self.block is not None else ''
        )
        start_table.add_row(
            'Chain', self.chain.block_hash if self.chain else 'GENESIS'
        )
        start_table.add_row(
            'Target', self.block.target if self.block is not None else ''
        )
        start_table.add_row('Started', now_iso())
        self.console.print(start_table)

    def print_stop(self, milled_block: Block | None) -> None:
        stop_table = Table(show_header=False)
        stop_table.add_column('key', justify='right')
        stop_table.add_column('value', justify='left')
        stop_table.add_row('Stopped', now_iso())
        stop_table.add_row('Elapsed', self.elapsed)
        stop_table.add_row('Hashes', f'{self.hash_count} @ {self.hps} hps')
        label_text = Text('POW')
        style = 'milling.milled'
        if milled_block:
            pofw = milled_block.proof_of_work
            value_text = Text(f'{pofw} ({human_bignum(pofw)})', style=style)  # type: ignore[arg-type]
            stop_table.add_row(label_text, value_text)
            stop_table.add_row('Block', f'{milled_block.block_hash}')
        elif self.block is not None and self.block.proof_of_work is not None:
            style = 'milling.close'
            value_text = Text('SCOOPED (but so close)', style=style)
            stop_table.add_row(label_text, value_text)
        else:
            style = 'milling.scooped'
            value_text = Text('SCOOPED', style=style)
            stop_table.add_row(label_text, value_text)
        stop_table.border_style = style
        self.console.print(stop_table)
        self.console.print(Rule(style=style))


@click.command('init', help='Initialize the database.')
@with_appcontext
def init_db_command() -> None:
    try:
        flask_migrate_upgrade()
        console.print('Initialized the database.', style='success')
    except Exception as e:
        console.print(f'Initialization failed: {e}', style='error')


@click.command(
    'sync', help="Synchronize the node's block chain to that of its peers."
)
@with_appcontext
def sync_blocks_command() -> None:
    try:
        node = Node(
            host=current_app.config['NODE_HOST'],
            peers=current_app.config['PEERS'],
            clients=current_app.clients,  # type: ignore[attr-defined]
            logger=current_app.logger,
        )
        for latest_block, peer in node.request_latest_blocks():
            try:
                progress_bar = BlockSyncProgress(peer=peer, console=console)
                with progress_bar as progress:
                    node.fill_chain(latest_block, progress=progress)
                    progress.finish()
            except httpx.HTTPStatusError as e:
                console.print(
                    f'Synchronization failed: {http_error_message(e)}',
                    style='error',
                )
            except Exception:
                console.print_exception()
    except Exception as e:
        console.print(f'Synchronization failed: {e}', style='error')


@click.command('validate', help="Validate the node's block chain.")
@with_appcontext
def validate_chain_command() -> None:
    try:
        node = Node(logger=current_app.logger)
        lc = node.longest_chain
        if lc is None:
            console.print(
                'No chain to validate (database is empty).', style='error'
            )
            return
        progress_bar = ProgressBar(
            'Validating Chain',
            console=console,
            total=lc.length,
        )
        with progress_bar as progress:
            lc.validate(progress=progress)
    except Exception as e:
        console.print(f'The block chain is invalid: {e}', style='error')


@click.command('export')
@click.argument('file', type=click.Path())
@with_appcontext
def export_blocks_command(file: str) -> None:
    """Export the block chain to file.

    \b
    FILE is the file path to export the blocks to.
    If the file already exists, it will be appended to.
    """
    try:
        node = Node(logger=current_app.logger)
        lc = node.longest_chain
        if lc is None:
            console.print(
                'No chain to export (database is empty).', style='error'
            )
            return
        lc_dao = lc.to_dao()
        last_block: Block | None = None
        append_blocks = False
        if os.path.isfile(file) and (last_line := read_last_line(file)):
            last_block = Block.from_json(last_line)
            if lc_dao.get_block(last_block.block_hash) is None:
                raise Exception(CHAIN_MISMATCH_MSG)
            append_blocks = True
        last_idx: int = last_block.idx if last_block is not None else -1  # type: ignore[assignment]
        lc_dao_block_idx: int = lc_dao.block.idx
        last_block_idx: int = last_block.idx if last_block is not None else -1  # type: ignore[assignment]
        progress_bar = ProgressBar(
            'Exporting Blocks',
            console=console,
            total=lc_dao_block_idx + 1,
            completed=last_block_idx + 1 if last_block is not None else 0,
        )
        with (
            open(file, 'a' if append_blocks else 'w', encoding='utf-8') as f,
            progress_bar as progress,
        ):
            block_dao = lc_dao.get_block(idx=last_idx + 1)
            while block_dao is not None:
                block = Block.from_dao(block_dao)
                f.write(block.to_json())
                f.write('\n')
                progress.next()
                block_dao = lc_dao.next_block(block_dao)
    except Exception:
        console.print_exception()
        console.print('Export failed', style='error')


@click.command('import')
@click.argument('file', type=click.Path(exists=True))
@with_appcontext
def import_blocks_command(file: str) -> None:
    """Import the block chain from file.

    \b
    FILE is the file path from which to import the blocks.
    """
    try:
        node = Node(logger=current_app.logger)
        with open(file, encoding='utf-8') as f:
            progress_bar = ProgressBar(
                'Importing Blocks',
                console=console,
                total=sum(1 for _ in bounded_lines(f)),
            )
        with open(file, encoding='utf-8') as f, progress_bar as progress:
            for line in bounded_lines(f):
                block = Block.from_json(line)
                if Block.from_db(block.block_hash) is None:  # type: ignore[arg-type]
                    node.add_block(block)
                progress.next()
    except Exception:
        console.print_exception()
        console.print('Import failed', style='error')


@click.command('mill')
@click.argument('address')
@click.option(
    '-m',
    '--multi',
    is_flag=True,
    default=False,
    help='Use python multiprocessing when calculating hashes.',
)
@click.option(
    '-r',
    '--rounds',
    default=1,
    help='Number of rounds of milling between new block checks. (default 1)',
)
@click.option(
    '-s',
    '--size',
    'worksize',
    default=100000,
    help=(
        'Number of hashes to calculate per round '
        '(per CPU if multiprocessing is enabled) '
        '(default 100000)'
    ),
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for milling coinbase rewards.',
)
@click.option(
    '-p',
    '--peer',
    default=None,
    help=('Peer node to poll before checking for new blocks and transactions.'),
)
@click.option(
    '-b',
    '--blocks',
    default=0,
    help='Stop after this many blocks. (default 0 (run forever))',
)
@with_appcontext
def mill_command(
    address: str,
    multi: bool,  # noqa: FBT001
    rounds: int,
    worksize: int,
    wallet: str | None,
    peer: str | None,
    blocks: int,
) -> None:
    """Start a milling process.

    \b
    ADDRESS is the address to use for milling coinbase rewards.
    """
    milling_wallet = address_wallet(address, wallet_file=wallet)
    if peer is not None and current_app.clients.get(peer) is None:  # type: ignore[attr-defined]
        msg = f'Peer {peer} client not configured.'
        raise Exception(msg)
    miller = Miller(
        host=current_app.config['NODE_HOST'],
        peers=current_app.config['PEERS'],
        clients=current_app.clients,  # type: ignore[attr-defined]
        logger=current_app.logger,
        milling_wallet=milling_wallet,
        milling_peer=peer,
    )
    if peer:
        try:
            progress_bar = BlockSyncProgress(peer=peer, console=console)
            with progress_bar as progress:
                miller.poll_latest_blocks(progress=progress)
                progress.finish()
        except Exception:
            console.print_exception()
            db.session.rollback()
    block_count = 0
    milling_progress = MillingProgress(console=console)
    with milling_progress as progress:
        progress.console.print()
        progress.console.print(
            Rule(
                title=f'Milling as address [bold]{milling_wallet.address}',
                align='left',
                style='milling',
            )
        )
        while (not blocks) or (block_count < blocks):
            try:
                chain = miller.longest_chain
                block = miller.create_block()
                progress.next_block(block, chain)
                progress.print_start()
                try:
                    milled_block = miller.mill_block(
                        block,
                        mp=multi,
                        rounds=rounds,
                        worksize=worksize,
                        progress=progress,
                    )
                finally:
                    block_count += 1
                progress.print_stop(milled_block)
            except Exception:
                progress.console.print_exception()
                db.session.rollback()


txn_cli = AppGroup('txn', help='Command group to create transactions.')


@txn_cli.command('transfer')
@click.argument('from_address')
@click.argument('amount', type=click.FLOAT)
@click.argument('to_address')
@click.option(
    '-t',
    '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.',
)
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config).',
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.',
)
@click.option(
    '-y',
    '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.',
)
@with_appcontext
def create_transfer(
    from_address: str,
    amount: float,
    to_address: str,
    txn_wallet: str | None,
    host: str | None,
    wallet: str | None,
    yes: bool,  # noqa: FBT001
) -> None:
    """Create and post a transfer transaction.

    \b
    FROM_ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of GRIT to transfer.
    TO_ADDRESS is the transaction destination address.
    """
    try:
        txn_wallet_obj = address_wallet(from_address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_transfer_transaction(
            txn_wallet_obj.public_key_b64,
            grit_to_grains(amount),
            to_address,
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            console.print(f'Transfer transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet_obj)
            txn.sign()
            client.post_transaction(txn)
            console.print('Transfer created.', style='success')
        else:
            console.print('Transfer aborted.', style='error')
    except httpx.HTTPStatusError as e:
        console.print(
            f'Transfer failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Transfer failed: {e}', style='error')


@txn_cli.command('opposition')
@click.argument('address')
@click.argument('amount', type=click.FLOAT)
@click.argument('subject')
@click.option(
    '-t',
    '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.',
)
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config).',
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.',
)
@click.option(
    '-y',
    '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.',
)
@with_appcontext
def create_opposition(
    address: str,
    amount: float,
    subject: str,
    txn_wallet: str | None,
    host: str | None,
    wallet: str | None,
    yes: bool,  # noqa: FBT001
) -> None:
    """Create an opposition transaction.

    \b
    ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of GRIT to apply.
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        txn_wallet_obj = address_wallet(address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_opposition_transaction(
            txn_wallet_obj.public_key_b64,
            grit_to_grains(amount),
            subject,
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            console.print(f'Opposition transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet_obj)
            txn.sign()
            client.post_transaction(txn)
            console.print(f'Opposition created: {txn.txid}', style='success')
        else:
            console.print('Opposition aborted.', style='error')
    except httpx.HTTPStatusError as e:
        console.print(
            f'Opposition failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Opposition failed: {e}', style='error')


@txn_cli.command('rescind')
@click.argument('address')
@click.argument('amount', type=click.FLOAT)
@click.argument('subject')
@click.option(
    '--kind',
    type=click.Choice(['opposition', 'support']),
    required=True,
    help='Which stake to rescind: opposition or support.',
)
@click.option(
    '-t',
    '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.',
)
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config).',
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.',
)
@click.option(
    '-y',
    '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.',
)
@with_appcontext
def create_rescind(
    address: str,
    amount: float,
    subject: str,
    kind: str,  # narrowed via cast below; click.Choice enforces the values
    txn_wallet: str | None,
    host: str | None,
    wallet: str | None,
    yes: bool,  # noqa: FBT001
) -> None:
    """Create a rescind transaction.

    \b
    ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of GRIT to apply.
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        txn_wallet_obj = address_wallet(address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_rescind_transaction(
            txn_wallet_obj.public_key_b64,
            grit_to_grains(amount),
            subject,
            cast(Literal['opposition', 'support'], kind),
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            console.print(f'Rescind transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet_obj)
            txn.sign()
            client.post_transaction(txn)
            console.print(f'Rescind created: {txn.txid}', style='success')
        else:
            console.print('Rescind aborted.', style='error')
    except httpx.HTTPStatusError as e:
        console.print(f'Rescind failed: {http_error_message(e)}', style='error')
    except Exception as e:
        console.print(f'Rescind failed: {e}', style='error')


@txn_cli.command('support')
@click.argument('address')
@click.argument('amount', type=click.FLOAT)
@click.argument('subject')
@click.option(
    '-t',
    '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.',
)
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config).',
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.',
)
@click.option(
    '-y',
    '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.',
)
@with_appcontext
def create_support(
    address: str,
    amount: float,
    subject: str,
    txn_wallet: str | None,
    host: str | None,
    wallet: str | None,
    yes: bool,  # noqa: FBT001
) -> None:
    """Create a subject support transaction.

    \b
    ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of GRIT to apply.
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        txn_wallet_obj = address_wallet(address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_support_transaction(
            txn_wallet_obj.public_key_b64,
            grit_to_grains(amount),
            subject,
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            console.print(f'Support transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet_obj)
            txn.sign()
            client.post_transaction(txn)
            console.print(f'Support created: {txn.txid}', style='success')
        else:
            console.print('Support aborted.', style='error')
    except httpx.HTTPStatusError as e:
        console.print(f'Support failed: {http_error_message(e)}', style='error')
    except Exception as e:
        console.print(f'Support failed: {e}', style='error')


wallet_cli = AppGroup('wallet', help='Command group to work with wallets.')


@wallet_cli.command('create')
@click.option(
    '-d',
    '--walletdir',
    type=click.Path(exists=True),
    default=None,
    help='Parent directory for the wallet file (default from app config).',
)
@with_appcontext
def create_wallet(walletdir: str | None) -> None:
    """Create a new wallet file."""
    walletdir = walletdir or current_app.config.get('WALLET_DIR')
    w = Wallet()
    filename = w.to_file(walletdir=walletdir)
    console.print(f'Created {filename}', style='success')


@wallet_cli.command('balance')
@click.argument('address')
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config).',
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.',
)
@with_appcontext
def wallet_balance(address: str, host: str | None, wallet: str | None) -> None:
    """Get the wallet balance in GRIT for an address.

    \b
    ADDRESS is the wallet address.
    """
    try:
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_wallet_balance(address)
        balance = r.json().get('balance')
        console.print(f'{human_grains(balance)} GRIT', style='success')
    except httpx.HTTPStatusError as e:
        console.print(f'Balance failed: {http_error_message(e)}', style='error')
    except Exception as e:
        console.print(f'Balance failed: {e}', style='error')


subject_cli = AppGroup('subject', help='Command group to work with subjects.')


@subject_cli.command('opposition')
@click.argument('subject')
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config).',
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.',
)
@with_appcontext
def opposition_balance(
    subject: str, host: str | None, wallet: str | None
) -> None:
    """Get the balance (i.e. opposition transactions minus rescind
       transactions) in GRIT for a subject.

    \b
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_opposition_balance(encode_subject(subject))
        balance = r.json().get('balance')
        console.print(f'{human_grains(balance)} GRIT', style='success')
    except httpx.HTTPStatusError as e:
        console.print(
            f'Opposition balance failed: {http_error_message(e)}',
            style='error',
        )
    except Exception as e:
        console.print(f'Opposition balance failed: {e}', style='error')


@subject_cli.command('support')
@click.argument('subject')
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config)',
)
@click.option(
    '-w',
    '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth',
)
@with_appcontext
def support_balance(subject: str, host: str | None, wallet: str | None) -> None:
    """Get the support total in GRIT for a subject.

    \b
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_support_balance(encode_subject(subject))
        support = r.json().get('support')
        console.print(f'{human_grains(support)} GRIT', style='success')
    except httpx.HTTPStatusError as e:
        console.print(
            f'Support balance failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Support balance failed: {e}', style='error')
