"""seed_data: начальные данные (навыки, травмы, симптомы, локации, лекарства, game_settings). Игроки — через скрипт import_players из CSV.

Revision ID: b23b712e18b6
Revises: 0a483c192000
Create Date: 2026-03-09 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b23b712e18b6'
down_revision: Union[str, None] = '0a483c192000'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Навыки ---
    op.execute(sa.text("""
        INSERT INTO skills (name, description, is_health, pain, recipes) VALUES
        ('Здоровье', 'Обычное здоровье персонажа', true, 0, '{}'),
        ('Стрельба', 'Умение обращаться с огнестрельным оружием', false, 0, '{}'),
        ('Скрытность', 'Умение оставаться незамеченным', false, 0, '{}'),
        ('Первая помощь', 'Базовые навыки лечения', false, 0, '{CRAFT_IMMUNICS,CRAFT_ANTIBIOTICS,CRAFT_PAINKILLERS}'::recipe[])
    """))

    # Болезни (раны, травмы, симптомы) добавляются только в 003_diseases_data — только разрешённый список.

    # --- Локации ---
    op.execute(sa.text("""
        INSERT INTO locations (code, name, infection_chance, capacity, quality, pain_disease_mod, pain_wound_mod, cure_mod, light_comp_mod, severe_comp_mod) VALUES
        (1, 'Степь', 25, 0, false, 0, 0, 0, 0, 0),
        (2, 'Улица', 15, 0, true, 0, 0, 0, 0, 0),
        (3, 'Дом', 5, 0, true, 0, 0, 0, 0, 0)
    """))

    # --- Лекарства ---
    op.execute(sa.text("""
        INSERT INTO medicines (code, med_type, cure_layer_1, cure_layer_2, cure_layer_3, pain, ingredient1_id, ingredient2_id) VALUES
        (1, 'ANTIBIOTIC', 2, 0, 0, 0, NULL, NULL),
        (2, 'IMMUNIC', 0, 1, 0, 0, NULL, NULL),
        (3, 'PAINKILLER', 0, 0, 0, 0, NULL, NULL),
        (4, 'ANTIBIOTIC', 1, 1, 0, 1, NULL, NULL),
        (5, 'IMMUNIC', 1, 0, 1, 0, NULL, NULL)
    """))

    # --- Режим ночи ---
    op.execute(sa.text("INSERT INTO game_settings (id, night_active) VALUES (1, false)"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM game_settings WHERE id = 1"))
    bind.execute(sa.text("DELETE FROM medicines WHERE code IN (1, 2, 3, 4, 5)"))
    bind.execute(sa.text("DELETE FROM locations WHERE code IN (1, 2, 3)"))
    bind.execute(sa.text("DELETE FROM skills WHERE name IN ('Здоровье', 'Стрельба', 'Скрытность', 'Первая помощь')"))
