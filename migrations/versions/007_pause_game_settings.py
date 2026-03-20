"""Добавить флаг паузы для остановки обработки команд в VK боте.

Во время паузы никакие команды (кроме команды включения/выключения паузы админами) не обрабатываются.
"""

from alembic import op
import sqlalchemy as sa


revision = "007_pause_game_settings"
down_revision = "006_skills_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "game_settings",
        sa.Column(
            "pause_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("game_settings", "pause_active")

