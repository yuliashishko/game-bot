#!/usr/bin/env python3
"""
Импорт локаций из CSV в БД.

Формат CSV:
Код локации,Название,Шанс заражения,количество мест,качество
"""
import argparse
import asyncio
import csv
import os
import sys

# Корень проекта в path для импорта database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database import async_session
from database.models import Location


def parse_int(raw: str | None, default: int = 0) -> int:
    value = (raw or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_bool_ru(raw: str | None, default: bool = False) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return default
    return value in ("да", "true", "1", "yes")


async def run(csv_path: str, replace_existing: bool = True) -> None:
    if not os.path.isfile(csv_path):
        print(f"Файл не найден: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("В CSV нет строк для импорта.", file=sys.stderr)
        sys.exit(1)

    imported_count = 0
    skipped_count = 0

    async with async_session() as session:
        for row in rows:
            code = parse_int(row.get("Код локации"), default=-1)
            if code < 0:
                continue

            name = (row.get("Название") or "").strip()
            infection_chance = parse_int(row.get("Шанс заражения"), default=0)
            capacity = parse_int(row.get("количество мест"), default=0)
            quality = parse_bool_ru(row.get("качество"), default=False)

            existing_result = await session.execute(
                select(Location).where(Location.code == code)
            )
            location = existing_result.scalar_one_or_none()

            if location and not replace_existing:
                skipped_count += 1
                continue

            if location:
                location.name = name
                location.infection_chance = infection_chance
                location.capacity = capacity
                location.quality = quality
            else:
                session.add(
                    Location(
                        code=code,
                        name=name,
                        infection_chance=infection_chance,
                        capacity=capacity,
                        quality=quality,
                    )
                )
            imported_count += 1

        await session.commit()

    print(f"Импортировано/обновлено локаций: {imported_count}")
    if skipped_count:
        print(f"Пропущено существующих (режим --no-replace): {skipped_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт локаций из CSV в БД")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "import_data",
            "Стартовый бот - Локации.csv",
        ),
        help="Путь к CSV (по умолчанию: import_data/Стартовый бот - Локации.csv)",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Не перезаписывать существующие локации по code, пропускать",
    )
    args = parser.parse_args()
    asyncio.run(run(args.csv_path, replace_existing=not args.no_replace))


if __name__ == "__main__":
    main()
