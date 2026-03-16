"""add default symptoms

Revision ID: 8f3a1c5d9e2b
Revises: 57ee22038479
Create Date: 2026-03-10 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f3a1c5d9e2b'
down_revision: Union[str, None] = '57ee22038479'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("""
        INSERT INTO diseases (name, type, trauma_code, operation, kind, energy, layers, health_only, strength, pain, light_complication, severe_complication)
        VALUES
        ('Лихорадка', 'SYMPTOM', NULL, false, NULL, false, '{1}', true, 0, 0, false, false),
        ('Кашель', 'SYMPTOM', NULL, false, NULL, false, '{1}', true, 0, 0, false, false),
        ('Слабость', 'SYMPTOM', NULL, false, NULL, false, '{1}', true, 0, 1, false, false),
        ('Головная боль', 'SYMPTOM', NULL, false, NULL, false, '{1,2}', true, 0, 1, false, false),
        ('Тошнота', 'SYMPTOM', NULL, false, NULL, false, '{1}', true, 0, 0, false, false),
        ('Озноб', 'SYMPTOM', NULL, false, NULL, false, '{1}', true, 0, 0, false, false)
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM diseases WHERE type = 'SYMPTOM'"))
