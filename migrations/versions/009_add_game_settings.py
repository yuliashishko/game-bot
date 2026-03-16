"""add game_settings table for night mode

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-10 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'game_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('night_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.execute(sa.text("INSERT INTO game_settings (id, night_active) VALUES (1, false)"))


def downgrade() -> None:
    op.drop_table('game_settings')
