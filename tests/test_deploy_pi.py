"""Sanity checks for the deploy/pi kit (string-level; no systemd in CI)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parent.parent / 'deploy' / 'pi'


def test_unit_files_exist_and_are_consistent():
    miller = (DEPLOY / 'gumptionchain-miller.service').read_text()
    update = (DEPLOY / 'gumptionchain-update.service').read_text()
    timer = (DEPLOY / 'gumptionchain-update.timer').read_text()
    # the miller runs as the gc user from the repo checkout
    assert 'User=gc' in miller
    assert 'WorkingDirectory=/home/gc/gumptionchain' in miller
    assert 'mill --peer ${GC_MILL_PEER} ${GC_MILL_ADDRESS}' in miller
    assert 'Restart=always' in miller
    # the updater is root (it restarts units and writes /etc) and points
    # at a script that exists in the kit
    assert 'update.sh' in update
    assert 'User=' not in update  # root
    script = update.split('ExecStart=')[1].splitlines()[0].strip()
    assert script == '/home/gc/gumptionchain/deploy/pi/update.sh'
    assert (DEPLOY / 'update.sh').name in script
    # timer fires daily with jitter and catches up after downtime
    assert 'OnCalendar=daily' in timer
    assert 'RandomizedDelaySec=' in timer
    assert 'Persistent=true' in timer


def test_kit_scripts_are_executable():
    for name in ('update.sh', 'install.sh', 'provision-appliance.sh'):
        path = DEPLOY / name
        assert path.exists(), name
        assert path.stat().st_mode & 0o111, f'{name} not executable'


UPDATE_SH = DEPLOY / 'update.sh'


def _git(cwd, *args):
    subprocess.run(  # noqa: S603
        ['git', *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def kit(tmp_path):
    """A fixture 'origin' with tags v0.1.0/v0.2.0/v0.10.0, a clone on v0.1.0,
    and stubbed seams (uv, systemctl) that log their invocations."""
    origin = tmp_path / 'origin.git'
    work = tmp_path / 'seed'
    work.mkdir()
    _git(work, 'init', '-q', '-b', 'main')
    _git(work, 'config', 'user.email', 't@example.com')
    _git(work, 'config', 'user.name', 't')
    (work / 'deploy' / 'pi').mkdir(parents=True)
    for unit in (
        'gumptionchain-miller.service',
        'gumptionchain-update.service',
        'gumptionchain-update.timer',
    ):
        shutil.copy(DEPLOY / unit, work / 'deploy' / 'pi' / unit)
    (work / 'f.txt').write_text('one\n')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-qm', 'one')
    _git(work, 'tag', 'v0.1.0')
    (work / 'f.txt').write_text('two\n')
    _git(work, 'commit', '-aqm', 'two')
    _git(work, 'tag', 'v0.2.0')
    # v0.10.0 is intentionally after v0.2.0: lexical sort ('1' < '2') would
    # wrongly pick v0.2.0; --sort=-version:refname must win with v0.10.0.
    (work / 'f.txt').write_text('ten\n')
    _git(work, 'commit', '-aqm', 'ten')
    _git(work, 'tag', 'v0.10.0')
    _git(work, 'clone', '-q', '--bare', '.', str(origin))
    repo = tmp_path / 'repo'
    _git(tmp_path, 'clone', '-q', str(origin), str(repo))
    _git(repo, 'checkout', '-q', 'v0.1.0')

    log = tmp_path / 'calls.log'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text(f'#!/bin/sh\necho "uv $@" >> {log}\nexit 0\n')
    uv.chmod(0o755)
    sysctl = bin_dir / 'systemctl'
    sysctl.write_text(f'#!/bin/sh\necho "systemctl $@" >> {log}\nexit 0\n')
    sysctl.chmod(0o755)

    env = os.environ | {
        'REPO_DIR': str(repo),
        'AS_GC': '',
        # update.sh defaults UV to the gc user's absolute ~/.local/bin/uv
        # (root's systemd PATH lacks it); tests point it at the stub.
        'UV': str(uv),
        'SKIP_FILE': str(tmp_path / 'skip'),
        'UNIT_DIR': str(tmp_path / 'units'),
        'HEALTH_SETTLE': '0',
        'PATH': f'{bin_dir}:{os.environ["PATH"]}',
    }
    (tmp_path / 'units').mkdir()
    return {'repo': repo, 'env': env, 'log': log, 'tmp': tmp_path}


def _head_tag(repo):
    out = subprocess.run(
        ['git', 'describe', '--tags', '--exact-match'],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _run_update(kit, **extra):
    return subprocess.run(  # noqa: S603
        [str(UPDATE_SH)],
        env=kit['env'] | {k: str(v) for k, v in extra.items()},
        capture_output=True,
        text=True,
        check=False,
    )


def test_update_follows_highest_tag(kit):
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0, result.stderr
    assert _head_tag(kit['repo']) == 'v0.10.0'
    calls = kit['log'].read_text()
    assert 'uv sync --frozen' in calls
    assert 'gumptionchain db upgrade' in calls
    assert 'systemctl restart gumptionchain-miller' in calls
    assert 'systemctl is-active' in calls


def test_update_noop_when_current(kit):
    _run_update(kit, GC_UPDATE_CHANNEL='tags')
    kit['log'].write_text('')
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0
    assert 'restart' not in kit['log'].read_text()


def test_update_rolls_back_and_skips_bad_tag(kit):
    # health gate fails (is-active exits 1) -> rollback to v0.1.0 + skip
    sysctl = kit['tmp'] / 'bin' / 'systemctl'
    sysctl.write_text(
        f'#!/bin/sh\necho "systemctl $@" >> {kit["log"]}\n'
        'case "$1" in is-active) exit 1;; esac\nexit 0\n'
    )
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode != 0
    assert _head_tag(kit['repo']) == 'v0.1.0'
    skip = (kit['tmp'] / 'skip').read_text()
    assert 'v0.10.0' in skip
    # next run: bad tag is skipped, exit 0, still on v0.1.0
    sysctl.write_text(
        f'#!/bin/sh\necho "systemctl $@" >> {kit["log"]}\nexit 0\n'
    )
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0
    assert _head_tag(kit['repo']) == 'v0.1.0'


def test_update_branch_channel_follows_main(kit):
    result = _run_update(kit, GC_UPDATE_CHANNEL='main')
    assert result.returncode == 0, result.stderr
    head = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],  # noqa: S607
        cwd=kit['repo'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    main = subprocess.run(
        ['git', 'rev-parse', 'origin/main'],  # noqa: S607
        cwd=kit['repo'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == main


def test_update_syncs_changed_unit_files(kit):
    result = _run_update(kit, GC_UPDATE_CHANNEL='tags')
    assert result.returncode == 0, result.stderr
    units = kit['tmp'] / 'units'
    assert (units / 'gumptionchain-miller.service').exists()
    assert 'systemctl daemon-reload' in kit['log'].read_text()
