"""rename outflow kinds subject->opposition forgive->rescind

Revision ID: 030f6eb8fca8
Revises: 63d32cd7621a
Create Date: 2026-06-03 20:38:43.114390

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '030f6eb8fca8'
down_revision = '63d32cd7621a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('outflow', schema=None) as batch_op:
        batch_op.alter_column('subject', new_column_name='opposition')
        batch_op.alter_column('forgive', new_column_name='rescind')


def downgrade() -> None:
    with op.batch_alter_table('outflow', schema=None) as batch_op:
        batch_op.alter_column('opposition', new_column_name='subject')
        batch_op.alter_column('rescind', new_column_name='forgive')
