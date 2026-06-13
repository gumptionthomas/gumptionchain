"""Vocabulary gate for the signing-key rename (EGU #265).

The retired finance term must never creep back into the live product
surfaces. This walks the source, client, test, deploy, and active-doc trees
and fails if the forbidden token reappears (case-insensitive) in any file
path or file body.

The forbidden token is assembled at runtime so this gate file does not
contain the literal it forbids (and therefore never flags itself). Dated
historical records under ``docs/superpowers/`` are intentionally excluded:
rewriting them would be falsification, not cleanup.
"""

from __future__ import annotations

from pathlib import Path

# Assembled so this file holds no literal occurrence of the retired word.
FORBIDDEN = 'w' + 'allet'

_ROOT = Path(__file__).resolve().parents[1]

# Trees and files that make up the live product surface.
SCAN_DIRS = ('src', 'clients', 'tests', 'deploy', 'scripts')
SCAN_FILES = ('README.md', 'CLAUDE.md')

# docs/*.md at the top level only — docs/superpowers/ is preserved history.
DOC_DIR = _ROOT / 'docs'

SKIP_SUFFIXES = (
    '.pem',
    '.lock',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.ico',
    '.woff',
    '.woff2',
    '.ttf',
    '.pyc',
)
SKIP_DIR_NAMES = {'.git', 'node_modules', '__pycache__'}


def _candidate_files() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_DIRS:
        root = _ROOT / name
        if not root.is_dir():
            continue
        for p in root.rglob('*'):
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            if p.is_file() and p.suffix not in SKIP_SUFFIXES:
                files.append(p)
    for name in SCAN_FILES:
        p = _ROOT / name
        if p.is_file():
            files.append(p)
    files.extend(sorted(DOC_DIR.glob('*.md')))
    return files


def test_no_legacy_key_vocabulary() -> None:
    needle = FORBIDDEN.lower()
    offenders: list[str] = []
    for path in _candidate_files():
        rel = path.relative_to(_ROOT).as_posix()
        if needle in rel.lower():
            offenders.append(f'{rel} (in path)')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        if needle in text.lower():
            for n, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    offenders.append(f'{rel}:{n}: {line.strip()[:80]}')

    assert not offenders, (
        'Retired vocabulary reappeared in live product surfaces:\n'
        + '\n'.join(offenders)
    )
