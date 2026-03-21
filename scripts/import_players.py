#!/usr/bin/env python3
"""
Импорт игроков из CSV (экспорт из таблицы) в БД.

Колонки CSV: character_name, tg_username, vk_username, telegram_id, is_active, is_admin,
is_alive, is_child, weak_zones, skill_1..skill_6.

Использование:
  python scripts/import_players.py [путь/к/players_import.csv]
"""
import argparse
import asyncio
import csv
import os
import sys

# Корень проекта в path для импорта config/database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import async_session
from player_import import upsert_player_from_row_dict


async def run(csv_path: str, replace_existing: bool = True) -> None:
    if not os.path.isfile(csv_path):
        print(f"Файл не найден: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("В CSV нет строк.", file=sys.stderr)
        sys.exit(1)

    imported = 0
    async with async_session() as session:
        for row in rows:
            try:
                user, info = await upsert_player_from_row_dict(
                    session, row, replace_existing=replace_existing
                )
            except ValueError as e:
                print(f"Строка «{row.get('character_name', '?')}»: {e}", file=sys.stderr)
                continue
            if user is None:
                print(info)
                continue
            imported += 1
            for line in info.split("\n"):
                if line.strip():
                    print(line)

        await session.commit()
    print(f"Импортировано игроков: {imported} из {len(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт игроков из CSV в БД")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "import_data",
            "Стартовый бот - Игроки.csv",
        ),
        help="Путь к CSV (по умолчанию: import_data/Стартовый бот - Игроки.csv)",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Не перезаписывать существующих по tg_username, пропускать",
    )
    args = parser.parse_args()
    asyncio.run(run(args.csv_path, replace_existing=not args.no_replace))


if __name__ == "__main__":
    main()
