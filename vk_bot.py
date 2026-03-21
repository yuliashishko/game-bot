"""
VK-бот: профиль, ранения, травмы, заражение, лечение, ночёвка, режим ночи для админов.
"""
import asyncio
import json
import logging
import sys
from datetime import datetime
from enum import Enum
from typing import Any

from vkbottle import Bot, Keyboard, Text
from vkbottle.bot import Message
from vkbottle.dispatch.rules import ABCRule
from vkbottle.dispatch.rules.base import CommandRule

import random
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from config import VK_TOKEN, VK_GROUP_ID
from database import (
    async_session,
    User,
    Disease,
    Skill,
    Slot,
    Complication,
    Location,
    Medicine,
    DiseaseType,
    DiseaseKind,
    InfectionStatus,
    MedType,
    DiseaseCompType,
    GameSettings,
    NightPeriod,
    NightStay,
)
from game_logic import (
    CURE_COOLDOWN_HOURS,
    _user_active_skill_names,
    _resolve_location,
    _apply_trauma,
    apply_infection,
    get_symptom,
    _apply_trauma_by_code,
    do_treat_finalize,
)
if not VK_TOKEN:
    raise RuntimeError("VK_TOKEN не задан в .env")

bot = Bot(token=VK_TOKEN)

SPECIAL_MED_TYPES = {
    MedType.SPECIAL,
    MedType.NON_WORKING,
    MedType.POWDER,
    MedType.PANACEA,
    MedType.VACCINE,
}

SPECIAL_MEDICINE_KIND_BY_TYPE = {
    MedType.PANACEA: "Панацея",
    MedType.VACCINE: "Вакцина",
    MedType.POWDER: "Порошочек",
    MedType.NON_WORKING: "Нерабочее лекарство",
    MedType.SPECIAL: "Особое",
}

# FSM: peer_id -> {"state": FsmState | None, "data": dict}
vk_fsm: dict[int, dict[str, Any]] = {}


class FsmState(str, Enum):
    """Состояния FSM (привязка, ночёвка, лечение, вылечить травму)."""
    LINK_USERNAME = "link_username"
    CURE_TRAUMA_CODE = "cure_trauma_code"
    NIGHT_LOCATION = "night_location"
    NIGHT_USE_MELKIY = "night_use_melkiy"
    NIGHT_FOOD = "night_food"
    NIGHT_IMMUNICS = "night_immunics"
    NIGHT_PAINKILLER = "night_painkiller"
    TREAT_TARGET = "treat_target"
    TREAT_MEDICINES = "treat_medicines"
    SPECIAL_TREAT_TARGET = "special_treat_target"
    SPECIAL_TREAT_CODE = "special_treat_code"
    SURGERY_CONFIRM_HOSPITAL = "surgery_confirm_hospital"
    SURGERY_TARGET = "surgery_target"
    SURGERY_MEDICINES = "surgery_medicines"


async def get_user_from_vk(session, vk_id: int | None, vk_username: str | None):
    """Находит персонажа по vk_id или vk_username. При нахождении по username проставляет vk_id и vk_connected."""
    load_options = [
        selectinload(User.slots).selectinload(Slot.skill),
        selectinload(User.slots).selectinload(Slot.disease),
    ]
    user_query = select(User).options(*load_options)
    if vk_id is not None:
        result = await session.execute(user_query.where(User.vk_id == vk_id))
        user = result.scalar_one_or_none()
        if user:
            return user
    if vk_username:
        result = await session.execute(user_query.where(User.vk_username == vk_username))
        user = result.scalar_one_or_none()
        if user:
            if vk_id is not None:
                user.vk_id = vk_id
                user.vk_connected = True
            return user
        # Поиск по tg_username (если в ВК ввели TG @username для привязки)
        result = await session.execute(user_query.where(User.tg_username == vk_username))
        user = result.scalar_one_or_none()
        if user and vk_id is not None:
            user.vk_id = vk_id
            user.vk_connected = True
        return user
    return None


async def get_night_active() -> bool:
    async with async_session() as session:
        result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
        row = result.scalar_one_or_none()
        return row.night_active if row else False


async def get_pause_active() -> bool:
    async with async_session() as session:
        result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
        row = result.scalar_one_or_none()
        return row.pause_active if row else False


async def _main_keyboard_for_peer(peer_id: int) -> str:
    async with async_session() as session:
        user = await get_user_from_vk(session, peer_id, None)
        night_active = await get_night_active()
        # Персонально скрываем кнопку ночёвки, если пользователь уже заночевал в текущем периоде.
        if night_active and user:
            current_period = await _get_current_night_period(session)
            if current_period:
                stay_result = await session.execute(
                    select(NightStay).where(
                        NightStay.period_id == current_period.id,
                        NightStay.user_id == user.id,
                    )
                )
                if stay_result.scalars().first():
                    night_active = False
    has_trauma = _user_has_trauma(user) if user else False
    has_doctor = _user_has_doctor_skill(user) if user else False
    return get_main_keyboard_vk(night_active, has_trauma, has_doctor)


async def _send_vk_message(peer_id: int, text: str, *, keyboard: str | None = None) -> None:
    """Отправка VK-сообщения по peer_id (тихо логирует ошибки)."""
    try:
        kwargs = {"peer_id": peer_id, "message": text, "random_id": random.randint(1, 2**31 - 1)}
        if keyboard:
            kwargs["keyboard"] = keyboard
        await bot.api.messages.send(**kwargs)
    except Exception as e:
        logging.warning("Не удалось отправить VK сообщение peer_id=%s: %s", peer_id, e)


async def _get_current_night_period(session) -> NightPeriod | None:
    result = await session.execute(
        select(NightPeriod).where(NightPeriod.ended_at.is_(None)).order_by(NightPeriod.id.desc())
    )
    return result.scalars().first()


class PauseActiveRule(ABCRule[Message]):
    """Глобальный guard: пока активна пауза, никакие команды не обрабатываются, кроме /pause."""

    async def check(self, message: Message) -> bool:
        if not await get_pause_active():
            return False
        txt = (getattr(message, "text", None) or "").strip().lower()
        if not txt:
            return True
        # Разрешаем админскую команду паузы
        if txt.startswith("/"):
            cmd = txt[1:]
        elif txt.startswith("!"):
            cmd = txt[1:]
        else:
            cmd = txt
        return not cmd.startswith("pause")


@bot.on.message(PauseActiveRule())
async def vk_pause_guard(message: Message):
    await message.answer("Бот сейчас на паузе администратором. Команды недоступны.")


class DeadPlayerRule(ABCRule[Message]):
    """Глобальный guard: для мёртвого персонажа доступен только просмотр профиля."""

    async def check(self, message: Message) -> bool:
        text = (getattr(message, "text", None) or "").strip().lower()
        # Разрешаем только профиль
        if text in {
            "👤 мой профиль",
            "мой профиль",
            "📋 детальное описание профиля",
            "детальное описание профиля",
            "/me",
            "!me",
            "me",
        }:
            return False

        async with async_session() as session:
            user = await get_user_from_vk(session, message.peer_id, None)
            if not user:
                return False
            return not user.is_alive


@bot.on.message(DeadPlayerRule())
async def vk_dead_player_guard(message: Message):
    await message.answer("Ваш персонаж мёртв. Доступно только действие «👤 Мой профиль».")


def _user_has_trauma(user) -> bool:
    """Есть ли у игрока хотя бы одна травма (слот с болезнью типа TRAUMA)."""
    if not getattr(user, "slots", None):
        return False
    # Нельзя снимать травмы, предназначенные для операции (`operation=True`)
    return any(
        s.disease
        and s.disease.type == DiseaseType.TRAUMA
        and not getattr(s.disease, "operation", False)
        for s in user.slots
    )


def _user_has_doctor_skill(user) -> bool:
    """Есть ли у игрока навык «Врач» (активный, слот не заблокирован)."""
    return "Врач" in _user_active_skill_names(user)


def get_main_keyboard_vk(
    night_visible: bool = False,
    has_trauma: bool = False,
    has_doctor: bool = False,
) -> str:
    k = Keyboard(one_time=False)
    k.add(Text("👤 Мой профиль"))
    k.add(Text("📋 Детальное описание профиля"))
    k.row()
    k.add(Text("🩸 Получить ранение"))
    k.add(Text("🦴 Получить травму"))
    k.row()
    k.add(Text("🦠 Получить заражение"))
    k.add(Text("💊 Лечиться обычными лекарствами"))
    k.row()
    if night_visible:
        k.add(Text("🌙 Заночевать"))
        k.row()
    k.add(Text("🧬 Лечиться особым лекарством"))
    if has_trauma:
        k.row()
        k.add(Text("🩹 Вылечить травму"))
    if has_doctor:
        k.row()
        k.add(Text("🏥 Провести хирургическую операцию"))
    return k.get_json()


