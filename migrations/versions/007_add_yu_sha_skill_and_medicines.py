"""add first aid skill for Yu_sha and default medicines

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Навык "Первая помощь" уже создаётся в 003, но на случай если его нет:
    bind.execute(
        sa.text("""
        INSERT INTO skills (name, description, is_health, pain, recipes)
        SELECT 'Первая помощь', 'Базовые навыки лечения', false, 0, '{}'
        WHERE NOT EXISTS (SELECT 1 FROM skills WHERE name = 'Первая помощь')
        """)
    )

    # Добавить слот с навыком "Первая помощь" пользователю Yu_sha, если такого слота ещё нет
    bind.execute(
        sa.text("""
        INSERT INTO slots (user_id, position, layer, skill_id, disease_id)
        SELECT u.id,
               COALESCE((SELECT MAX(sl.position) + 1 FROM slots sl WHERE sl.user_id = u.id), 0),
               1, s.id, NULL
        FROM users u
        CROSS JOIN skills s
        WHERE u.tg_username = 'Yu_sha'
          AND s.name = 'Первая помощь'
          AND NOT EXISTS (
            SELECT 1 FROM slots sl
            WHERE sl.user_id = u.id AND sl.skill_id = s.id
          )
        """)
    )

    # Заполнить таблицу лекарств, если она пуста
    res = bind.execute(sa.text("SELECT COUNT(*) FROM medicines"))
    row = res.fetchone()
    count = row[0] if row else 0
    if count == 0:
        bind.execute(
            sa.text("""
            INSERT INTO medicines (code, med_type, cure_layer_1, cure_layer_2, cure_layer_3, pain, ingredient1_id, ingredient2_id)
            VALUES
            (1, 'ANTIBIOTIC', 2, 0, 0, 0, NULL, NULL),
            (2, 'IMMUNIC', 0, 1, 0, 0, NULL, NULL),
            (3, 'PAINKILLER', 0, 0, 0, 0, NULL, NULL),
            (4, 'ANTIBIOTIC', 1, 1, 0, 1, NULL, NULL),
            (5, 'IMMUNIC', 1, 0, 1, 0, NULL, NULL)
            """)
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("""
        DELETE FROM slots WHERE id IN (
          SELECT sl.id FROM slots sl
          JOIN users u ON u.id = sl.user_id
          JOIN skills s ON s.id = sl.skill_id
          WHERE u.tg_username = 'Yu_sha' AND s.name = 'Первая помощь' AND sl.position >= 6
        )
        """)
    )
    bind.execute(sa.text("DELETE FROM medicines WHERE code IN (1, 2, 3, 4, 5)"))
