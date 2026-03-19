"""Данные лекарств: коды 11–13 (иммуники), 21–26 (антибиотики), 31 (обезболивающее), 41/51/61 (особое).

Перезаписывает существующие лекарства с этими кодами. Ингредиенты: трава = SAVYUR, мозг = BRAIN, сердце = HEART, зубы = TEETH, кровь = BLOOD.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "004_medicines_data"
down_revision = "003_diseases_data"
branch_labels = None
depends_on = None

MEDICINE_CODES = [11, 12, 13, 21, 22, 23, 24, 25, 26, 31, 41, 51, 61]


def upgrade() -> None:
    bind = op.get_bind()

    # Ингредиенты для подстановки (трава=SAVYUR, органы по имени)
    bind.execute(
        text("""
            INSERT INTO ingredients (name, category)
            VALUES
                ('SAVYUR', 'HERB'),
                ('BRAIN', 'ORGAN'),
                ('HEART', 'ORGAN'),
                ('TEETH', 'ORGAN'),
                ('BLOOD', 'ORGAN')
            ON CONFLICT (name) DO NOTHING
        """)
    )

    # Удалить старые лекарства с этими кодами (перезапись)
    bind.execute(
        text("DELETE FROM medicines WHERE code = ANY(:codes)"),
        {"codes": MEDICINE_CODES},
    )

    # Вставить лекарства. ingredient IDs через подзапросы к ingredients по name.
    bind.execute(
        text("""
            INSERT INTO medicines (code, med_type, cure_layer_1, cure_layer_2, cure_layer_3, pain, ingredient1_id, ingredient2_id)
            VALUES
            (11, 'IMMUNIC', 2, 2, 2, 1, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1)),
            (12, 'IMMUNIC', 2, 2, 2, 1, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1)),
            (13, 'IMMUNIC', 2, 2, 2, 1, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1)),
            (21, 'ANTIBIOTIC', 3, 3, 0, 2, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'BRAIN' LIMIT 1)),
            (22, 'ANTIBIOTIC', 0, 3, 3, 2, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'HEART' LIMIT 1)),
            (23, 'ANTIBIOTIC', 3, 0, 3, 2, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'TEETH' LIMIT 1)),
            (24, 'ANTIBIOTIC', 3, 3, 0, 2, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'BRAIN' LIMIT 1)),
            (25, 'ANTIBIOTIC', 0, 3, 3, 2, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'HEART' LIMIT 1)),
            (26, 'ANTIBIOTIC', 3, 0, 3, 2, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'TEETH' LIMIT 1)),
            (31, 'PAINKILLER', -1, -1, -1, -3, (SELECT id FROM ingredients WHERE name = 'SAVYUR' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'BLOOD' LIMIT 1)),
            (41, 'SPECIAL', 0, 0, 0, 0, (SELECT id FROM ingredients WHERE name = 'BRAIN' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'HEART' LIMIT 1)),
            (51, 'SPECIAL', 0, 0, 0, 0, (SELECT id FROM ingredients WHERE name = 'BRAIN' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'TEETH' LIMIT 1)),
            (61, 'SPECIAL', 0, 0, 0, 0, (SELECT id FROM ingredients WHERE name = 'HEART' LIMIT 1), (SELECT id FROM ingredients WHERE name = 'TEETH' LIMIT 1))
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text("DELETE FROM medicines WHERE code = ANY(:codes)"),
        {"codes": MEDICINE_CODES},
    )
    # Ингредиенты не удаляем: могли использоваться в других миграциях или данных
    bind.execute(
        text("""
            DELETE FROM ingredients WHERE name IN ('SAVYUR', 'BRAIN', 'HEART', 'TEETH', 'BLOOD')
            AND NOT EXISTS (SELECT 1 FROM medicines m WHERE m.ingredient1_id = ingredients.id OR m.ingredient2_id = ingredients.id)
        """)
    )