async def get_wound_keyboard_vk() -> str | None:
    async with async_session() as session:
        result = await session.execute(
            select(Disease).where(
                Disease.type == DiseaseType.WOUND,
                Disease.hidden_from_getting == False,
            )
        )
        wounds = list(result.scalars().all())
    if not wounds:
        return None
    k = Keyboard(one_time=True)
    first = True
    for disease in wounds:
        if not first:
            k.row()
        first = False
        k.add(Text(disease.name, payload={"cmd": f"wound_id_{disease.id}"}))
    return k.get_json()


def get_yes_no_keyboard_vk() -> str:
    k = Keyboard(one_time=True)
    k.add(Text("✅ Да", payload={"cmd": "yes"}))
    k.add(Text("❌ Нет", payload={"cmd": "no"}))
    return k.get_json()


def _is_yes_answer(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in {"да", "yes", "1", "✅ да"}


def get_trauma_keyboard_vk(traumas: list) -> str:
    k = Keyboard(one_time=True)
    for trauma in traumas:
        trauma_code = trauma.trauma_code if trauma.trauma_code is not None else trauma.id
        k.add(Text(trauma.name, payload={"cmd": f"trauma_{trauma_code}"}))
        k.row()
    return k.get_json()


def get_cure_trauma_keyboard_vk(traumas: list) -> str:
    """Клавиатура для «Вылечить травму»: payload не конфликтует с «Получить травму». """
    k = Keyboard(one_time=True)
    for trauma in traumas:
        trauma_code = trauma.trauma_code if trauma.trauma_code is not None else trauma.id
        k.add(
            Text(
                trauma.name,
                payload={"cmd": f"cure_trauma_{trauma_code}"},
            )
        )
        k.row()
    return k.get_json()


def get_payload_cmd(message: Message) -> str | None:
    """Извлекает cmd из payload кнопки (строка JSON с ключом cmd)."""
    if not getattr(message, "payload", None):
        return None
    try:
        payload_data = json.loads(message.payload) if isinstance(message.payload, str) else message.payload
        if not isinstance(payload_data, dict):
            return None
        return payload_data.get("cmd") or None
    except (TypeError, json.JSONDecodeError):
        return None


class HasPayloadRule(ABCRule[Message]):
    """Правило только для payload-кнопок, которые обрабатываются vk_payload_handler."""
    async def check(self, message: Message) -> bool:
        cmd = get_payload_cmd(message)
        return bool(cmd and cmd.startswith(("wound_", "trauma_", "cure_trauma_")))


class NightTextRule(ABCRule[Message]):
    """Фоллбек для команды ночи по тексту (ловим `/night`, `night`, `!night` и варианты с пробелами)."""
    async def check(self, message: Message) -> bool:
        txt = (getattr(message, "text", None) or "").strip().lower()
        return txt == "night" or txt.startswith("/night") or txt.startswith("!night")


def get_fsm(peer_id: int) -> dict:
    if peer_id not in vk_fsm:
        vk_fsm[peer_id] = {"state": None, "data": {}}
    return vk_fsm[peer_id]


# ---------- Старт и профиль ----------

@bot.on.message(text=["/start", "начать", "старт"])
async def vk_start_handler(message: Message):
    peer_id = message.peer_id
    # В VK нет username в сообщении — используем vk_id для поиска или просим ввести tg/vk username
    async with async_session() as session:
        user = await get_user_from_vk(session, peer_id, None)
        if not user:
            await message.answer(
                "Вас нет в базе. Для привязки введите ваш TG @username или VK short name (как в ссылке vk.com/...)."
            )
            get_fsm(peer_id)["state"] = FsmState.LINK_USERNAME
            return
        user.is_active = True
        await session.commit()

    name = user.character_name or user.tg_username or "Игрок"
    await message.answer(
        f"Добро пожаловать, {name}! Ваш профиль активирован.",
        keyboard=await _main_keyboard_for_peer(peer_id),
    )


@bot.on.message(text="👤 Мой профиль")
@bot.on.message(command=("me", 0))
async def vk_me_handler(message: Message):
    peer_id = message.peer_id
    async with async_session() as session:
        current_user = await get_user_from_vk(session, peer_id, None)
        if not current_user:
            await message.answer("Вы не подключены. Напишите /start для привязки.")
            return
        await session.commit()

    health_max = health_current = wounds = 0
    traumas_text = []
    symptoms_text = []
    skills = []
    recipes = []
    for slot in current_user.slots:
        is_blocked = slot.disease is not None
        if slot.disease:
            disease = slot.disease
            match disease.type:
                case DiseaseType.WOUND:
                    wounds += 1
                case DiseaseType.TRAUMA:
                    traumas_text.append(f"🦴 {disease.name}")
                case DiseaseType.SYMPTOM:
                    symptoms_text.append(f"🤒 {disease.name}")
        if slot.skill:
            if slot.skill.is_health:
                health_max += 1
                if not is_blocked:
                    health_current += 1
            elif not is_blocked:
                if (slot.skill.name or "").strip().lower() != "здоровье":
                    skills.append(slot.skill.name)
                for recipe in (slot.skill.recipes or []):
                    recipe_name = recipe.value if hasattr(recipe, "value") else str(recipe)
                    if recipe_name not in recipes:
                        recipes.append(recipe_name)

    status_text = current_user.infection_status.value if current_user.infection_status else "Здоров"
    life_status = "Мёртв" if not current_user.is_alive else "Жив"
    weak_zones = [z.value if hasattr(z, "value") else str(z) for z in (current_user.weak_zones or [])]
    char_name = current_user.character_name or "Неизвестный"
    vk_line = current_user.vk_username or "не указан"
    msg = f"👤 Профиль игрока {char_name}\n"
    msg += f"🔗 VK (логин в базе): {vk_line}\n\n"
    msg += f"☠️ Статус персонажа: {life_status}\n"
    msg += f"🦠 Статус инфекции: {status_text}\n"
    msg += f"❤️ Здоровье: {health_current} / {health_max}\n"
    msg += f"🩸 Ран: {wounds}\n"
    msg += "🎯 Слабые зоны: " + (", ".join(weak_zones) if weak_zones else "Нет") + "\n\n"
    msg += "Травмы:\n" + ("\n".join(set(traumas_text)) if traumas_text else "Нет") + "\n\n"
    msg += "Симптомы:\n" + ("\n".join(set(symptoms_text)) if symptoms_text else "Нет") + "\n\n"
    msg += "Активные навыки:\n" + ("\n".join(f"🧠 {s}" for s in skills) if skills else "Нет") + "\n\n"
    msg += "Доступные рецепты:\n" + ("\n".join(f"📜 {r}" for r in recipes) if recipes else "Нет")

    await message.answer(msg, keyboard=await _main_keyboard_for_peer(peer_id))


@bot.on.message(text="📋 Детальное описание профиля")
@bot.on.message(command=("profile_details", 0))
async def vk_profile_details_handler(message: Message):
    peer_id = message.peer_id
    async with async_session() as session:
        current_user = await get_user_from_vk(session, peer_id, None)
        if not current_user:
            await message.answer("Вы не подключены. Напишите /start для привязки.")
            return

    wounds: list[str] = []
    traumas: list[str] = []
    symptoms: list[str] = []
    skills: list[str] = []

    def _disease_line(disease) -> str:
        description = (getattr(disease, "description", "") or "").strip()
        return f"• {disease.name} — {description}" if description else f"• {disease.name}"

    for slot in current_user.slots or []:
        if slot.disease:
            disease = slot.disease
            disease_line = _disease_line(disease)
            match disease.type:
                case DiseaseType.WOUND:
                    wounds.append(disease_line)
                case DiseaseType.TRAUMA:
                    traumas.append(disease_line)
                case DiseaseType.SYMPTOM:
                    symptoms.append(disease_line)
        if slot.skill and slot.disease_id is None and (slot.skill.name or "").strip().lower() != "здоровье":
            desc = (slot.skill.description or "").strip()
            skills.append(f"• {slot.skill.name}: {desc}" if desc else f"• {slot.skill.name}")

    char_name = current_user.character_name or "Неизвестный"
    vk_line = current_user.vk_username or "не указан"
    msg = f"📋 Детальный профиль: {char_name}\n"
    msg += f"🔗 VK (логин в базе): {vk_line}\n\n"
    msg += "Болячки (раны):\n" + ("\n".join(wounds) if wounds else "Нет") + "\n\n"
    msg += "Травмы:\n" + ("\n".join(traumas) if traumas else "Нет") + "\n\n"
    msg += "Симптомы:\n" + ("\n".join(symptoms) if symptoms else "Нет") + "\n\n"
    msg += "Навыки:\n" + ("\n".join(skills) if skills else "Нет")
    await message.answer(msg, keyboard=await _main_keyboard_for_peer(peer_id))


# ---------- Привязка по username (внутри единого FSM-обработчика) ----------


# ---------- Ранение ----------

@bot.on.message(text="🩸 Получить ранение")
async def vk_wound_start(message: Message):
    keyboard = await get_wound_keyboard_vk()
    if not keyboard:
        await message.answer("Сейчас нет доступных для получения ранений.")
        return
    await message.answer("Выберите тип ранения:", keyboard=keyboard)


@bot.on.message(HasPayloadRule())
async def vk_payload_handler(message: Message):
    """Обработка нажатий кнопок: ранение (wound_*) и травма (trauma_*)."""
    payload_cmd = get_payload_cmd(message)
    if not payload_cmd:
        return
    peer_id = message.peer_id

    if payload_cmd.startswith("wound_"):
        if not payload_cmd.startswith("wound_id_"):
            return False
        try:
            disease_id = int(payload_cmd.replace("wound_id_", ""))
        except ValueError:
            return False
        async with async_session() as session:
            current_user = await get_user_from_vk(session, peer_id, None)
            await session.commit()
            if not current_user:
                await message.answer("Вы не зарегистрированы.")
                return True
            if not current_user.is_alive:
                await message.answer("Ваш персонаж уже мёртв.")
                return True
            available_health_slots = [
                slot
                for slot in current_user.slots
                if slot.disease is None and slot.skill and slot.skill.is_health
            ]
            if not available_health_slots:
                await message.answer("Нет свободных ячеек здоровья для ранения!")
                return True
            chosen_slot = random.choice(available_health_slots)
            disease_result = await session.execute(
                select(Disease).where(
                    Disease.id == disease_id,
                    Disease.type == DiseaseType.WOUND,
                    Disease.hidden_from_getting == False,
                )
            )
            wound_disease = disease_result.scalar_one_or_none()
            if not wound_disease:
                await message.answer("Ранение этого типа скрыто от получения.")
                return True
            chosen_slot.disease_id = wound_disease.id
            remaining_health = len(available_health_slots) - 1
            skill_name = chosen_slot.skill.name if chosen_slot.skill else ""
            if remaining_health == 0:
                current_user.is_alive = False
            await session.commit()
        msg = f"Вы получили ранение: {wound_disease.name}.\n"
        if skill_name != "Здоровье":
            msg += f"⚠️ Навык {skill_name} временно недоступен.\n"
        if remaining_health == 0:
            msg += "\n💀 Ваше здоровье упало до 0. Вы мертвы!"
        else:
            msg += f"\n❤️ Осталось здоровья: {remaining_health}"
        await message.answer(
            msg,
            keyboard=await _main_keyboard_for_peer(peer_id),
        )
        return True

    if payload_cmd.startswith("trauma_"):
        try:
            trauma_code = int(payload_cmd.replace("trauma_", ""))
        except ValueError:
            return False
        async with async_session() as session:
            disease_result = await session.execute(
                select(Disease).where(
                    Disease.type == DiseaseType.TRAUMA,
                    Disease.trauma_code == trauma_code
                )
            )
            trauma_disease = disease_result.scalar_one_or_none()
            if not trauma_disease:
                await message.answer("Травма не найдена.")
                return True
            current_user = await get_user_from_vk(session, peer_id, None)
            await session.commit()
            if not current_user:
                await message.answer("Вы не зарегистрированы.")
                return True
            if not current_user.is_alive:
                await message.answer("Ваш персонаж уже мёртв.")
                return True
            chosen_slot, remaining_health, skill_name = _apply_trauma(session, current_user, trauma_disease)
            if chosen_slot is None:
                await message.answer("Нет подходящей свободной ячейки для травмы.")
                return True
            if remaining_health == 0:
                current_user.is_alive = False
            await session.commit()
        msg = f"Вы получили травму: {trauma_disease.name}.\n"
        if skill_name and skill_name != "Здоровье":
            msg += f"⚠️ Навык {skill_name} временно недоступен.\n"
        if remaining_health == 0:
            msg += "\n💀 Здоровье = 0. Вы мертвы."
        else:
            msg += f"\n❤️ Осталось здоровья: {remaining_health}"
        await message.answer(
            msg,
            keyboard=await _main_keyboard_for_peer(peer_id),
        )
        return True

    if payload_cmd.startswith("cure_trauma_"):
        # Снятие травмы в FSM «Вылечить травму»
        fsm_state = get_fsm(peer_id).get("state")
        try:
            state = fsm_state if isinstance(fsm_state, FsmState) else FsmState(fsm_state)
        except Exception:
            state = fsm_state
        if state != FsmState.CURE_TRAUMA_CODE:
            return False

        try:
            trauma_code = int(payload_cmd.replace("cure_trauma_", ""))
        except ValueError:
            return False

        # Завершаем FSM перед попыткой (как и в текстовой ветке)
        get_fsm(peer_id)["state"] = None
        get_fsm(peer_id)["data"] = {}

        async with async_session() as session:
            current_user = await get_user_from_vk(session, peer_id, None)
            if not current_user:
                await message.answer("Вы не подключены.")
                return True
            if not current_user.is_alive:
                await message.answer("Ваш персонаж уже мёртв.")
                return True

            slot_with_trauma = None
            trauma_name = ""
            for slot in current_user.slots or []:
                if slot.disease and slot.disease.type == DiseaseType.TRAUMA:
                    code = (
                        slot.disease.trauma_code
                        if slot.disease.trauma_code is not None
                        else slot.disease.id
                    )
                    if code == trauma_code:
                        if getattr(slot.disease, "operation", False):
                            await message.answer(
                                "Эту травму нельзя снять действием «Вылечить травму» — она предназначена для операции."
                            )
                            return True
                        slot_with_trauma = slot
                        trauma_name = slot.disease.name
                        break

            if slot_with_trauma is None:
                await message.answer("У вас нет травмы с таким кодом.")
                return True

            slot_with_trauma.disease_id = None
            # Если relationship уже загружен, явно подчистим чтобы кнопки/проверки обновились без refresh
            try:
                slot_with_trauma.disease = None
            except Exception:
                pass
            await session.commit()

        await message.answer(
            f"Травма «{trauma_name}» снята.",
            keyboard=await _main_keyboard_for_peer(peer_id),
        )
        return True

    return False


# ---------- Травма ----------

@bot.on.message(text="🦴 Получить травму")
async def vk_trauma_start(message: Message):
    async with async_session() as session:
        result = await session.execute(
            select(Disease).where(
                Disease.type == DiseaseType.TRAUMA,
                Disease.hidden_from_getting == False,
            ).order_by(Disease.trauma_code)
        )
        traumas = list(result.scalars().all())
    if not traumas:
        await message.answer("В базе нет травм.")
        return
    await message.answer("Выберите травму:", keyboard=get_trauma_keyboard_vk(traumas))


# ---------- Заражение ----------

@bot.on.message(text="🦠 Получить заражение")
async def vk_infection_handler(message: Message):
    peer_id = message.peer_id
    async with async_session() as session:
        user = await get_user_from_vk(session, peer_id, None)
        if not user:
            await message.answer("Вы не подключены.")
            return
        await session.commit()
        if not user.is_alive:
            await message.answer("Ваш персонаж уже мёртв.")
            return
        msg = await apply_infection(session, user)
        await session.commit()
    await message.answer(msg)


# ---------- Список лекарств ----------

@bot.on.message(command=("medicines", 0))
async def vk_medicines_handler(message: Message):
    async with async_session() as session:
        medicines_result = await session.execute(
            select(Medicine).where(Medicine.med_type.notin_(SPECIAL_MED_TYPES)).order_by(Medicine.code)
        )
        medicines = list(medicines_result.scalars().all())
    if not medicines:
        await message.answer("В базе пока нет обычных лекарств.")
        return
    lines = ["💊 Коды лекарств (для ввода при лечении):\n"]
    for medicine in medicines:
        lines.append(
            f"  {medicine.code} — {medicine.med_type.value} (слой1: {medicine.cure_layer_1}, слой2: {medicine.cure_layer_2}, слой3: {medicine.cure_layer_3}, боль: {medicine.pain})"
        )
    await message.answer("\n".join(lines))


# ---------- Режим ночи (админ) ----------

async def _apply_auto_night_action(session, user: User, period: NightPeriod | None, gavno_location: Location | None) -> str:
    """
    Автодействие в конце ночи для тех, кто не заночевал:
    - локация "Говно"
    - проверка заражения
    - травма "Бессонница" (код 3)
    """
    now_msg_parts = ["🌙 Вы не выполнили действие «Заночевать» до конца ночи. Применено автодействие."]
    active_skills = set(_user_active_skill_names(user))
    if gavno_location:
        roll = random.randint(0, max(0, gavno_location.infection_chance))
        if user.is_child:
            roll -= 10
        if "Крепыш" in active_skills:
            roll -= 10
        if roll > 0:
            infection_msg = await apply_infection(session, user)
            now_msg_parts.append(f"🦠 Заражение: {infection_msg}")
        else:
            now_msg_parts.append("🦠 Заражения не произошло.")
    else:
        now_msg_parts.append("⚠️ Локация «Говно» не найдена в базе.")

    ok, trauma_msg = await _apply_trauma_by_code(session, user, 3)  # Бессонница
    if ok:
        now_msg_parts.append(f"🩹 Применена травма: {trauma_msg}.")
    else:
        now_msg_parts.append(f"⚠️ Не удалось применить травму «Бессонница»: {trauma_msg}")

    if period:
        auto_stay = NightStay(
            period_id=period.id,
            user_id=user.id,
            location_id=gavno_location.id if gavno_location else None,
            stayed_at=datetime.utcnow(),
            auto_applied=True,
        )
        session.add(auto_stay)

    return "\n".join(now_msg_parts)


async def _notify_night_started() -> None:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.vk_id.is_not(None), User.is_alive == True)
        )
        users = list(result.scalars().all())
    logging.info("Старт ночи: отправляем уведомление %s пользователям", len(users))
    for user in users:
        keyboard = await _main_keyboard_for_peer(user.vk_id)
        await _send_vk_message(
            user.vk_id,
            "🌙 Наступила ночь. Доступно действие «Заночевать».",
            keyboard=keyboard,
        )


