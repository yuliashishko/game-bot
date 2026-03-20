"""Данные лекарств: коды 11–13 (иммуники), 21–26 (антибиотики), 31 (обезболивающее), 41/51/61 (особое)."""
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

    # Удалить старые лекарства с этими кодами (перезапись)
    bind.execute(
        text("DELETE FROM medicines WHERE code = ANY(:codes)"),
        {"codes": MEDICINE_CODES},
    )

    # Вставить лекарства
    bind.execute(
        text("""
            INSERT INTO medicines (code, med_type, cure_layer_1, cure_layer_2, cure_layer_3, pain)
            VALUES
            (11, 'IMMUNIC', 2, 2, 2, 1),
            (12, 'IMMUNIC', 2, 2, 2, 1),
            (13, 'IMMUNIC', 2, 2, 2, 1),
            (21, 'ANTIBIOTIC', 3, 3, 0, 2),
            (22, 'ANTIBIOTIC', 0, 3, 3, 2),
            (23, 'ANTIBIOTIC', 3, 0, 3, 2),
            (24, 'ANTIBIOTIC', 3, 3, 0, 2),
            (25, 'ANTIBIOTIC', 0, 3, 3, 2),
            (26, 'ANTIBIOTIC', 3, 0, 3, 2),
            (31, 'PAINKILLER', -1, -1, -1, -3),
            (41, 'SPECIAL', 0, 0, 0, 0),
            (51, 'SPECIAL', 0, 0, 0, 0),
            (61, 'SPECIAL', 0, 0, 0, 0)
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text("DELETE FROM medicines WHERE code = ANY(:codes)"),
        {"codes": MEDICINE_CODES},
    )
