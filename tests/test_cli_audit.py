"""Demonstration tests for the 2026-06-02 CLI / operator-surface audit.

Each test demonstrates one finding and asserts the DESIRED post-fix
behavior. While a finding is open it carries
``@pytest.mark.xfail(strict=True)``; once remediated the marker is dropped
and it becomes a passing regression (tests below may be a mix). All file I/O
is confined to ``tmp_path``; no test exhausts real memory/disk or writes
outside it. (Per the CLI / operator-surface security audit.)

The `app` and `runner` fixtures come from tests/conftest.py; CLI invocation
mirrors tests/test_command.py.
"""

import os
import stat

from gumptionchain.command import MAX_IMPORT_LINE_BYTES


def test_cli1_signing_key_create_writes_private_key_0600(app, runner, tmp_path):
    """CLI1 (Medium) — REMEDIATED. `gumptionchain signing-key create` wrote
    the RSA key via `SigningKey.to_file` → `open(filename, 'wb')` with
    no `chmod 0o600`, landing at the process umask (commonly 0o644/0o664 →
    readable by a different local user/process, who then held a live signing
    identity). `to_file` now creates the PEM owner-only via
    `os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)`. This regression asserts the
    0o600 mode; the umask is pinned permissive so it stays deterministic
    regardless of the runner's environment.
    """
    old_umask = os.umask(0o022)
    try:
        with app.app_context():
            result = runner.invoke(
                args=['signing-key', 'create', '-d', str(tmp_path)]
            )
        assert result.exit_code == 0, result.output
        pems = list(tmp_path.glob('*.pem'))
        assert len(pems) == 1, f'expected one *.pem, got {pems}'
        mode = stat.S_IMODE(os.stat(pems[0]).st_mode)
        # Desired post-fix: owner-only. Today: inherits umask (e.g. 0o644).
        assert mode == 0o600, f'private key world/group-accessible: {oct(mode)}'
    finally:
        os.umask(old_umask)


def test_cli4_import_bounds_line_length(app, runner, tmp_path, monkeypatch):
    """CLI4 (Low) — REMEDIATED. `gumptionchain import` used to read each line
    with no length bound and hand the whole line to `Block.from_json`, so a
    crafted `.jsonl` with one enormous line was buffered whole (OOM risk).
    `bounded_lines` now caps each line at `MAX_IMPORT_LINE_BYTES` in BOTH the
    count and parse passes, aborting on overflow before the full line is
    buffered or parsed. Bounded-observation: one line just over the cap (no
    real exhaustion); assert the import rejects it and the full line never
    reaches `Block.from_json`.
    """
    seen_lengths: list[int] = []

    # Block.from_json is a @classmethod; monkeypatch.setattr replaces it with
    # this plain function, stripping the classmethod descriptor, so the
    # import's `Block.from_json(line)` passes `line` as `data` (no `cls`). The
    # *args tail is purely defensive. (Post-fix the oversized line is rejected
    # in the count pass before from_json is reached, so this records nothing.)
    def recording_from_json(data, *args, **kwargs):
        seen_lengths.append(len(data))
        msg = 'stop after recording'
        raise RuntimeError(msg)

    monkeypatch.setattr(
        'gumptionchain.block.Block.from_json', recording_from_json
    )

    big = tmp_path / 'big.jsonl'
    big.write_text('x' * (MAX_IMPORT_LINE_BYTES + 1024) + '\n')
    with app.app_context():
        result = runner.invoke(args=['import', str(big)])

    # The over-cap line is rejected (import aborts) and is never delivered to
    # Block.from_json beyond the cap.
    assert 'Import failed' in result.output
    assert not seen_lengths or max(seen_lengths) <= MAX_IMPORT_LINE_BYTES, (
        f'import delivered a {max(seen_lengths)}-byte line to Block.from_json'
    )
