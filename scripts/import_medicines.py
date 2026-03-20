#!/usr/bin/env python3
"""
Импорт лекарств из CSV в БД.

Особенность формата:
- первые 3 колонки содержат коды лекарств;
- в каждой из этих колонок может быть один код или список кодов через запятую;
- на каждый код создаётся отдельная запись в таблице medicines.

Использование:
  python scripts/import_medicines.py [путь/к/файлу.csv]
По умолчанию: import_data/Стартовый бот - Лекарства.csv
"""
import argparse
import asyncio
import csv
import os
import re
import sys

# Корень проекта в path для импорта database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database import async_session
from database.models import MedType, Medicine

MED_TYPE_MAP: dict[str, MedType] = {
    "антибиотик": MedType.ANTIBIOTIC,
    "антибиотики": MedType.ANTIBIOTIC,
    "иммуник": MedType.IMMUNIC,
    "иммуники": MedType.IMMUNIC,
    "обезболивающее": MedType.PAINKILLER,
    "обезболивающие": MedType.PAINKILLER,
    "панацея": MedType.PANACEA,
    "вакцина": MedType.VACCINE,
    "порошочек": MedType.POWDER,
    "нерабочее лекарство": MedType.NON_WORKING,
}


def parse_int(raw: str | None, default: int = 0) -> int:
    value = (raw or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_codes_from_cells(cells: list[str]) -> list[int]:
    """
    Из первых 3 колонок получить список кодов.
    Поддерживает как:
    - 1 | 2 | 3
    - "301, 302, 303" | "" | ""
    """
    codes: list[int] = []
    for cell in cells:
        raw = (cell or "").strip()
        if not raw:
            continue
        for part in raw.split(","):
            token = part.strip().strip('"').strip("'")
            if not token:
                continue
            if re.fullmatch(r"-?\d+", token):
                codes.append(int(token))
    # Убираем дубли в рамках одной строки, сохраняя порядок
    seen: set[int] = set()
    uniq_codes: list[int] = []
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        uniq_codes.append(code)
    return uniq_codes


def parse_med_type(raw: str) -> MedType:
    value = (raw or "").strip().lower()
    return MED_TYPE_MAP.get(value, MedType.SPECIAL)


async def run(csv_path: str, replace_existing: bool = True) -> None:
    if not os.path.isfile(csv_path):
        print(f"Файл не найден: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if len(rows) <= 1:
        print("В CSV нет строк для импорта.", file=sys.stderr)
        sys.exit(1)

    # Пропускаем заголовок
    data_rows = rows[1:]

    imported_count = 0
    skipped_count = 0

    async with async_session() as session:
        for row_idx, row in enumerate(data_rows, start=2):
            # Приводим строку к ожидаемой длине: 8 колонок
            if len(row) < 8:
                row = row + [""] * (8 - len(row))

            codes = parse_codes_from_cells(row[:3])
            if not codes:
                continue

            med_type = parse_med_type(row[3])
            cure_layer_1 = parse_int(row[4], default=0)
            cure_layer_2 = parse_int(row[5], default=0)
            cure_layer_3 = parse_int(row[6], default=0)
            pain = parse_int(row[7], default=0)

            for code in codes:
                existing_result = await session.execute(
                    select(Medicine).where(Medicine.code == code)
                )
                medicine = existing_result.scalar_one_or_none()

                if medicine and not replace_existing:
                    skipped_count += 1
                    continue

                if medicine:
                    medicine.med_type = med_type
                    medicine.cure_layer_1 = cure_layer_1
                    medicine.cure_layer_2 = cure_layer_2
                    medicine.cure_layer_3 = cure_layer_3
                    medicine.pain = pain
                else:
                    session.add(
                        Medicine(
                            code=code,
                            med_type=med_type,
                            cure_layer_1=cure_layer_1,
                            cure_layer_2=cure_layer_2,
                            cure_layer_3=cure_layer_3,
                            pain=pain,
                        )
                    )
                imported_count += 1

            if row_idx % 25 == 0:
                await session.flush()

        await session.commit()

    print(f"Импортировано/обновлено лекарств: {imported_count}")
    if skipped_count:
        print(f"Пропущено существующих (режим --no-replace): {skipped_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт лекарств из CSV в БД")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "import_data",
            "Стартовый бот - Лекарства.csv",
        ),
        help="Путь к CSV (по умолчанию: import_data/Стартовый бот - Лекарства.csv)",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Не перезаписывать существующие лекарства по code, пропускать",
    )
    args = parser.parse_args()
    asyncio.run(run(args.csv_path, replace_existing=not args.no_replace))


if __name__ == "__main__":
    main()
