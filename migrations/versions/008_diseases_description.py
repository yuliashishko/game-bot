"""Добавить поле description для болезней и заполнить описания.

Revision ID: 008_diseases_description
Revises: 007_pause_game_settings
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "008_diseases_description"
down_revision = "007_pause_game_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diseases",
        sa.Column("description", sa.String(), nullable=False, server_default=""),
    )

    bind = op.get_bind()
    bind.execute(
        text(
            """
            UPDATE diseases
            SET description = CASE name
                WHEN 'Небоевая рана' THEN 'Небоевая рана: базовое ранение. Учитывается как рана и влияет на доступность слотов здоровья.'
                WHEN 'Ножевая рана' THEN 'Ножевая рана: более болезненное ранение, влияет на боль и состояние персонажа.'
                WHEN 'Пулевая рана' THEN 'Пулевая рана: тяжёлое ранение с высоким вкладом в боль и риски осложнений.'
                WHEN 'Лёгкая травма' THEN 'Лёгкая травма: травма с кодом 1, применяется как травма и может участвовать в хирургических сценариях.'
                WHEN 'Тяжёлая травма' THEN 'Тяжёлая травма: травма с кодом 2, более тяжёлое состояние с высоким влиянием на здоровье.'
                WHEN 'Бессонница' THEN 'Бессонница: травма/состояние с кодом 3, влияет на общее самочувствие.'
                WHEN 'Шок' THEN 'Шок: травма/состояние с кодом 4, опасное состояние персонажа.'
                WHEN 'Кашель' THEN 'Кашель: симптом заражения, занимает слот и требует лечения.'
                WHEN 'Насморк' THEN 'Насморк: симптом заражения, ухудшает состояние до снятия симптома.'
                WHEN 'Температура' THEN 'Температура: симптом заражения, повышает тяжесть текущего состояния.'
                WHEN 'Слабость' THEN 'Слабость: симптом заражения с высоким влиянием на боеспособность.'
                ELSE description
            END
            """
        )
    )


def downgrade() -> None:
    op.drop_column("diseases", "description")