async def _handle_night_finished(session, period: NightPeriod | None) -> tuple[list[str], list[str], list[str]]:
    """
    Завершение ночи:
    - автодействие тем, кто не заночевал
    - вернуть строки (не заночевали, заночевали) для админской сводки
    """
    users_result = await session.execute(
        select(User)
        .options(
            selectinload(User.slots).selectinload(Slot.skill),
            selectinload(User.slots).selectinload(Slot.disease),
        )
        .where(User.vk_id.is_not(None), User.is_alive == True)
    )
    active_users = list(users_result.scalars().all())
    logging.info("Конец ночи: кандидатов для автодействия=%s", len(active_users))

    stayed_user_ids: set[int] = set()
    stays_by_user_id: dict[int, NightStay] = {}
    all_stays: list[NightStay] = []
    if period:
        stays_result = await session.execute(select(NightStay).where(NightStay.period_id == period.id))
        all_stays = list(stays_result.scalars().all())
        stayed_user_ids = {s.user_id for s in all_stays}
        stays_by_user_id = {s.user_id: s for s in all_stays}

    location_result = await session.execute(select(Location).where(Location.code == 0))
    gavno_location = location_result.scalars().first()
    location_names = {}
    all_locations_result = await session.execute(select(Location))
    for loc in all_locations_result.scalars().all():
        location_names[loc.id] = loc.name or f"#{loc.code}"

    missing_lines: list[str] = []
    stayed_lines: list[str] = []

    # Блок "Заночевали" строим по фактическим NightStay (независимо от is_active),
    # чтобы сводка не была пустой при неактивном флаге у игроков.
    if all_stays:
        user_ids = sorted({s.user_id for s in all_stays})
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u for u in users_result.scalars().all()}
        for stay in all_stays:
            u = users_map.get(stay.user_id)
            display_name = (
                (u.character_name if u else None)
                or (u.vk_username if u else None)
                or (u.tg_username if u else None)
                or f"id={stay.user_id}"
            )
            loc_name = location_names.get(stay.location_id, "Неизвестно")
            suffix = " (авто)" if stay.auto_applied else ""
            stayed_lines.append(f"• {display_name} — {loc_name}{suffix}")

    for user in active_users:
        display_name = user.character_name or user.vk_username or user.tg_username or f"id={user.id}"
        if user.id not in stayed_user_ids:
            auto_msg = await _apply_auto_night_action(session, user, period, gavno_location)
            missing_lines.append(f"• {display_name}")
            await _send_vk_message(user.vk_id, auto_msg, keyboard=await _main_keyboard_for_peer(user.vk_id))
            continue

    # Проверка превышения вместимости среди тех, кто реально заночевал сам (без авто-действия)
    overflow_lines: list[str] = []
    if period:
        manual_stays_result = await session.execute(
            select(NightStay).where(NightStay.period_id == period.id, NightStay.auto_applied == False)
        )
        manual_stays = list(manual_stays_result.scalars().all())
        if manual_stays:
            count_by_location_id: dict[int, int] = {}
            for stay in manual_stays:
                if stay.location_id is None:
                    continue
                count_by_location_id[stay.location_id] = count_by_location_id.get(stay.location_id, 0) + 1

            for location_id, stayed_count in count_by_location_id.items():
                loc_name = location_names.get(location_id, f"id={location_id}")
                # Ищем capacity по уже загруженным локациям (в location_names его нет)
                loc_result = await session.execute(select(Location).where(Location.id == location_id))
                loc = loc_result.scalars().first()
                capacity = loc.capacity if loc else 0
                if capacity > 0 and stayed_count > capacity:
                    overflow_lines.append(
                        f"• {loc_name}: {stayed_count}/{capacity} (превышение на {stayed_count - capacity})"
                    )

    return missing_lines, stayed_lines, overflow_lines


