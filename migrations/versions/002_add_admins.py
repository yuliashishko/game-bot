"""add admins

Revision ID: b23b712e18b6
Revises: 0a483c192000
Create Date: 2026-03-09 13:03:39.822404

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b23b712e18b6'
down_revision: Union[str, None] = '0a483c192000'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("""
        INSERT INTO users (character_name, tg_username, is_active, is_admin, is_alive, is_child, weak_zones, twyrine_addiction, infection_status) 
        VALUES 
        ('Admin Yu_sha', 'Yu_sha', true, true, true, false, '{}', false, 'HEALTHY'),
        ('Admin the_dogmeat', 'the_dogmeat', true, true, true, false, '{}', false, 'HEALTHY'),
        ('Маша', 'Ren_Raven', true, true, true, false, '{}', false, 'HEALTHY')
        ON CONFLICT (tg_username) DO UPDATE SET is_admin = true
        """)
    )

def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM users WHERE tg_username IN ('Yu_sha', 'the_dogmeat', 'Ren_Raven')"))
