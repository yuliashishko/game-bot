"""
Импорт одной строки игрока (как в CSV экспорте): парсинг текста и upsert в БД.

При конфликте tg_username или vk_username с другим персонажём у старого записи
tg_username меняется на ЗАМЕНЕН_<id>, снимаются привязки TG/VK.
"""
from __future__ import annotations

import csv
import io
import json
import re
from urllib.parse import urlparse, unquote

from sqlalchemy import delete, or_, select

from database.models import InfectionStatus, Recipe, Skill, Slot, User, WeakZone

PLAYER_ROW_HEADER = (
    "character_name,tg_username,vk_username,telegram_id,is_active,is_admin,"
    "is_alive,is_child,weak_zones,skill_1,skill_2,skill_3,skill_4,skill_5,skill_6\n"
)


def parse_weak_zones(raw: str) -> list:
    if not raw or not raw.strip():
        return []
    raw = raw.strip()
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


def _parse_recipe_names(raw: str | None) -> list[str] | None:
    value = (raw or "").strip()
    if not value:
        return []
    tokens = []
    for part in value.split(","):
        token = part.strip().strip('"').strip("'")
        if token:
            tokens.append(token)
    return tokens


def _to_recipe_enums(recipe_names: list[str] | None) -> list[Recipe]:
    if recipe_names is None:
        return []
    out: list[Recipe] = []
    for name in recipe_names:
        try:
            out.append(Recipe[name])
        except KeyError:
            continue
    return out


def parse_skill_meta(cell: str) -> dict:
    meta = {
        "name": None,
        "description": None,
        "is_health": None,
        "pain": None,
        "recipes": None,
    }
    if not cell or not cell.strip():
        return meta
    raw = cell.strip()
    if raw.endswith(","):
        raw = raw[:-1]
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            name = data.get("name")
            description = data.get("description")
            is_health = data.get("is_health")
            pain = data.get("pain")
            recipes = data.get("recipes")
            meta["name"] = name if isinstance(name, str) else None
            meta["description"] = description if isinstance(description, str) else None
            meta["is_health"] = is_health if isinstance(is_health, bool) else None
            meta["pain"] = pain if isinstance(pain, int) else None
            if isinstance(recipes, list):
                meta["recipes"] = [r for r in recipes if isinstance(r, str)]
            return meta
        return meta
    except json.JSONDecodeError:
        m = re.search(r'"name"\s*:\s*"([^"]+)"', raw)
        md = re.search(r'"description"\s*:\s*"([^"]*)"', raw)
        mh = re.search(r'"is_health"\s*:\s*(true|false)', raw, flags=re.IGNORECASE)
        mp = re.search(r'"pain"\s*:\s*(-?\d+)', raw)
        mr = re.search(r'"recipes"\s*:\s*\[([^\]]*)\]', raw)
        meta["name"] = m.group(1) if m else None
        meta["description"] = md.group(1) if md else None
        if mh:
            meta["is_health"] = mh.group(1).lower() == "true"
        if mp:
            try:
                meta["pain"] = int(mp.group(1))
            except ValueError:
                meta["pain"] = None
        if mr:
            meta["recipes"] = _parse_recipe_names(mr.group(1))
        return meta
    return meta


def slot_layer_from_position(position: int) -> int:
    return (position % 3) + 1


def parse_bool(raw: str, default: bool = False) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "да")


def normalize_vk_username(raw: str) -> str | None:
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
    value = value.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    value = unquote(value).strip()
    return value or None