def _build_night_summary_text(
    missing_lines: list[str], stayed_lines: list[str], overflow_lines: list[str]
) -> str:
    summary = ["🌙 Ночь завершена. Сводка:"]
    summary.append("\nНе заночевали (сверху):")
    summary.append("\n".join(missing_lines) if missing_lines else "Нет")
    summary.append("\nЗаночевали:")
    summary.append("\n".join(stayed_lines) if stayed_lines else "Нет")
    summary.append("\nПревышение вместимости локаций:")
    summary.append("\n".join(overflow_lines) if overflow_lines else "Нет")
    return "\n".join(summary)


async def _notify_admins_night_summary(
    missing_lines: list[str], stayed_lines: list[str], overflow_lines: list[str], initiator_peer_id: int | None = None
) -> None:
    async with async_session() as session:
        admins_result = await session.execute(
            select(User).where(User.is_admin == True, User.vk_id.is_not(None))
        )
        admins = list(admins_result.scalars().all())
    recipient_peer_ids = {admin.vk_id for admin in admins if admin.vk_id}
    if not recipient_peer_ids:
        return
    text = _build_night_summary_text(missing_lines, stayed_lines, overflow_lines)
    for peer_id in recipient_peer_ids:
        await _send_vk_message(peer_id, text, keyboard=await _main_keyboard_for_peer(peer_id))


async def _do_night_toggle(message: Message) -> None:
    peer_id = message.peer_id
    async with async_session() as session:
        user = await get_user_from_vk(session, peer_id, None)
        if not user or not user.is_admin:
            await message.answer("Команда только для администраторов.")
            return
        result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
        settings = result.scalar_one_or_none()
        if not settings:
            settings = GameSettings(id=1, night_active=False, pause_active=False)
            session.add(settings)
            await session.flush()
        settings.night_active = not settings.night_active
        new_state = settings.night_active

        if new_state:
            # Старт ночи: открыть период
            period = NightPeriod(started_at=datetime.utcnow(), ended_at=None)
            session.add(period)
            await session.flush()
        else:
            # Конец ночи: закрыть текущий период и применить автодействия
            period = await _get_current_night_period(session)
            if period:
                logging.info(
                    "Закрываем период ночи: id=%s started_at=%s",
                    period.id,
                    period.started_at,
                )
                period.ended_at = datetime.utcnow()
            else:
                logging.warning("Ночь выключается, но открытый период ночи не найден.")
            missing_lines, stayed_lines, overflow_lines = await _handle_night_finished(session, period)
        await session.commit()

    status = "включена" if new_state else "выключена"
    await message.answer(
        f"🌙 Ночь {status}. Кнопка «Заночевать» теперь {'видна' if new_state else 'скрыта'} у всех."
    )
    if new_state:
        await _notify_night_started()
    else:
        # Гарантированно показываем сводку инициатору в текущем диалоге.
        await message.answer(
            _build_night_summary_text(missing_lines, stayed_lines, overflow_lines),
            keyboard=await _main_keyboard_for_peer(peer_id),
        )
        await _notify_admins_night_summary(
            missing_lines, stayed_lines, overflow_lines, initiator_peer_id=peer_id
        )


