#!/usr/bin/env python3
"""
Полный импорт стартовых данных в БД в правильном порядке.

Порядок:
1) Локации
2) Болячки
3) Осложнения
4) Лекарства
5) Игроки
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.import_locations import run as run_locations
from scripts.import_diseases import run as run_diseases
from scripts.import_complications import run as run_complications
from scripts.import_medicines import run as run_medicines
from scripts.import_players import run as run_players


def default_import_path(filename: str) -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "import_data",
        filename,
    )


async def run_all(replace_existing: bool = True) -> None:
    steps = [
        ("Локации", run_locations, default_import_path("Стартовый бот - Локации.csv")),
        ("Болячки", run_diseases, default_import_path("Стартовый бот - Болячки.csv")),
        ("Осложнения", run_complications, default_import_path("Стартовый бот - Осложнения.csv")),
        ("Лекарства", run_medicines, default_import_path("Стартовый бот - Лекарства.csv")),
        ("Игроки", run_players, default_import_path("Стартовый бот - Игроки.csv")),
    ]

    for title, func, path in steps:
        print(f"\n=== Импорт: {title} ===")
        await func(path, replace_existing=replace_existing)

    print("\nГотово: полный импорт завершен.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Полный импорт стартовых данных в БД")
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Не перезаписывать существующие записи (пропускать)",
    )
    args = parser.parse_args()
    asyncio.run(run_all(replace_existing=not args.no_replace))


if __name__ == "__main__":
    main()
