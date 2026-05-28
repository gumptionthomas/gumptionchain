"""Test-only SA 2.0 helpers — keep the verbose 2.0 patterns out of asserts."""

from cancelchain.database import db


def _count(model: type) -> int:
    """SELECT COUNT(*) FROM <model>.

    Used by `Model.query.count()` translations.
    """
    return db.session.scalar(db.select(db.func.count()).select_from(model)) or 0


def _count_select(stmt) -> int:  # type: ignore[no-untyped-def]
    """SELECT COUNT(*) FROM (<stmt>). Used by composed `.count()` translations
    where stmt is a Select returned by a chain factory or DAO method."""
    return (
        db.session.scalar(
            db.select(db.func.count()).select_from(stmt.subquery())
        )
        or 0
    )