# ---------- Пауза (админ) ----------

async def _do_pause_toggle(message: Message) -> None:
    peer_id = message.peer_id
    async with async_session() as session:
        user = await get_user_from_vk(session, peer_id, None)
        if not user or not user.is_admin:
            await message.answer("Команда только для администраторов.")
            return
        result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
        settings = result.scalar_one_or_none()
        if not settings:
            settings = GameSettings(id=1, night_active=False, pause_active=False)
            session.add(settings)
            await session.flush()
        settings.pause_active = not settings.pause_active
        await session.commit()
        new_state = settings.pause_active

    status = "включена" if new_state else "выключена"
    await message.answer(f"⏸ Пауза {status}.")


@bot.on.message(CommandRule("pause", prefixes=["/", "!"], args_count=0))
async def vk_pause_command_rule(message: Message):
    await _do_pause_toggle(message)


@bot.on.message(CommandRule("night", prefixes=["/", "!"], args_count=0))
async def vk_night_toggle_command_rule(message: Message):
    await _do_night_toggle(message)


@bot.on.message(NightTextRule())
async def vk_night_toggle_text_fallback(message: Message):
    await _do_night_toggle(message)


# ---------- Заночевать (FSM) ----------

async def vk_night_finalize(message: Message):
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    data = fsm.get("data", {})
    location_id = data.get("location_id")
    food = data.get("food", 0)
    immunics = data.get("immunics", 0)
    painkiller = data.get("painkiller", False)
    use_melkiy = data.get("use_melkiy", False)
    fsm["state"] = None
    fsm["data"] = {}

    async with async_session() as session:
        location_result = await session.execute(select(Location).where(Location.id == location_id))
        location = location_result.scalar_one_or_none()
        if not location:
            await message.answer("Локация не найдена.")
            return
        current_user = await get_user_from_vk(session, peer_id, None)
        if not current_user or not current_user.is_alive:
            await message.answer("Персонаж не найден или мёртв.")
            return

        # Фиксируем факт ночёвки в текущем периоде ночи.
        current_period = await _get_current_night_period(session)
        # Страховка: если открытого периода нет, но ночь активна — создаём период лениво.
        if not current_period:
            gs_result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
            gs = gs_result.scalar_one_or_none()
            if gs and gs.night_active:
                current_period = NightPeriod(started_at=datetime.utcnow(), ended_at=None)
                session.add(current_period)
                await session.flush()
        if current_period:
            existing_stay_result = await session.execute(
                select(NightStay).where(
                    NightStay.period_id == current_period.id,
                    NightStay.user_id == current_user.id,
                )
            )
            existing_stay = existing_stay_result.scalars().first()
            if existing_stay:
                await message.answer("Вы уже заночевали в этом периоде ночи. Повторная ночёвка недоступна.")
                return
            session.add(
                NightStay(
                    period_id=current_period.id,
                    user_id=current_user.id,
                    location_id=location.id,
                    stayed_at=datetime.utcnow(),
                    auto_applied=False,
                )
            )

        active_skills = _user_active_skill_names(current_user)
        energy = 0
        if location.quality:
            energy += 1 if "Привычный к улице" in active_skills else 2
        if painkiller:
            energy += 2
        if food > 0:
            energy += min(food, 3)
        else:
            energy -= 1
        energy_drain = sum(
            1
            for slot in current_user.slots
            if slot.disease and getattr(slot.disease, "energy", False)
        )
        energy -= energy_drain
        if "Непоседа" in active_skills:
            energy += 2
        if use_melkiy:
            energy -= 1
        # Энергия не должна уходить ниже 0.
        energy = max(0, min(6, energy))

        msg = f"🌙 Ночёвка\n\nВосстановлено энергии: {energy}"
        if location.quality and "Густая кровь" in active_skills:
            wound_slots = [
                slot
                for slot in current_user.slots
                if slot.disease
                and slot.disease.type == DiseaseType.WOUND
                and slot.disease.kind != DiseaseKind.BULLET
            ]
            if wound_slots:
                healed_slot = wound_slots[0]
                wound_name = healed_slot.disease.name
                healed_slot.disease_id = None
                msg += f"\n\nНавык «Густая кровь»: вылечена рана {wound_name}."

        roll = random.randint(0, max(0, location.infection_chance))
        if current_user.is_child:
            roll -= 10
        if "Крепыш" in active_skills:
            roll -= 10
        roll -= 10 * immunics
        if roll > 0:
            infection_msg = await apply_infection(session, current_user)
            msg += f"\n\n🦠 Заражение: {infection_msg}"
        else:
            msg += "\n\n🦠 Заражения не произошло."
        msg += "\n\nВы можете посмотреть 1 сон из имеющихся у вас."
        await session.commit()

    await message.answer(
        msg,
        keyboard=await _main_keyboard_for_peer(message.peer_id),
    )


@bot.on.message(text="🌙 Заночевать")
async def vk_night_start(message: Message):
    night_active = await get_night_active()
    if not night_active:
        await message.answer("Сейчас не ночь. Заночевать нельзя.")
        return
    async with async_session() as session:
        current_user = await get_user_from_vk(session, message.peer_id, None)
        if current_user:
            current_period = await _get_current_night_period(session)
            if current_period:
                stay_result = await session.execute(
                    select(NightStay).where(
                        NightStay.period_id == current_period.id,
                        NightStay.user_id == current_user.id,
                    )
                )
                existing_stay = stay_result.scalars().first()
                if existing_stay:
                    await message.answer("Вы уже заночевали в этом периоде ночи.")
                    return
    fsm = get_fsm(message.peer_id)
    fsm["state"] = FsmState.NIGHT_LOCATION
    fsm["data"] = {}
    await message.answer("Введите код или название места ночёвки:")


# ---------- Вылечить травму (FSM) ----------

@bot.on.message(text="🩹 Вылечить травму")
async def vk_cure_trauma_start(message: Message):
    peer_id = message.peer_id
    async with async_session() as session:
        current_user = await get_user_from_vk(session, peer_id, None)
    if not current_user:
        await message.answer("Вы не подключены. Напишите /start для привязки.")
        return
    curable_traumas = [
        slot.disease
        for slot in (current_user.slots or [])
        if slot.disease
        and slot.disease.type == DiseaseType.TRAUMA
        and not getattr(slot.disease, "operation", False)
    ]
    if not curable_traumas:
        await message.answer("У вас нет травм, которые можно снять этим действием.")
        return
    get_fsm(peer_id)["state"] = FsmState.CURE_TRAUMA_CODE
    get_fsm(peer_id)["data"] = {}
    await message.answer(
        "Выберите травму для снятия по кнопке (или введите код числом):",
        keyboard=get_cure_trauma_keyboard_vk(curable_traumas),
    )


# ---------- Лечиться (FSM) — регистрируем ДО общего text="<text>", иначе не сработает ----------

@bot.on.message(text="💊 Лечиться обычными лекарствами")
async def vk_treat_start(message: Message):
    get_fsm(message.peer_id)["state"] = FsmState.TREAT_TARGET
    get_fsm(message.peer_id)["data"] = {"initiator_peer_id": message.peer_id}
    await message.answer("Укажите, кого лечите: введите @username игрока (TG или VK) или «себя».")


# ---------- Лечиться особым лекарством (FSM) — регистрируем ДО общего text="<text>" ----------

@bot.on.message(text="🧬 Лечиться особым лекарством")
async def vk_special_treat_start(message: Message):
    peer_id = message.peer_id
    get_fsm(peer_id)["state"] = FsmState.SPECIAL_TREAT_TARGET
    get_fsm(peer_id)["data"] = {"initiator_peer_id": peer_id}
    await message.answer("Укажите, кого лечите: введите @username игрока (TG или VK) или «себя».")


# ---------- Хирургическая операция (FSM) — только для навыка «Врач» ----------

