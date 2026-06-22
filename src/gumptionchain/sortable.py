"""Shared server-side sort helper for the paginated explorer leaderboards.

The URL `?sort` key is validated against a per-view allowlist and `?dir`
against asc/desc; the DAO maps the validated key to an actual column
expression. The ORDER BY column is NEVER interpolated from raw request input
— that mapping is the whole security surface.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SortSpec:
    """A validated (column key, direction) pair + template helpers."""

    key: str
    direction: str  # 'asc' | 'desc'

    def toggled(self, col_key: str) -> str:
        """The direction a header link for `col_key` should request: flip if
        it's the active column, else the column's default ('desc' — the
        leaderboards lead with 'most')."""
        if col_key == self.key:
            return 'asc' if self.direction == 'desc' else 'desc'
        return 'desc'

    def indicator(self, col_key: str) -> str:
        """'▼'/'▲' on the active column, '' otherwise."""
        if col_key != self.key:
            return ''
        return '▼' if self.direction == 'desc' else '▲'


def parse_sort(
    args: Mapping[str, str],
    *,
    allowed: set[str],
    default_key: str,
    default_dir: str = 'desc',
) -> SortSpec:
    """Validate `?sort`/`?dir` against the allowlist; fall back to defaults."""
    key = args.get('sort')
    if key not in allowed:
        key = default_key
    direction = args.get('dir')
    if direction not in ('asc', 'desc'):
        direction = default_dir
    return SortSpec(key=key, direction=direction)
