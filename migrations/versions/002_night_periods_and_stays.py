"""Добавить хранение периодов ночи и фактов ночёвки.

Revision ID: 002_night_periods_and_stays
Revises: 0a483c192000
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa


revision = "002_night_periods_and_stays"
down_revision = "0a483c192000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "night_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "night_stays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("stayed_at", sa.DateTime(), nullable=False),
        sa.Column("auto_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["period_id"], ["night_periods.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_id", "user_id", name="uq_night_stays_period_user"),
    )

    op.create_index("ix_night_stays_period_id", "night_stays", ["period_id"])
    op.create_index("ix_night_stays_user_id", "night_stays", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_night_stays_user_id", table_name="night_stays")
    op.drop_index("ix_night_stays_period_id", table_name="night_stays")
    op.drop_table("night_stays")
    op.drop_table("night_periods")