@bot.on.message(text="🏥 Провести хирургическую операцию")
async def vk_surgery_start(message: Message):
    peer_id = message.peer_id
    async with async_session() as session:
        current_user = await get_user_from_vk(session, peer_id, None)
    if not current_user:
        await message.answer("Вы не подключены. Напишите /start для привязки.")
        return
    if not _user_has_doctor_skill(current_user):
        await message.answer("Действие доступно только персонажам с навыком «Врач».")
        return
    get_fsm(peer_id)["state"] = FsmState.SURGERY_CONFIRM_HOSPITAL
    get_fsm(peer_id)["data"] = {"initiator_peer_id": peer_id}
    await message.answer(
        "Подтверждаете, что находитесь в Госпитале?",
        keyboard=get_yes_no_keyboard_vk(),
    )

@bot.on.message(text="<text>")
async def vk_fsm_text_handler(message: Message, text: str):
    """Единый обработчик текста для FSM: привязка, ночёвка, лечение."""
    payload_cmd = get_payload_cmd(message)
    if payload_cmd and payload_cmd.startswith(("wound_", "trauma_", "cure_trauma_")):
        return False  # Эти payload-кнопки обрабатывает vk_payload_handler
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    raw_state = fsm.get("state")
    state = FsmState(raw_state) if isinstance(raw_state, str) else raw_state
    normalized_text = (text or "").strip()
    # Чтобы команду /pause не пыталась обработать FSM-логика
    normalized_cmd = normalized_text.lower().lstrip("!/ ")
    if normalized_cmd.startswith("pause"):
        return False

    # Привязка по username
    if state == FsmState.LINK_USERNAME:
        entered_username = normalized_text.replace("@", "")
        if not entered_username:
            await message.answer("Введите @username (TG) или short name (VK).")
            return
        async with async_session() as session:
            linked_user = await get_user_from_vk(session, peer_id, entered_username)
            if not linked_user:
                await message.answer("Пользователь с таким именем не найден в базе.")
                return
            await session.commit()
        fsm["state"] = None
        fsm["data"] = {}
        await message.answer(
            f"Привязано к персонажу {linked_user.character_name}. Добро пожаловать!",
            keyboard=await _main_keyboard_for_peer(peer_id),
        )
        return

    # Ночёвка FSM
    if state and state.value.startswith("night_"):
        night_active = await get_night_active()
        if not night_active:
            fsm["state"] = None
            fsm["data"] = {}
            await message.answer("Сейчас не ночь. Заночевать нельзя.")
            return
        match state:
            case FsmState.NIGHT_LOCATION:
                async with async_session() as session:
                    resolved_location = await _resolve_location(session, text)
                    if not resolved_location:
                        await message.answer("Локация не найдена. Введите код или название:")
                        return
                    location_name = (resolved_location.name or "").strip().lower()
                    is_steppe = (resolved_location.code == 1) or (location_name == "степь")
                    current_user = await get_user_from_vk(session, peer_id, None)
                    if (
                        is_steppe
                        and current_user
                        and "привычный к степи"
                        not in {s.lower() for s in _user_active_skill_names(current_user)}
                    ):
                        fsm["state"] = None
                        await message.answer("Вы не можете ночевать здесь: нет навыка «Привычный к степи».")
                        return
                    fsm["data"]["location_id"] = resolved_location.id
                    # По умолчанию навык «Мелкий» не используется (если не спросили — значит false)
                    fsm["data"]["use_melkiy"] = False
                    if resolved_location.quality and current_user and "Мелкий" in _user_active_skill_names(current_user):
                        fsm["state"] = FsmState.NIGHT_USE_MELKIY
                        await message.answer(
                            "Использовать навык «Мелкий»?",
                            keyboard=get_yes_no_keyboard_vk(),
                        )
                        return
                fsm["state"] = FsmState.NIGHT_FOOD
                await message.answer("Введите количество съеденной еды (0–3):")
                return

            case FsmState.NIGHT_USE_MELKIY:
                fsm["data"]["use_melkiy"] = _is_yes_answer(normalized_text)
                fsm["state"] = FsmState.NIGHT_FOOD
                await message.answer("Введите количество съеденной еды (0–3):")
                return

            case FsmState.NIGHT_FOOD:
                try:
                    food = max(0, min(3, int((text or "0").strip())))
                except ValueError:
                    await message.answer("Введите число от 0 до 3.")
                    return
                fsm["data"]["food"] = food
                fsm["state"] = FsmState.NIGHT_IMMUNICS
                await message.answer("Введите количество использованных иммуников:")
                return

            case FsmState.NIGHT_IMMUNICS:
                try:
                    immunics = max(0, int((text or "0").strip()))
                except ValueError:
                    await message.answer("Введите неотрицательное число.")
                    return
                fsm["data"]["immunics"] = immunics
                fsm["state"] = FsmState.NIGHT_PAINKILLER
                await message.answer(
                    "Использовали обезболивающее?",
                    keyboard=get_yes_no_keyboard_vk(),
                )
                return

            case FsmState.NIGHT_PAINKILLER:
                fsm["data"]["painkiller"] = _is_yes_answer(normalized_text)
                await vk_night_finalize(message)
                return

    # Вылечить травму по коду
    if state == FsmState.CURE_TRAUMA_CODE:
        try:
            trauma_code = int((text or "").strip())
        except ValueError:
            await message.answer("Введите число — код травмы.")
            return
        peer_id = message.peer_id
        fsm["state"] = None
        fsm["data"] = {}
        async with async_session() as session:
            current_user = await get_user_from_vk(session, peer_id, None)
            if not current_user:
                await message.answer("Вы не подключены.")
                return
            slot_with_trauma = None
            for slot in current_user.slots:
                if slot.disease and slot.disease.type == DiseaseType.TRAUMA:
                    code = slot.disease.trauma_code if slot.disease.trauma_code is not None else slot.disease.id
                    if code == trauma_code:
                        if getattr(slot.disease, "operation", False):
                            await message.answer("Эту травму нельзя снять действием «Вылечить травму» — она предназначена для операции.")
                            return
                        slot_with_trauma = slot
                        trauma_name = slot.disease.name
                        break
            if slot_with_trauma is None:
                await message.answer("У вас нет травмы с таким кодом.")
                return
            slot_with_trauma.disease_id = None
            await session.commit()
        await message.answer(
            f"Травма «{trauma_name}» снята.",
            keyboard=await _main_keyboard_for_peer(peer_id),
        )
        return

    # Лечение и крафт FSM
    match state:
        case FsmState.TREAT_TARGET:
            target_input = normalized_text.lower().replace("@", "")
            if not target_input:
                await message.answer("Введите @username или «себя».")
                return
            if target_input == "себя":
                async with async_session() as session:
                    current_user = await get_user_from_vk(session, peer_id, None)
                    target_username = current_user.tg_username if current_user else None
                if not target_username:
                    await message.answer("Вы не привязаны. Введите username.")
                    return
            else:
                target_username = target_input
            await vk_treat_target_next(message, target_username)
            return

        case FsmState.SPECIAL_TREAT_TARGET:
            target_input = normalized_text.lower().replace("@", "")
            if not target_input:
                await message.answer("Введите @username или «себя».")
                return
            if target_input == "себя":
                async with async_session() as session:
                    current_user = await get_user_from_vk(session, peer_id, None)
                    target_username = current_user.tg_username if current_user else None
                if not target_username:
                    await message.answer("Вы не привязаны. Введите username.")
                    return
            else:
                target_username = target_input
            await vk_special_treat_target_next(message, target_username)
            return

        case FsmState.TREAT_MEDICINES:
            await vk_treat_medicines_next(message, text or "")
            return

        case FsmState.SPECIAL_TREAT_CODE:
            await vk_special_treat_code_next(message, text or "")
            return

        case FsmState.SURGERY_CONFIRM_HOSPITAL:
            if not _is_yes_answer(normalized_text):
                fsm["state"] = None
                fsm["data"] = {}
                await message.answer("Действие отменено. Подтвердите нахождение в Госпитале для проведения операции.")
                return
            fsm["state"] = FsmState.SURGERY_TARGET
            await message.answer("Укажите, кого оперируете: введите @username игрока (TG или VK) или «себя».")
            return

        case FsmState.SURGERY_TARGET:
            target_input = normalized_text.lower().replace("@", "")
            if not target_input:
                await message.answer("Введите @username или «себя».")
                return
            if target_input == "себя":
                fsm["state"] = None
                fsm["data"] = {}
                await message.answer(
                    "Невозможно провести операцию над собой. Действие завершено.",
                    keyboard=await _main_keyboard_for_peer(peer_id),
                )
                return
            await vk_surgery_target_next(message, target_input)
            return

        case FsmState.SURGERY_MEDICINES:
            await vk_surgery_medicines_next(message, text or "")
            return


