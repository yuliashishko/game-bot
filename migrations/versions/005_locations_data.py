"""Данные локаций: коды 0–3. Перезаписывает существующие по коду.

Код локации, Название, Шанс заражения %, количество мест, качество.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "005_locations_data"
down_revision = "004_medicines_data"
branch_labels = None
depends_on = None

LOCATION_CODES = [0, 1, 2, 3]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text("DELETE FROM locations WHERE code = ANY(:codes)"),
        {"codes": LOCATION_CODES},
    )
    bind.execute(
        text("""
            INSERT INTO locations (code, name, infection_chance, capacity, quality, pain_disease_mod, pain_wound_mod, cure_mod, light_comp_mod, severe_comp_mod)
            VALUES
            (0, 'Говно', 50, 999, false, 0, 0, 0, 0, 0),
            (1, 'Степь', 0, 999, false, 0, 0, 0, 0, 0),
            (2, 'Улица', 40, 999, false, 0, 0, 0, 0, 0),
            (3, 'Дом', 10, 3, true, 0, 0, 0, 0, 0)
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text("DELETE FROM locations WHERE code = ANY(:codes)"),
        {"codes": LOCATION_CODES},
    )
