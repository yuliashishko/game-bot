#!/usr/bin/env python3
"""
Импорт болячек (раны/травмы/симптомы) из CSV в БД.

Файл по умолчанию: import_data/Стартовый бот - Болячки.csv
"""
import argparse
import asyncio
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database import async_session
from database.models import Disease, DiseaseKind, DiseaseType


def parse_bool_ru(raw: str | None, default: bool = False) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return default
    return value in ("да", "true", "1", "yes")


def parse_int(raw: str | None, default: int = 0) -> int:
    value = (raw or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_optional_int(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_layers(raw: str | None) -> list[int]:
    value = (raw or "").strip()
    if not value:
        return []
    out: list[int] = []
    for ch in value:
        if ch in ("1", "2", "3"):
            layer = int(ch)
            if layer not in out:
                out.append(layer)
    return out


def parse_disease_type(raw: str | None) -> DiseaseType | None:
    value = (raw or "").strip().lower()
    if value == "рана":
        return DiseaseType.WOUND
    if value == "травма":
        return DiseaseType.TRAUMA
    if value == "симптом":
        return DiseaseType.SYMPTOM
    return None


def infer_wound_kind(name: str) -> DiseaseKind | None:
    value = (name or "").strip().lower()
    if "небоевая" in value:
        return DiseaseKind.NON_COMBAT
    if "ножевая" in value:
        return DiseaseKind.KNIFE
    if "пулевая" in value:
        return DiseaseKind.BULLET
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
            name = (row.get("Название") or "").strip()
            d_type = parse_disease_type(row.get("Тип"))
            if not name or d_type is None:
                continue

            description = (row.get("Описание") or "").strip()
            health_only = parse_bool_ru(row.get("Здоровье"), default=False)
            layers = parse_layers(row.get("Слой"))
            pain = parse_int(row.get("Боль"), default=0)
            strength = parse_int(row.get("Сила"), default=0)
            trauma_code = parse_optional_int(row.get("Код травмы"))
            hidden_from_getting = parse_bool_ru(row.get("Скрыто от получения"), default=False)
            operation = parse_bool_ru(row.get("Операция"), default=False)
            energy = parse_bool_ru(row.get("Энергия"), default=False)
            light_complication = parse_bool_ru(row.get("Лёгкое осложнение"), default=False)
            severe_complication = parse_bool_ru(row.get("Тяжёлое осложнение"), default=False)
            kind = infer_wound_kind(name) if d_type == DiseaseType.WOUND else None

            existing = None
            if trauma_code is not None:
                existing_result = await session.execute(
                    select(Disease).where(Disease.trauma_code == trauma_code)
                )
                existing = existing_result.scalar_one_or_none()
            if existing is None:
                existing_result = await session.execute(
                    select(Disease).where(Disease.type == d_type, Disease.name == name)
                )
                existing = existing_result.scalar_one_or_none()

            if existing and not replace_existing:
                skipped_count += 1
                continue

            if existing:
                existing.name = name
                existing.type = d_type
                existing.description = description
                existing.health_only = health_only
                existing.layers = layers
                existing.pain = pain
                existing.strength = strength
                existing.trauma_code = trauma_code
                existing.hidden_from_getting = hidden_from_getting
                existing.operation = operation
                existing.energy = energy
                existing.light_complication = light_complication
                existing.severe_complication = severe_complication
                existing.kind = kind
            else:
                session.add(
                    Disease(
                        name=name,
                        type=d_type,
                        description=description,
                        health_only=health_only,
                        layers=layers,
                        pain=pain,
                        strength=strength,
                        trauma_code=trauma_code,
                        hidden_from_getting=hidden_from_getting,
                        operation=operation,
                        energy=energy,
                        light_complication=light_complication,
                        severe_complication=severe_complication,
                        kind=kind,
                    )
                )
            imported_count += 1

        await session.commit()

    print(f"Импортировано/обновлено болячек: {imported_count}")
    if skipped_count:
        print(f"Пропущено существующих (режим --no-replace): {skipped_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт болячек из CSV в БД")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "import_data",
            "Стартовый бот - Болячки.csv",
        ),
        help="Путь к CSV (по умолчанию: import_data/Стартовый бот - Болячки.csv)",
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