async def vk_treat_target_next(message: Message, target_username: str):
    from datetime import datetime
    peer_id = message.peer_id
    async with async_session() as session:
        patient_result = await session.execute(
            select(User)
            .options(
                selectinload(User.slots).selectinload(Slot.skill),
                selectinload(User.slots).selectinload(Slot.disease),
            )
            .where(
                or_(User.tg_username == target_username, User.vk_username == target_username)
            )
        )
        patient = patient_result.scalar_one_or_none()
    if not patient:
        await message.answer("Игрок с таким именем не найден.")
        return
    if not patient.is_alive:
        get_fsm(peer_id)["state"] = None
        get_fsm(peer_id)["data"] = {}
        await message.answer("Невозможно провести лечение: игрок мёртв.")
        return
    if patient.infection_status != InfectionStatus.INFECTED:
        get_fsm(peer_id)["state"] = None
        await message.answer("Невозможно провести лечение: у игрока нет статуса «Заражён».")
        return
    if patient.last_cure_time:
        now = datetime.utcnow()
        last = patient.last_cure_time
        if last.tzinfo:
            last = last.replace(tzinfo=None)
        if (now - last).total_seconds() < CURE_COOLDOWN_HOURS * 3600:
            get_fsm(peer_id)["state"] = None
            await message.answer("Невозможно провести лечение: игрок лечился менее часа назад.")
            return
    get_fsm(peer_id)["data"]["target_username"] = target_username
    get_fsm(peer_id)["state"] = FsmState.TREAT_MEDICINES
    await message.answer("Введите коды использованных лекарств через пробел или запятую (например: 1 2 3).")


async def vk_special_treat_target_next(message: Message, target_username: str):
    """Переход: выбрана цель для лечения особым лекарством."""
    from datetime import datetime

    peer_id = message.peer_id
    async with async_session() as session:
        patient_result = await session.execute(
            select(User)
            .options(
                selectinload(User.slots).selectinload(Slot.skill),
                selectinload(User.slots).selectinload(Slot.disease),
            )
            .where(or_(User.tg_username == target_username, User.vk_username == target_username))
        )
        patient = patient_result.scalar_one_or_none()

    if not patient:
        await message.answer("Игрок с таким именем не найден.")
        return
    if not patient.is_alive:
        get_fsm(peer_id)["state"] = None
        get_fsm(peer_id)["data"] = {}
        await message.answer("Невозможно провести лечение: игрок мёртв.")
        return

    if patient.last_cure_time:
        now = datetime.utcnow()
        last = patient.last_cure_time
        if last.tzinfo:
            last = last.replace(tzinfo=None)
        if (now - last).total_seconds() < CURE_COOLDOWN_HOURS * 3600:
            get_fsm(peer_id)["state"] = None
            get_fsm(peer_id)["data"] = {}
            await message.answer("Невозможно провести лечение: игрок лечился менее часа назад.")
            return

    # По ТЗ проверка «Заражён» обязательна, но вакцина работает и на «Здоров».
    # Поэтому проверяем после ввода кода: если это не вакцина — требуется «Заражён».
    get_fsm(peer_id)["data"]["target_username"] = target_username
    get_fsm(peer_id)["state"] = FsmState.SPECIAL_TREAT_CODE
    await message.answer("Введите 1 код особого лекарства.")


async def vk_special_treat_code_next(message: Message, raw_code: str):
    """Финализация: применить особое лекарство по коду."""
    from datetime import datetime

    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    target_username = fsm.get("data", {}).get("target_username")
    fsm["state"] = None
    fsm["data"] = {}

    try:
        code = int((raw_code or "").strip())
    except ValueError:
        await message.answer("Введите код особого лекарства числом.")
        return

    async with async_session() as session:
        med_result = await session.execute(select(Medicine).where(Medicine.code == code))
        medicine = med_result.scalar_one_or_none()
        if not medicine:
            await message.answer("Лекарство с таким кодом не найдено в базе.")
            return
        if medicine.med_type not in SPECIAL_MED_TYPES:
            await message.answer("Нельзя использовать обычные лекарства в этом действии. Нужен код особого лекарства.")
            return
        kind = SPECIAL_MEDICINE_KIND_BY_TYPE.get(medicine.med_type, "Особое")

        patient_result = await session.execute(
            select(User)
            .options(
                selectinload(User.slots).selectinload(Slot.skill),
                selectinload(User.slots).selectinload(Slot.disease),
            )
            .where(or_(User.tg_username == target_username, User.vk_username == target_username))
        )
        patient = patient_result.scalar_one_or_none()
        if not patient:
            await message.answer("Игрок не найден.")
            return
        if not patient.is_alive:
            await message.answer("Невозможно провести лечение: игрок мёртв.")
            return

        # Требование статуса «Заражён» для всех, кроме вакцины на здоровом
        if kind != "Вакцина" and patient.infection_status != InfectionStatus.INFECTED:
            await message.answer("Невозможно провести лечение: у выбранного игрока нет статуса «Заражён».")
            return

        # Кулдаун (повторная защита на момент применения)
        if patient.last_cure_time:
            now = datetime.utcnow()
            last = patient.last_cure_time
            if last.tzinfo:
                last = last.replace(tzinfo=None)
            if (now - last).total_seconds() < CURE_COOLDOWN_HOURS * 3600:
                await message.answer("Невозможно провести лечение: игрок лечился менее часа назад.")
                return

        def clear_symptoms() -> list[str]:
            removed_names: list[str] = []
            for slot in patient.slots:
                if slot.disease and slot.disease.type == DiseaseType.SYMPTOM:
                    removed_names.append(slot.disease.name)
                    slot.disease_id = None
            return removed_names

        consequence_msgs: list[str] = []
        if kind == "Панацея":
            removed_names = clear_symptoms()
            patient.infection_status = InfectionStatus.HEALTHY
            if removed_names:
                consequence_msgs.append("Снятые симптомы:\n" + "\n".join(f"• {n}" for n in removed_names))
            else:
                consequence_msgs.append("Снятые симптомы: нет.")

        elif kind == "Вакцина":
            if patient.infection_status == InfectionStatus.HEALTHY:
                patient.infection_status = InfectionStatus.VACCINATED
                consequence_msgs.append("Статус изменён на «Вакцинирован».")
            else:
                consequence_msgs.append("На заражённого вакцина не действует.")

        elif kind == "Порошочек":
            removed_names = clear_symptoms()
            patient.infection_status = InfectionStatus.HEALTHY
            if removed_names:
                consequence_msgs.append("Снятые симптомы:\n" + "\n".join(f"• {n}" for n in removed_names))
            else:
                consequence_msgs.append("Снятые симптомы: нет.")

            comp_result = await session.execute(
                select(Complication).where(Complication.disease_comp_type.is_not(None))
            )
            pool = list(comp_result.scalars().all())

            severe_used = 0
            trauma_used = 0
            chosen: list[Complication] = []
            random.shuffle(pool)
            for comp in pool:
                if len(chosen) >= 3:
                    break
                if comp.disease_comp_type and comp.disease_comp_type.value == "Тяжёлое":
                    if severe_used >= 1:
                        continue
                if comp.trauma_code is not None:
                    if trauma_used >= 2:
                        continue
                chosen.append(comp)
                if comp.disease_comp_type and comp.disease_comp_type.value == "Тяжёлое":
                    severe_used += 1
                if comp.trauma_code is not None:
                    trauma_used += 1

            for comp in chosen:
                if comp.trauma_code is not None:
                    ok, trauma_msg = await _apply_trauma_by_code(session, patient, comp.trauma_code)
                    if ok:
                        consequence_msgs.append(f"Применена травма: {trauma_msg}")
                    else:
                        consequence_msgs.append(f"Не удалось применить травму: {trauma_msg}")
                else:
                    consequence_msgs.append(f"Осложнение: {comp.name} — {comp.description}")

        patient.last_cure_time = datetime.utcnow()
        await session.commit()

    msg = f"🧬 Особое лечение проведено: {kind}."
    if consequence_msgs:
        msg += "\n\n" + "\n".join(consequence_msgs)
    await message.answer(msg, keyboard=await _main_keyboard_for_peer(peer_id))


