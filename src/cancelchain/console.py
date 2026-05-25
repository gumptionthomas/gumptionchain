from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

theme = Theme(
    {
        'error': 'bold red',
        'milling': 'cyan',
        'milling.close': 'yellow',
        'milling.milled': 'bold green',
        'milling.scooped': 'dim',
        'success': 'bold green',
        # Overridden defaults
        'progress.elapsed': 'none',
        'progress.percentage': 'none',
        'progress.remaining': 'none',
        'rule.line': 'none',
    }
)

console = Console(theme=theme, highlight=False)
