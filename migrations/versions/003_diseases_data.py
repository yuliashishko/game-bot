"""Данные болезней: раны, травмы, симптомы (только разрешённый список)."""
from alembic import op
from sqlalchemy import text

revision = "003_diseases_data"
down_revision = "b23b712e18b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text("""
            INSERT INTO diseases (name, type, trauma_code, operation, kind, energy, layers, health_only, strength, pain, light_complication, severe_complication)
            VALUES
            ('Небоевая рана', 'WOUND', NULL, true, 'NON_COMBAT', false, '{}', true, 0, 1, true, false),
            ('Ножевая рана', 'WOUND', NULL, true, 'KNIFE', false, '{}', true, 0, 2, false, true),
            ('Пулевая рана', 'WOUND', NULL, true, 'BULLET', false, '{}', true, 0, 3, true, true),
            ('Лёгкая травма', 'TRAUMA', 1, true, NULL, false, '{1,2,3}', false, 2, 1, true, false),
            ('Тяжёлая травма', 'TRAUMA', 2, false, NULL, false, '{}', true, 3, 2, false, false),
            ('Бессонница', 'TRAUMA', 3, false, NULL, false, '{1,2,3}', false, 0, 0, false, false),
            ('Шок', 'TRAUMA', 4, false, NULL, false, '{1,2,3}', false, 0, 0, false, false),
            ('Кашель', 'SYMPTOM', NULL, false, NULL, false, '{1,2}', false, 4, 2, false, false),
            ('Насморк', 'SYMPTOM', NULL, false, NULL, false, '{2,3}', false, 4, 2, false, false),
            ('Температура', 'SYMPTOM', NULL, false, NULL, false, '{1,3}', false, 4, 2, false, false),
            ('Слабость', 'SYMPTOM', NULL, false, NULL, false, '{}', true, 5, 3, false, false)
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    names = [
        "Небоевая рана", "Ножевая рана", "Пулевая рана",
        "Лёгкая травма", "Тяжёлая травма", "Бессонница", "Шок",
        "Кашель", "Насморк", "Температура", "Слабость",
    ]
    bind.execute(
        text("UPDATE slots SET disease_id = NULL WHERE disease_id IN (SELECT id FROM diseases WHERE name = ANY(:names))"),
        {"names": names},
    )
    bind.execute(text("DELETE FROM diseases WHERE name = ANY(:names)"), {"names": names})
