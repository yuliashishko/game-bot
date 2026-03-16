"""add location name and default locations for overnight

Revision ID: a1b2c3d4e5f6
Revises: 8f3a1c5d9e2b
Create Date: 2026-03-10 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '8f3a1c5d9e2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('locations', sa.Column('name', sa.String(), nullable=True))

    bind = op.get_bind()
    # Вставляем локации для ночёвки (если ещё нет по code)
    bind.execute(
        sa.text("""
        INSERT INTO locations (code, name, infection_chance, capacity, quality, pain_disease_mod, pain_wound_mod, cure_mod, light_comp_mod, severe_comp_mod)
        SELECT 1, 'Степь', 25, 0, false, 0, 0, 0, 0, 0
        WHERE NOT EXISTS (SELECT 1 FROM locations WHERE code = 1)
        """)
    )
    bind.execute(
        sa.text("""
        INSERT INTO locations (code, name, infection_chance, capacity, quality, pain_disease_mod, pain_wound_mod, cure_mod, light_comp_mod, severe_comp_mod)
        SELECT 2, 'Улица', 15, 0, true, 0, 0, 0, 0, 0
        WHERE NOT EXISTS (SELECT 1 FROM locations WHERE code = 2)
        """)
    )
    bind.execute(
        sa.text("""
        INSERT INTO locations (code, name, infection_chance, capacity, quality, pain_disease_mod, pain_wound_mod, cure_mod, light_comp_mod, severe_comp_mod)
        SELECT 3, 'Дом', 5, 0, true, 0, 0, 0, 0, 0
        WHERE NOT EXISTS (SELECT 1 FROM locations WHERE code = 3)
        """)
    )


def downgrade() -> None:
    op.drop_column('locations', 'name')
