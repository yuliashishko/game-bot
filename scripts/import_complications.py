#!/usr/bin/env python3
"""
Импорт осложнений из CSV в БД.

Файл по умолчанию: import_data/Стартовый бот - Осложнения.csv
"""
import argparse
import asyncio
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database import async_session
from database.models import Complication, ComplicationSource, DiseaseCompType


def parse_optional_int(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_types(raw: str | None) -> tuple[ComplicationSource, DiseaseCompType | None] | None:
    value = (raw or "").strip().lower()
    if value == "болезнь":
        return ComplicationSource.DISEASE, None
    if value == "легкое":
        return ComplicationSource.TRAUMA, DiseaseCompType.LIGHT
    if value == "тяжелое":
        return ComplicationSource.TRAUMA, DiseaseCompType.SEVERE
    return None


async def run(csv_path: str, replace_existing: bool = True) -> None:
    if not os.path.isfile(csv_path):
        print(f"Файл не найден: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("В CSV нет строк для импорта.", file=sys.stderr)
        sys.exit(1)

    imported_count = 0
    skipped_count = 0

    async with async_session() as session:
        for row in rows:
            name = (row.get("название") or "").strip()
            type_pair = parse_types(row.get("тип"))
            trauma_code = parse_optional_int(row.get("Травма"))
            description = (row.get("описание") or "").strip()

            if not name or type_pair is None:
                continue
            source_type, disease_comp_type = type_pair

            if trauma_code is not None:
                existing_result = await session.execute(
                    select(Complication).where(Complication.trauma_code == trauma_code)
                )
                existing = existing_result.scalar_one_or_none()
            else:
                existing_result = await session.execute(
                    select(Complication).where(
                        Complication.name == name,
                        Complication.source_type == source_type,
                        Complication.disease_comp_type == disease_comp_type,
                    )
                )
                existing = existing_result.scalar_one_or_none()

            if existing and not replace_existing:
                skipped_count += 1
                continue

            if existing:
                existing.name = name
                existing.description = description
                existing.source_type = source_type
                existing.disease_comp_type = disease_comp_type
                existing.trauma_code = trauma_code
            else:
                session.add(
                    Complication(
                        name=name,
                        description=description,
                        source_type=source_type,
                        disease_comp_type=disease_comp_type,
                        trauma_code=trauma_code,
                    )
                )
            imported_count += 1

        await session.commit()

    print(f"Импортировано/обновлено осложнений: {imported_count}")
    if skipped_count:
        print(f"Пропущено существующих (режим --no-replace): {skipped_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт осложнений из CSV в БД")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "import_data",
            "Стартовый бот - Осложнения.csv",
        ),
        help="Путь к CSV (по умолчанию: import_data/Стартовый бот - Осложнения.csv)",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Не перезаписывать существующие записи, пропускать",
    )
    args = parser.parse_args()
    asyncio.run(run(args.csv_path, replace_existing=not args.no_replace))


if __name__ == "__main__":
    main()
