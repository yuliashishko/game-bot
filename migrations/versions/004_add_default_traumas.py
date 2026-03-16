"""add default traumas

Revision ID: 57ee22038479
Revises: 46df11929368
Create Date: 2026-03-09 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '57ee22038479'
down_revision: Union[str, None] = '46df11929368'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("""
        INSERT INTO diseases (name, type, trauma_code, operation, kind, energy, layers, health_only, strength, pain, light_complication, severe_complication)
        VALUES
        ('Перелом руки', 'TRAUMA', 1, false, NULL, false, '{1}', true, 0, 1, false, false),
        ('Сотрясение мозга', 'TRAUMA', 2, true, NULL, false, '{1,2}', true, 0, 2, true, false),
        ('Вывих (слой 1)', 'TRAUMA', 10, false, NULL, false, '{1}', false, 0, 0, false, false),
        ('Ушиб (слой 2)', 'TRAUMA', 11, false, NULL, false, '{2}', false, 0, 0, false, false)
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM diseases WHERE type = 'TRAUMA' AND trauma_code IN (1, 2, 10, 11)"))
