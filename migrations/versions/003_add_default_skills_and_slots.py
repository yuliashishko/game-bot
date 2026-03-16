"""add default skills and slots

Revision ID: 46df11929368
Revises: b23b712e18b6
Create Date: 2026-03-09 13:05:41.292455

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46df11929368'
down_revision: Union[str, None] = 'b23b712e18b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Создаем дефолтные навыки, если их нет
    bind.execute(
        sa.text("""
        INSERT INTO skills (name, description, is_health, pain, recipes) VALUES 
        ('Здоровье', 'Обычное здоровье персонажа', true, 0, '{}'),
        ('Стрельба', 'Умение обращаться с огнестрельным оружием', false, 0, '{}'),
        ('Скрытность', 'Умение оставаться незамеченным', false, 0, '{}'),
        ('Первая помощь', 'Базовые навыки лечения', false, 0, '{CRAFT_IMMUNICS,CRAFT_ANTIBIOTICS,CRAFT_PAINKILLERS}'::recipe[])
        ON CONFLICT (name) DO NOTHING
        """)
    )

    # 2. Получаем ID созданных навыков
    res = bind.execute(sa.text("SELECT id, name FROM skills WHERE name IN ('Здоровье', 'Стрельба', 'Скрытность', 'Первая помощь')"))
    skills_map = {row[1]: row[0] for row in res}

    if len(skills_map) < 4:
        return # На всякий случай, если не удалось создать

    # 3. Для каждого существующего пользователя создаем 6 слотов, если их еще нет
    users_res = bind.execute(sa.text("SELECT id FROM users"))
    for user_row in users_res:
        uid = user_row[0]
        
        # Проверяем, есть ли уже слоты
        slots_count = bind.execute(sa.text(f"SELECT COUNT(*) FROM slots WHERE user_id = {uid}")).scalar()
        if slots_count == 0:
            bind.execute(
                sa.text("""
                INSERT INTO slots (user_id, position, layer, skill_id, disease_id) VALUES 
                (:uid, 0, 1, :health_id, NULL),
                (:uid, 1, 1, :health_id, NULL),
                (:uid, 2, 1, :health_id, NULL),
                (:uid, 3, 1, :skill1_id, NULL),
                (:uid, 4, 1, :skill2_id, NULL),
                (:uid, 5, 1, :skill3_id, NULL)
                """),
                {
                    "uid": uid,
                    "health_id": skills_map['Здоровье'],
                    "skill1_id": skills_map['Стрельба'],
                    "skill2_id": skills_map['Скрытность'],
                    "skill3_id": skills_map['Первая помощь']
                }
            )

def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM slots"))
    bind.execute(sa.text("DELETE FROM skills WHERE name IN ('Здоровье', 'Стрельба', 'Скрытность', 'Первая помощь')"))
