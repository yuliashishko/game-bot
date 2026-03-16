"""add medicine recipes to First aid skill

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-10 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # У навыка «Первая помощь» добавляем рецепты: создание иммуников, антибиотиков, обезболивающих (CRAFT_IMMUNICS, CRAFT_ANTIBIOTICS, CRAFT_PAINKILLERS)
    bind.execute(
        sa.text("""
        UPDATE skills SET recipes = '{CRAFT_IMMUNICS,CRAFT_ANTIBIOTICS,CRAFT_PAINKILLERS}'::recipe[]
        WHERE name = 'Первая помощь'
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE skills SET recipes = '{}'::recipe[] WHERE name = 'Первая помощь'")
    )
