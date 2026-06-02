"""Demonstration tests for the 2026-06-02 CLI / operator-surface audit.

Each test demonstrates one finding and asserts the DESIRED post-fix
behavior. While a finding is open it carries
``@pytest.mark.xfail(strict=True)``; once remediated the marker is dropped
and it becomes a passing regression (tests below may be a mix). All file I/O
is confined to ``tmp_path``; no test exhausts real memory/disk or writes
outside it. See docs/superpowers/audits/2026-06-02-cli-audit.md.

The `app` and `runner` fixtures come from tests/conftest.py; CLI invocation
mirrors tests/test_command.py.
"""

import os
import stat

import pytest


@pytest.mark.xfail(
    strict=True,
    reason='CLI1: `wallet create` writes the private-key PEM at the process '
    'umask (group/other-readable), not 0o600; flips once to_file creates it '
    'owner-only.',
)
def test_cli1_wallet_create_writes_private_key_0600(app, runner, tmp_path):
    """CLI1 (Medium) — `cancelchain wallet create` writes the new RSA private
    key via `Wallet.to_file` → `open(filename, 'wb')` with no `chmod 0o600`,
    so it lands at the process umask (commonly 0o644/0o664 → readable by a
    different local user/process, who then holds a live signing identity).
    Desired: the key file is owner-read/write only (0o600). The umask is
    pinned permissive so the test is deterministic regardless of the runner's
    environment.
    """
    old_umask = os.umask(0o022)
    try:
        with app.app_context():
            result = runner.invoke(
                args=['wallet', 'create', '-d', str(tmp_path)]
            )
        assert result.exit_code == 0, result.output
        pems = list(tmp_path.glob('*.pem'))
        assert len(pems) == 1, f'expected one *.pem, got {pems}'
        mode = stat.S_IMODE(os.stat(pems[0]).st_mode)
        # Desired post-fix: owner-only. Today: inherits umask (e.g. 0o644).
        assert mode == 0o600, f'private key world/group-accessible: {oct(mode)}'
    finally:
        os.umask(old_umask)


@pytest.mark.xfail(
    strict=True,
    reason='CLI4: `import` buffers an unbounded single line — in BOTH the '
    'count pass (sum(1 for line in f)) and the parse pass (Block.from_json). '
    'A full fix must bound the line in both; this test observes the parse '
    'pass and flips once the oversized line no longer reaches Block.from_json.',
)
def test_cli4_import_bounds_line_length(app, runner, tmp_path, monkeypatch):
    """CLI4 (Low) — `cancelchain import` reads the file line-by-line with no
    length bound and hands the whole line to `Block.from_json`, so a crafted
    `.jsonl` with one enormous line is buffered whole (OOM risk). Desired: the
    import bounds per-line input, so `Block.from_json` never receives the full
    oversized line. Bounded-observation: an 8 MiB line — far larger than any
    legitimate block (~hundreds of KB at MAX_TRANSACTIONS) so any sane cap
    rejects it — with no real exhaustion.
    """
    oversize = 8 * 1024 * 1024  # 8 MiB, >> any legitimate block line
    seen_lengths: list[int] = []

    # Block.from_json is a @classmethod; monkeypatch.setattr replaces it with
    # this plain function, stripping the classmethod descriptor. The import's
    # `Block.from_json(line)` call therefore passes `line` as `data` (no `cls`
    # is injected), so len(data) is the line length. The *args tail is purely
    # defensive.
    def recording_from_json(data, *args, **kwargs):
        seen_lengths.append(len(data))
        msg = 'stop after recording'
        raise RuntimeError(msg)

    monkeypatch.setattr(
        'cancelchain.block.Block.from_json', recording_from_json
    )

    big = tmp_path / 'big.jsonl'
    big.write_text('x' * oversize + '\n')
    with app.app_context():
        runner.invoke(args=['import', str(big)])

    # Desired post-fix: the full oversized line never reaches Block.from_json
    # (either bounded below `oversize`, or rejected before the call).
    assert not seen_lengths or max(seen_lengths) < oversize, (
        f'import delivered an unbounded {max(seen_lengths)}-byte line to '
        'Block.from_json'
    )
