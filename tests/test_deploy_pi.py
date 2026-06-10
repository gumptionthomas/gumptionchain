"""Sanity checks for the deploy/pi kit (string-level; no systemd in CI)."""

from pathlib import Path

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
