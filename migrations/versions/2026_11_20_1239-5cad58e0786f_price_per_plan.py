"""price per plan

The single ``price`` becomes ``basic_price`` and ``pro_price``. A price already entered was the
price of Pro (the only plan anyone paid for), so it moves to ``pro_price``. Downgrading keeps the
Pro price.

Revision ID: 5cad58e0786f
Revises: e22470e7b87f
Created: 2026-11-20 12:39:59.936312
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '5cad58e0786f'
down_revision: str | None = 'e22470e7b87f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default fills the existing row, so the NOT NULL columns can be added to a full table.
    with op.batch_alter_table('payment_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('basic_price', sa.String(length=120), nullable=False, server_default='')
        )
        batch_op.add_column(
            sa.Column('pro_price', sa.String(length=120), nullable=False, server_default='')
        )
    op.execute("UPDATE payment_settings SET pro_price = price")
    with op.batch_alter_table('payment_settings', schema=None) as batch_op:
        batch_op.drop_column('price')
        batch_op.alter_column('basic_price', server_default=None)
        batch_op.alter_column('pro_price', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('payment_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('price', sa.VARCHAR(length=120), nullable=False, server_default='')
        )
    op.execute("UPDATE payment_settings SET price = pro_price")
    with op.batch_alter_table('payment_settings', schema=None) as batch_op:
        batch_op.alter_column('price', server_default=None)
        batch_op.drop_column('pro_price')
        batch_op.drop_column('basic_price')
