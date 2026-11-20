"""plans as records

Plans become rows an admin can add to (``plans``): each with its price, whether it includes typed
requests to the assistant, its receipt scans and voice notes a month, its longest voice note, and
whether the app offers it. Trial, Basic and Pro are created from what was configured before — the
allowances in ``plan_settings.allowances`` and the prices in ``payment_settings`` — so nobody's
access or price changes. Those two old places are then dropped.

``owners.plan`` widens to hold the code of a plan an admin adds.

Revision ID: 7f3e2a91c4d5
Revises: abc44b7cb1ca
Created: 2026-11-20 14:00:00.000000
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = '7f3e2a91c4d5'
down_revision: str | None = 'abc44b7cb1ca'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: What each plan included before anything was configured (the old defaults).
_DEFAULTS = {
    "trial": {"receipts": None, "voice": None, "voice_seconds": 60},
    "basic": {"receipts": 0, "voice": 0, "voice_seconds": 60},
    "pro": {"receipts": None, "voice": None, "voice_seconds": 60},
}


def upgrade() -> None:
    plans = op.create_table('plans',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('price', sa.String(length=120), nullable=False),
    sa.Column('typed_requests', sa.Boolean(), nullable=False),
    sa.Column('receipts', sa.Integer(), nullable=True),
    sa.Column('voice', sa.Integer(), nullable=True),
    sa.Column('voice_seconds', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('visible', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plans'))
    )
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_plans_code'), ['code'], unique=True)

    bind = op.get_bind()
    prices = bind.execute(
        sa.text("SELECT basic_price, pro_price FROM payment_settings WHERE id = 1")
    ).first()
    price = {"basic": (prices[0] or "") if prices else "", "pro": (prices[1] or "") if prices else ""}
    stored = bind.execute(sa.text("SELECT allowances FROM plan_settings WHERE id = 1")).scalar()
    if isinstance(stored, str):
        stored = json.loads(stored or "{}")
    stored = stored or {}

    def allowance(code: str) -> dict:
        return _DEFAULTS[code] | {k: v for k, v in (stored.get(code) or {}).items()
                                  if k in _DEFAULTS[code]}

    now = datetime.now(UTC)
    rows = []
    for position, (code, name, typed, visible) in enumerate(
        [("trial", "Trial", True, False), ("basic", "Basic", False, True),
         ("pro", "Pro", True, True)]
    ):
        limits = allowance(code)
        rows.append({
            "id": uuid.uuid4(), "code": code, "name": name, "price": price.get(code, ""),
            "typed_requests": typed, "receipts": limits["receipts"], "voice": limits["voice"],
            "voice_seconds": limits["voice_seconds"], "position": position * 10,
            "visible": visible, "created_at": now,
        })
    op.bulk_insert(plans, rows)

    with op.batch_alter_table('payment_settings', schema=None) as batch_op:
        batch_op.drop_column('basic_price')
        batch_op.drop_column('pro_price')
    with op.batch_alter_table('plan_settings', schema=None) as batch_op:
        batch_op.drop_column('allowances')
    with op.batch_alter_table('owners', schema=None) as batch_op:
        batch_op.alter_column(
            'plan', existing_type=sa.String(length=8), type_=sa.String(length=20),
            existing_nullable=False, existing_server_default='basic',
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Owners on a plan an admin added fall back to Basic, which the old schema can hold.
    op.execute("UPDATE owners SET plan = 'basic', plan_expires_at = NULL "
               "WHERE plan NOT IN ('trial', 'basic', 'pro')")
    with op.batch_alter_table('owners', schema=None) as batch_op:
        batch_op.alter_column(
            'plan', existing_type=sa.String(length=20), type_=sa.String(length=8),
            existing_nullable=False, existing_server_default='basic',
        )
    rows = bind.execute(sa.text(
        "SELECT code, price, receipts, voice, voice_seconds FROM plans "
        "WHERE code IN ('trial', 'basic', 'pro')"
    )).all()
    by_code = {r[0]: r for r in rows}
    allowances = {
        code: {"receipts": r[2], "voice": r[3], "voice_seconds": r[4]}
        for code, r in by_code.items()
    }
    with op.batch_alter_table('plan_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('allowances', sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
    bind.execute(
        sa.text("UPDATE plan_settings SET allowances = :value"),
        {"value": json.dumps(allowances)},
    )
    with op.batch_alter_table('payment_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('basic_price', sa.String(length=120), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('pro_price', sa.String(length=120), nullable=False, server_default=''))
    bind.execute(
        sa.text("UPDATE payment_settings SET basic_price = :basic, pro_price = :pro"),
        {"basic": by_code["basic"][1] if "basic" in by_code else "",
         "pro": by_code["pro"][1] if "pro" in by_code else ""},
    )
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_plans_code'))
    op.drop_table('plans')
