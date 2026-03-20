"""Удалить неиспользуемые модификаторы из locations.

Revision ID: 004_drop_unused_location_modifiers
Revises: 003_seed_game_settings
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa


revision = "004_drop_unused_location_modifiers"
down_revision = "003_seed_game_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("locations", "pain_disease_mod")
    op.drop_column("locations", "pain_wound_mod")
    op.drop_column("locations", "cure_mod")
    op.drop_column("locations", "light_comp_mod")
    op.drop_column("locations", "severe_comp_mod")


def downgrade() -> None:
    op.add_column("locations", sa.Column("pain_disease_mod", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("locations", sa.Column("pain_wound_mod", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("locations", sa.Column("cure_mod", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("locations", sa.Column("light_comp_mod", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("locations", sa.Column("severe_comp_mod", sa.Integer(), nullable=False, server_default="0"))
