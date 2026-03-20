#!/usr/bin/env python3
"""
Импорт игроков из CSV (экспорт из таблицы) в БД.

Колонки CSV: character_name, tg_username, vk_username, is_active, is_admin, is_alive, is_child, weak_zones, skill_1..skill_6.
- weak_zones: список enum через запятую в квадратных скобках, например [HEAD, CHEST, LEFT_ARM].
- skill_N: JSON с полем "name" (название навыка для поиска в таблице skills).

Использование:
  python scripts/import_players.py [путь/к/players_import.csv]
По умолчанию: players_import.csv в корне проекта.
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
from urllib.parse import urlparse, unquote

# Корень проекта в path для импорта config/database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete
from database import async_session
from database.models import User, Slot, Skill, WeakZone, InfectionStatus


def parse_weak_zones(raw: str) -> list:
    """[HEAD, CHEST, LEFT_ARM] -> [WeakZone.HEAD, ...]"""
    if not raw or not raw.strip():
        return []
    raw = raw.strip()
    # Убрать скобки и разбить по запятой
    if raw.startswith("["):
        raw = raw[1:]
    if raw.endswith("]"):
        raw = raw[:-1]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    result = []
    for p in parts:
        try:
            result.append(WeakZone[p])
        except KeyError:
            pass
    return result


def parse_skill_name(cell: str) -> str | None:
    """Из ячейки с JSON навыка извлечь name."""
    if not cell or not cell.strip():
        return None
    raw = cell.strip()
    # Убрать лишнюю запятую в конце (часто в экспорте)
    if raw.endswith(","):
        raw = raw[:-1]
    try:
        data = json.loads(raw)
        return data.get("name") if isinstance(data, dict) else None
    except json.JSONDecodeError:
        # Попробовать вытащить "name": "..." регексом
        m = re.search(r'"name"\s*:\s*"([^"]+)"', raw)
        return m.group(1) if m else None
    return None


def slot_layer_from_position(position: int) -> int:
    return (position % 3) + 1


def parse_bool(raw: str, default: bool = False) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "да")


def normalize_vk_username(raw: str) -> str | None:
    """
    Привести vk_username к short name / idNNN:
    - https://vk.com/username -> username
    - vk.com/username -> username
    - @username -> username
    - отрезать query/hash и декодировать %xx
    """
    value = (raw or "").strip()
    if not value:
        return None

    value = value.replace("\\", "/").strip()
    if value.startswith("@"):
        value = value[1:].strip()

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        path = (parsed.path or "").strip("/")
        value = path or value
    elif value.lower().startswith("vk.com/"):
        value = value.split("/", 1)[1].strip()

    # Срезаем query/hash, если остались
    value = value.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    value = unquote(value).strip()
    return value or None


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

    async with async_session() as session:
        # Загрузить все навыки по имени
        result = await session.execute(select(Skill))
        skills_by_name = {s.name: s.id for s in result.scalars().all()}

        for row in rows:
            tg_username = (row.get("tg_username") or "").strip()
            if not tg_username:
                print(f"Пропуск строки без tg_username: {row.get('character_name', '?')}", file=sys.stderr)
                continue

            character_name = (row.get("character_name") or tg_username).strip()
            vk_username = normalize_vk_username(row.get("vk_username") or "")
            # telegram_id игнорируем, но булевые флаги берём из CSV
            is_active = parse_bool(row.get("is_active") or "", default=False)
            is_admin = parse_bool(row.get("is_admin") or "", default=False)
            is_alive = parse_bool(row.get("is_alive") or "", default=True)
            is_child = parse_bool(row.get("is_child") or "", default=False)
            weak_zones = parse_weak_zones(row.get("weak_zones") or "")

            # Существующий пользователь: обновить или пропустить
            existing = await session.execute(select(User).where(User.tg_username == tg_username))
            user = existing.scalar_one_or_none()
            if user and replace_existing:
                user.character_name = character_name
                user.vk_username = vk_username
                user.is_active = is_active
                user.is_admin = is_admin
                user.is_alive = is_alive
                user.is_child = is_child
                user.weak_zones = weak_zones
                user.infection_status = InfectionStatus.HEALTHY
                # Удалить старые слоты и создать заново
                await session.execute(delete(Slot).where(Slot.user_id == user.id))
                await session.flush()
            elif user:
                print(f"Пропуск существующего: {tg_username}")
                continue
            else:
                user = User(
                    character_name=character_name,
                    tg_username=tg_username,
                    vk_username=vk_username,
                    is_active=is_active,
                    is_admin=is_admin,
                    is_alive=is_alive,
                    is_child=is_child,
                    weak_zones=weak_zones,
                    infection_status=InfectionStatus.HEALTHY,
                )
                session.add(user)
                await session.flush()

            # Слоты по skill_1..skill_6
            for pos in range(6):
                key = f"skill_{pos + 1}"
                skill_name = parse_skill_name(row.get(key) or "")
                skill_id = skills_by_name.get(skill_name) if skill_name else None
                if skill_name and skill_id is None:
                    new_skill = Skill(
                        name=skill_name,
                        description="",
                        is_health=False,
                        pain=0,
                        recipes=[],
                    )
                    session.add(new_skill)
                    await session.flush()
                    skill_id = new_skill.id
                    skills_by_name[skill_name] = skill_id
                    print(f"  Создан новый навык: «{skill_name}»")
                layer = slot_layer_from_position(pos)
                slot = Slot(
                    user_id=user.id,
                    position=pos,
                    layer=layer,
                    skill_id=skill_id,
                    disease_id=None,
                )
                session.add(slot)

        await session.commit()
    print(f"Импортировано игроков: {len(rows)}")


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