def parse_player_row_text(text: str) -> dict[str, str | None]:
    """
    Одна строка данных в формате CSV (как в «Стартовый бот - Игроки.csv»), без строки заголовка.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Пустая строка.")
    reader = csv.DictReader(io.StringIO(PLAYER_ROW_HEADER + text))
    rows = list(reader)
    if not rows:
        raise ValueError("Не удалось разобрать строку (ожидается формат как в экспорте CSV).")
    row = rows[0]
    if not any(
        [
            (row.get("character_name") or "").strip(),
            (row.get("tg_username") or "").strip(),
        ]
    ):
        raise ValueError("В строке нет character_name / tg_username.")
    return {k: (v if v is not None else "") for k, v in row.items()}


def release_user_identifiers(user: User) -> None:
    """Освободить tg/vk у персонажа, отданного новой строке импорта."""
    user.tg_username = f"ЗАМЕНЕН_{user.id}"
    user.telegram_id = None
    user.tg_connected = False
    user.vk_username = None
    user.vk_id = None
    user.vk_connected = False


async def _find_conflicting_users(session, new_tg: str, new_vk: str | None) -> list[User]:
    conds = [User.tg_username == new_tg]
    if new_vk:
        conds.append(User.vk_username == new_vk)
    result = await session.execute(select(User).where(or_(*conds)))
    return list(result.scalars().all())


async def upsert_player_from_row_dict(
    session,
    row: dict[str, str | None],
    *,
    replace_existing: bool = True,
) -> tuple[User | None, str]:
    """
    Создать или обновить игрока по строке CSV.
    При конфликте tg/vk у других персонажей — release_user_identifiers.

    Если replace_existing=False и уже есть пользователь с таким tg_username — (None, сообщение).
    """
    tg_username = (row.get("tg_username") or "").strip()
    if not tg_username:
        raise ValueError("В строке нет tg_username.")

    character_name = (row.get("character_name") or tg_username).strip()
    vk_username = normalize_vk_username(row.get("vk_username") or "")
    is_active = parse_bool(row.get("is_active") or "", default=False)
    is_admin = parse_bool(row.get("is_admin") or "", default=False)
    is_alive = parse_bool(row.get("is_alive") or "", default=True)
    is_child = parse_bool(row.get("is_child") or "", default=False)
    weak_zones = parse_weak_zones(row.get("weak_zones") or "")

    conflicts = await _find_conflicting_users(session, tg_username, vk_username)
    winner = next((u for u in conflicts if u.tg_username == tg_username), None)
    losers = [u for u in conflicts if winner is None or u.id != winner.id]

    if winner and not replace_existing:
        return None, "Пропуск: пользователь с таким tg_username уже есть (--no-replace)."

    notes: list[str] = []
    for u in losers:
        old_tg = u.tg_username
        old_vk = u.vk_username
        release_user_identifiers(u)
        notes.append(
            f"Конфликт: персонаж «{u.character_name}» (был tg={old_tg!r}, vk={old_vk!r}) "
            f"→ tg заменён на ЗАМЕНЕН_{u.id}, VK отвязан."
        )

    if winner:
        user = winner
        user.character_name = character_name
        user.vk_username = vk_username
        user.is_active = is_active
        user.is_admin = is_admin
        user.is_alive = is_alive
        user.is_child = is_child
        user.weak_zones = weak_zones
        user.infection_status = InfectionStatus.HEALTHY
        await session.execute(delete(Slot).where(Slot.user_id == user.id))
        await session.flush()
        notes.insert(0, f"Обновлён персонаж: {character_name} (@{tg_username}).")
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
        notes.insert(0, f"Добавлен персонаж: {character_name} (@{tg_username}).")

    for pos in range(6):
        key = f"skill_{pos + 1}"
        skill_meta = parse_skill_meta(row.get(key) or "")
        skill_name = skill_meta["name"]
        skill = None
        if skill_name:
            health_skill = skill_meta["is_health"] is True
            skill_description = skill_meta["description"] or ""
            skill_pain = skill_meta["pain"]
            if not isinstance(skill_pain, int):
                skill_pain = 0
            skill_recipes = _to_recipe_enums(skill_meta["recipes"])
            new_skill = Skill(
                name=skill_name,
                description=skill_description,
                is_health=health_skill,
                pain=skill_pain,
                recipes=skill_recipes,
            )
            session.add(new_skill)
            await session.flush()
            skill = new_skill
        layer = slot_layer_from_position(pos)
        session.add(
            Slot(
                user_id=user.id,
                position=pos,
                layer=layer,
                skill_id=skill.id if skill else None,
                disease_id=None,
            )
        )

    return user, "\n".join(notes)
