"""Добавить стартовую запись game_settings (id=1).

Revision ID: 003_seed_game_settings
Revises: 002_night_periods_and_stays
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa


revision = "003_seed_game_settings"
down_revision = "002_night_periods_and_stays"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO game_settings (
                id,
                night_active,
                pause_active,
                pain_disease_mod,
                pain_wound_mod,
                cure_mod,
                light_comp_mod,
                severe_comp_mod,
                pain_death_threshold,
                pain_consequence_divisor
            )
            VALUES (1, false, false, 1, 3, -1, 1, 1, 10, 3)
            ON CONFLICT (id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM game_settings WHERE id = 1"))