async def vk_surgery_target_next(message: Message, target_username: str):
    """После выбора цели операции: проверка наличия болячки с операция=да."""
    peer_id = message.peer_id
    async with async_session() as session:
        patient_result = await session.execute(
            select(User)
            .options(
                selectinload(User.slots).selectinload(Slot.skill),
                selectinload(User.slots).selectinload(Slot.disease),
            )
            .where(or_(User.tg_username == target_username, User.vk_username == target_username))
        )
        patient = patient_result.scalar_one_or_none()
    if not patient:
        get_fsm(peer_id)["state"] = None
        get_fsm(peer_id)["data"] = {}
        await message.answer("Игрок с таким именем не найден.")
        return
    if not patient.is_alive:
        get_fsm(peer_id)["state"] = None
        get_fsm(peer_id)["data"] = {}
        await message.answer("Невозможно провести операцию: пациент мёртв.")
        return
    has_operable = any(
        s.disease and getattr(s.disease, "operation", False) for s in patient.slots
    )
    if not has_operable:
        get_fsm(peer_id)["state"] = None
        get_fsm(peer_id)["data"] = {}
        await message.answer(
            "Невозможно провести операцию: у выбранного игрока нет ни одной болячки с операция = «да»."
        )
        return
    get_fsm(peer_id)["data"]["surgery_target_username"] = target_username
    get_fsm(peer_id)["state"] = FsmState.SURGERY_MEDICINES
    await message.answer(
        "Введите количество использованных лекарств по типам (кроме особых): "
        "иммуники, антибиотики, обезболивающие — три числа через пробел или запятую (например: 2 1 0)."
    )


async def _do_surgery_finalize(
    session,
    patient,
    immunics_count: int,
    antibiotics_count: int,
    painkillers_count: int,
) -> tuple[list[str], bool]:
    """
    Проводит хирургическую операцию: боль, осложнения, снятие болячек с операция=да.
    Возвращает (список строк для сообщения, patient_died).
    """
    from datetime import datetime

    settings_result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
    settings = settings_result.scalar_one_or_none()
    pain_death_threshold = settings.pain_death_threshold if settings else 10
    pain_consequence_divisor = settings.pain_consequence_divisor if settings else 3
    pain_wound_mod = settings.pain_wound_mod if settings else 0
    light_comp_mod = settings.light_comp_mod if settings else 0
    severe_comp_mod = settings.severe_comp_mod if settings else 0

    # Боль от лекарств: сумма боли по первым N лекарствам каждого типа (по коду)
    medicine_pain = 0
    for med_type, count in [
        (MedType.IMMUNIC, immunics_count),
        (MedType.ANTIBIOTIC, antibiotics_count),
        (MedType.PAINKILLER, painkillers_count),
    ]:
        if count <= 0:
            continue
        res = await session.execute(
            select(Medicine).where(Medicine.med_type == med_type).order_by(Medicine.code)
        )
        meds = list(res.scalars().all())[:count]
        medicine_pain += sum(m.pain or 0 for m in meds)

    pain_sum = 0
    for s in patient.slots:
        if s.skill and s.disease_id is None:
            pain_sum += s.skill.pain or 0
        if s.disease:
            pain_sum += s.disease.pain or 0
    pain_sum += medicine_pain + pain_wound_mod

    heavy_pain = 0
    light_pain = 0
    if pain_sum > pain_death_threshold + pain_consequence_divisor:
        heavy_pain = 1
        light_pain = 1
    elif pain_sum > pain_death_threshold:
        heavy_pain = 1
    elif pain_sum > pain_consequence_divisor:
        light_pain = 1

    if pain_sum > pain_death_threshold:
        patient.is_alive = False

    # Лёгкие осложнения: модификатор + активные болячки + лёгкая боль − иммуники, затем гасим антибиотиками
    light_comp_sum = light_comp_mod + light_pain
    for s in patient.slots:
        if s.disease and getattr(s.disease, "light_complication", False):
            light_comp_sum += 1
    after_immunics = max(0, light_comp_sum - immunics_count)
    abx_left = antibiotics_count
    while after_immunics > 0 and abx_left > 0:
        after_immunics -= 1
        abx_left -= 1
    light_final = after_immunics

    # Тяжёлые осложнения: модификатор + активные болячки + тяжёлая боль − оставшиеся антибиотики
    severe_comp_sum = severe_comp_mod + heavy_pain
    for s in patient.slots:
        if s.disease and getattr(s.disease, "severe_complication", False):
            severe_comp_sum += 1
    severe_final = max(0, severe_comp_sum - abx_left)

    # Убрать все болячки с операция = да
    for s in patient.slots:
        if s.disease and getattr(s.disease, "operation", False):
            s.disease_id = None

    msgs: list[str] = []
    severe_pool_result = await session.execute(
        select(Complication).where(Complication.disease_comp_type == DiseaseCompType.SEVERE)
    )
    severe_pool = list(severe_pool_result.scalars().all())
    light_pool_result = await session.execute(
        select(Complication).where(Complication.disease_comp_type == DiseaseCompType.LIGHT)
    )
    light_pool = list(light_pool_result.scalars().all())

    for _ in range(severe_final):
        if not severe_pool:
            break
        comp = random.choice(severe_pool)
        if comp.trauma_code is not None:
            ok, trauma_msg = await _apply_trauma_by_code(session, patient, comp.trauma_code)
            msgs.append(f"Тяжёлое осложнение (травма): {trauma_msg}")
        else:
            msgs.append(f"Тяжёлое осложнение: {comp.name} — {comp.description}")

    for _ in range(light_final):
        if not light_pool:
            break
        comp = random.choice(light_pool)
        if comp.trauma_code is not None:
            ok, trauma_msg = await _apply_trauma_by_code(session, patient, comp.trauma_code)
            msgs.append(f"Лёгкое осложнение (травма): {trauma_msg}")
        else:
            msgs.append(f"Лёгкое осложнение: {comp.name} — {comp.description}")

    patient_died = not patient.is_alive
    return msgs, patient_died


async def vk_surgery_medicines_next(message: Message, raw: str):
    """Ввод лекарств и финализация хирургической операции."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    target_username = fsm.get("data", {}).get("surgery_target_username")
    initiator_peer_id = fsm.get("data", {}).get("initiator_peer_id", peer_id)
    fsm["state"] = None
    fsm["data"] = {}

    parts = []
    for x in (raw or "").replace(",", " ").split():
        try:
            parts.append(max(0, int(x.strip())))
        except ValueError:
            continue
    if len(parts) < 3:
        await message.answer(
            "Введите три числа: иммуники, антибиотики, обезболивающие (например: 2 1 0)."
        )
        return
    immunics_count, antibiotics_count, painkillers_count = parts[0], parts[1], parts[2]

    async with async_session() as session:
        patient_result = await session.execute(
            select(User)
            .options(
                selectinload(User.slots).selectinload(Slot.skill),
                selectinload(User.slots).selectinload(Slot.disease),
            )
            .where(or_(User.tg_username == target_username, User.vk_username == target_username))
        )
        patient = patient_result.scalar_one_or_none()
        if not patient:
            await message.answer("Пациент не найден.")
            return
        if not patient.is_alive:
            await message.answer("Невозможно провести операцию: пациент мёртв.")
            return
        msgs, patient_died = await _do_surgery_finalize(
            session, patient, immunics_count, antibiotics_count, painkillers_count
        )
        await session.commit()

    result = "🏥 Хирургическая операция проведена."
    if msgs:
        result += "\n\nОсложнения:\n" + "\n".join(msgs)
    await message.answer(result, keyboard=await _main_keyboard_for_peer(peer_id))
    if patient_died:
        await message.answer(
            "Пациент умер в результате операции. Напоминание: необходимо получить метку репутации."
        )


async def vk_treat_medicines_next(message: Message, raw_codes: str):
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    medicine_codes: list[int] = []
    for x in raw_codes.replace(",", " ").split():
        try:
            medicine_codes.append(int(x.strip()))
        except ValueError:
            continue
    if not medicine_codes:
        await message.answer("Введите хотя бы один код лекарства.")
        return
    async with async_session() as session:
        medicines_result = await session.execute(select(Medicine).where(Medicine.code.in_(medicine_codes)))
        medicines = list(medicines_result.scalars().all())
    found_codes = {m.code for m in medicines}
    missing_codes = [code for code in sorted(set(medicine_codes)) if code not in found_codes]
    if missing_codes:
        await message.answer(f"Не найдены лекарства с кодами: {missing_codes}. Введите коды снова.")
        return
    if any(m.med_type in SPECIAL_MED_TYPES for m in medicines):
        fsm["state"] = None
        await message.answer("Невозможно провести лечение: использовано лекарство вида «Особое».")
        return

    target_username = fsm["data"].get("target_username")
    async with async_session() as session:
        msg, _ = await do_treat_finalize(session, target_username, medicine_codes)
    fsm["state"] = None
    fsm["data"] = {}
    await message.answer(msg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    # run_forever() — синхронная точка входа, сам управляет event loop (нельзя вызывать из asyncio.run + run_polling)
    bot.run_forever()
