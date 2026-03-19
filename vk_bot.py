"""
VK-бот: дублирует функциональность TG-бота (профиль, ранения, травмы, заражение,
лечение, крафт, ночёвка, режим ночи для админов).
"""
import asyncio
import json
import logging
import sys
from enum import Enum
from typing import Any

from vkbottle import Bot, Keyboard, Text
from vkbottle.bot import Message
from vkbottle.dispatch.rules import ABCRule
from vkbottle.dispatch.rules.base import CommandRule

import random
from sqlalchemy import select, and_, or_
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
    Ingredient,
    IngredientName,
    IngredientCategory,
    GameSettings,
)
from game_logic import (
    MEDICINE_RECIPES,
    RECIPE_TO_MED_TYPE,
    CURE_COOLDOWN_HOURS,
    PAIN_DEATH_THRESHOLD,
    PAIN_CONSEQUENCE_DIVISOR,
    _user_has_medicine_recipe,
    _user_medicine_recipes,
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

# FSM: peer_id -> {"state": FsmState | None, "data": dict}
vk_fsm: dict[int, dict[str, Any]] = {}


class FsmState(str, Enum):
    """Состояния FSM (привязка, ночёвка, лечение, крафт, вылечить травму)."""
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
    CRAFT_ALCHEMY = "craft_alchemy"
    CRAFT_MED_TYPE = "craft_med_type"
    CRAFT_INGREDIENT1 = "craft_ingredient1"
    CRAFT_INGREDIENT2 = "craft_ingredient2"
    RESEARCH_CONFIRM_LAB = "research_confirm_lab"
    RESEARCH_REAGENT_CODE = "research_reagent_code"
    RESEARCH_OBJECT_TYPE = "research_object_type"
    RESEARCH_INGREDIENT_NAME = "research_ingredient_name"
    RESEARCH_BLUE_MARK = "research_blue_mark"
    RESEARCH_MEDICINE_CODE = "research_medicine_code"
    RESEARCH_CORPSE_ID = "research_corpse_id"


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


def _user_has_trauma(user) -> bool:
    """Есть ли у игрока хотя бы одна травма (слот с болезнью типа TRAUMA)."""
    if not getattr(user, "slots", None):
        return False
    return any(s.disease and s.disease.type == DiseaseType.TRAUMA for s in user.slots)


def _user_has_doctor_skill(user) -> bool:
    """Есть ли у игрока навык «Врач» (активный, слот не заблокирован)."""
    return "Врач" in _user_active_skill_names(user)


def get_main_keyboard_vk(
    night_visible: bool = False,
    has_craft_recipe: bool = False,
    has_trauma: bool = False,
    has_doctor: bool = False,
) -> str:
    k = Keyboard(one_time=False)
    k.add(Text("👤 Мой профиль"))
    k.row()
    k.add(Text("🩸 Получить ранение"))
    k.add(Text("🦴 Получить травму"))
    k.row()
    k.add(Text("🦠 Получить заражение"))
    k.add(Text("💊 Лечиться обычными лекарствами"))
    k.row()
    k.add(Text("🧬 Лечиться особым лекарством"))
    if has_trauma:
        k.row()
        k.add(Text("🩹 Вылечить травму"))
    if has_doctor:
        k.row()
        k.add(Text("🏥 Провести хирургическую операцию"))
        k.add(Text("🔬 Провести исследование"))
    if has_craft_recipe:
        k.row()
        k.add(Text("🧪 Создать лекарство"))
    if night_visible:
        k.row()
        k.add(Text("🌙 Заночевать"))
    return k.get_json()


def get_wound_keyboard_vk() -> str:
    k = Keyboard(one_time=True)
    k.add(Text("Небоевая", payload={"cmd": "wound_NON_COMBAT"}))
    k.row()
    k.add(Text("Ножевая", payload={"cmd": "wound_KNIFE"}))
    k.add(Text("Пулевая", payload={"cmd": "wound_BULLET"}))
    return k.get_json()


def get_trauma_keyboard_vk(traumas: list) -> str:
    k = Keyboard(one_time=True)
    for trauma in traumas:
        trauma_code = trauma.trauma_code if trauma.trauma_code is not None else trauma.id
        k.add(Text(f"{trauma.name} (код {trauma_code})", payload={"cmd": f"trauma_{trauma_code}"}))
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
    """Правило: сообщение с payload (нажатие кнопки)."""
    async def check(self, message: Message) -> bool:
        return bool(getattr(message, "payload", None))


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

    night_active = await get_night_active()
    has_craft = _user_has_medicine_recipe(user)
    has_trauma = _user_has_trauma(user)
    has_doctor = _user_has_doctor_skill(user)
    name = user.character_name or user.tg_username or "Игрок"
    await message.answer(
        f"Добро пожаловать, {name}! Ваш профиль активирован.",
        keyboard=get_main_keyboard_vk(night_active, has_craft, has_trauma, has_doctor)
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
                skills.append(slot.skill.name)

    status_text = current_user.infection_status.value if current_user.infection_status else "Здоров"
    char_name = current_user.character_name or "Неизвестный"
    msg = f"👤 Профиль игрока {char_name}\n\n"
    msg += f"🦠 Статус инфекции: {status_text}\n"
    msg += f"❤️ Здоровье: {health_current} / {health_max}\n"
    msg += f"🩸 Ран: {wounds}\n\n"
    msg += "Травмы:\n" + ("\n".join(set(traumas_text)) if traumas_text else "Нет") + "\n\n"
    msg += "Симптомы:\n" + ("\n".join(set(symptoms_text)) if symptoms_text else "Нет") + "\n\n"
    msg += "Активные навыки:\n" + ("\n".join(f"🧠 {s}" for s in skills) if skills else "Нет")

    night_active = await get_night_active()
    has_craft = _user_has_medicine_recipe(current_user)
    has_trauma = _user_has_trauma(current_user)
    has_doctor = _user_has_doctor_skill(current_user)
    await message.answer(msg, keyboard=get_main_keyboard_vk(night_active, has_craft, has_trauma, has_doctor))


# ---------- Привязка по username (внутри единого FSM-обработчика) ----------


# ---------- Ранение ----------

@bot.on.message(text="🩸 Получить ранение")
async def vk_wound_start(message: Message):
    await message.answer("Выберите тип ранения:", keyboard=get_wound_keyboard_vk())


@bot.on.message(HasPayloadRule())
async def vk_payload_handler(message: Message):
    """Обработка нажатий кнопок: ранение (wound_*) и травма (trauma_*)."""
    payload_cmd = get_payload_cmd(message)
    if not payload_cmd:
        return
    peer_id = message.peer_id

    if payload_cmd.startswith("wound_"):
        wound_kind_name = payload_cmd.split("_", 1)[1]
        wound_kind = getattr(DiseaseKind, wound_kind_name, None)
        if wound_kind is None:
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
                select(Disease).where(Disease.type == DiseaseType.WOUND, Disease.kind == wound_kind)
            )
            wound_disease = disease_result.scalars().first()
            if not wound_disease:
                wound_disease = Disease(
                    name=f"{wound_kind.value} рана",
                    type=DiseaseType.WOUND,
                    kind=wound_kind,
                )
                session.add(wound_disease)
                await session.flush()
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
            keyboard=get_main_keyboard_vk(
                await get_night_active(),
                _user_has_medicine_recipe(current_user),
                _user_has_trauma(current_user),
                _user_has_doctor_skill(current_user),
            ),
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
            keyboard=get_main_keyboard_vk(
                await get_night_active(),
                _user_has_medicine_recipe(current_user),
                _user_has_trauma(current_user),
                _user_has_doctor_skill(current_user),
            ),
        )
        return True

    return False


# ---------- Травма ----------

@bot.on.message(text="🦴 Получить травму")
async def vk_trauma_start(message: Message):
    async with async_session() as session:
        result = await session.execute(
            select(Disease).where(Disease.type == DiseaseType.TRAUMA).order_by(Disease.trauma_code)
        )
        traumas = list(result.scalars().all())
    if not traumas:
        await message.answer("В базе нет травм.")
        return
    await message.answer("Выберите травму по коду:", keyboard=get_trauma_keyboard_vk(traumas))


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
            select(Medicine).where(Medicine.med_type != MedType.SPECIAL).order_by(Medicine.code)
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
            settings = GameSettings(id=1, night_active=False)
            session.add(settings)
            await session.flush()
        settings.night_active = not settings.night_active
        await session.commit()
        new_state = settings.night_active

    status = "включена" if new_state else "выключена"
    await message.answer(
        f"🌙 Ночь {status}. Кнопка «Заночевать» теперь {'видна' if new_state else 'скрыта'} у всех."
    )


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
        energy = min(6, energy)

        msg = f"🌙 Ночёвка\n\nВосстановлено энергии: {energy}"
        if "Густая кровь" in active_skills:
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
        keyboard=get_main_keyboard_vk(
            True,
            _user_has_medicine_recipe(current_user),
            _user_has_trauma(current_user),
            _user_has_doctor_skill(current_user),
        ),
    )


@bot.on.message(text="🌙 Заночевать")
async def vk_night_start(message: Message):
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
    get_fsm(peer_id)["state"] = FsmState.CURE_TRAUMA_CODE
    get_fsm(peer_id)["data"] = {}
    await message.answer("Введите код травмы (число):")


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
    await message.answer("Подтверждаете, что находитесь в Госпитале? (да / нет)")


@bot.on.message(text="🔬 Провести исследование")
async def vk_research_start(message: Message):
    peer_id = message.peer_id
    async with async_session() as session:
        current_user = await get_user_from_vk(session, peer_id, None)
    if not current_user:
        await message.answer("Вы не подключены. Напишите /start для привязки.")
        return
    if not _user_has_doctor_skill(current_user):
        await message.answer("Действие доступно только персонажам с навыком «Врач».")
        return
    get_fsm(peer_id)["state"] = FsmState.RESEARCH_CONFIRM_LAB
    get_fsm(peer_id)["data"] = {}
    await message.answer("Подтверждаете работу за лабораторным столом? (да / нет)")


# ---------- Создать лекарство (FSM) — регистрируем ДО общего text="<text>", иначе не сработает ----------

@bot.on.message(text="🧪 Создать лекарство")
async def vk_craft_start(message: Message):
    peer_id = message.peer_id
    async with async_session() as session:
        user = await get_user_from_vk(session, peer_id, None)
    if not user:
        await message.answer("Вы не подключены.")
        return
    if not _user_has_medicine_recipe(user):
        await message.answer("У вас нет рецептов создания лекарств.")
        return
    recipes = _user_medicine_recipes(user)
    if not recipes:
        await message.answer("Нет рецептов.")
        return
    get_fsm(peer_id)["state"] = FsmState.CRAFT_ALCHEMY
    get_fsm(peer_id)["data"] = {"craft_peer_id": peer_id}
    await message.answer("Используете алхимический стол? (да / нет)")


@bot.on.message(text="<text>")
async def vk_fsm_text_handler(message: Message, text: str):
    """Единый обработчик текста для FSM: привязка, ночёвка, лечение, крафт."""
    if get_payload_cmd(message):
        return False  # Кнопки обрабатывает vk_payload_handler
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    raw_state = fsm.get("state")
    state = FsmState(raw_state) if isinstance(raw_state, str) else raw_state
    normalized_text = (text or "").strip()

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
        night_active = await get_night_active()
        has_craft = _user_has_medicine_recipe(linked_user)
        has_trauma = _user_has_trauma(linked_user)
        has_doctor = _user_has_doctor_skill(linked_user)
        await message.answer(
            f"Привязано к персонажу {linked_user.character_name}. Добро пожаловать!",
            keyboard=get_main_keyboard_vk(night_active, has_craft, has_trauma, has_doctor)
        )
        return

    # Ночёвка FSM
    if state and state.value.startswith("night_"):
        match state:
            case FsmState.NIGHT_LOCATION:
                async with async_session() as session:
                    resolved_location = await _resolve_location(session, text)
                    if not resolved_location:
                        await message.answer("Локация не найдена. Введите код или название:")
                        return
                    location_name = (resolved_location.name or "").strip().lower()
                    current_user = await get_user_from_vk(session, peer_id, None)
                    if (
                        location_name == "степь"
                        and current_user
                        and "Привычный к степи" not in _user_active_skill_names(current_user)
                    ):
                        fsm["state"] = None
                        await message.answer("Вы не можете ночевать здесь: нет навыка «Привычный к степи».")
                        return
                    fsm["data"]["location_id"] = resolved_location.id
                    # По умолчанию навык «Мелкий» не используется (если не спросили — значит false)
                    fsm["data"]["use_melkiy"] = False
                    if resolved_location.quality and current_user and "Мелкий" in _user_active_skill_names(current_user):
                        fsm["state"] = FsmState.NIGHT_USE_MELKIY
                        await message.answer("Использовать навык «Мелкий»? (да / нет)")
                        return
                fsm["state"] = FsmState.NIGHT_FOOD
                await message.answer("Введите количество съеденной еды (0–3):")
                return

            case FsmState.NIGHT_USE_MELKIY:
                fsm["data"]["use_melkiy"] = normalized_text.lower() in ("да", "yes", "1")
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
                await message.answer("Использовали обезболивающее? (да / нет)")
                return

            case FsmState.NIGHT_PAINKILLER:
                fsm["data"]["painkiller"] = normalized_text.lower() in ("да", "yes", "1")
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
                        slot_with_trauma = slot
                        trauma_name = slot.disease.name
                        break
            if slot_with_trauma is None:
                await message.answer("У вас нет травмы с таким кодом.")
                return
            slot_with_trauma.disease_id = None
            await session.commit()
        night_active = await get_night_active()
        has_craft = _user_has_medicine_recipe(current_user)
        has_trauma = _user_has_trauma(current_user)
        has_doctor = _user_has_doctor_skill(current_user)
        await message.answer(
            f"Травма «{trauma_name}» снята.",
            keyboard=get_main_keyboard_vk(night_active, has_craft, has_trauma, has_doctor),
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
            if normalized_text.lower() not in ("да", "yes", "1"):
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
                await message.answer("Невозможно провести операцию над собой. Действие завершено.")
                return
            await vk_surgery_target_next(message, target_input)
            return

        case FsmState.SURGERY_MEDICINES:
            await vk_surgery_medicines_next(message, text or "")
            return

        case FsmState.CRAFT_ALCHEMY:
            fsm["data"]["use_alchemy"] = normalized_text.lower() in ("да", "yes", "1")
            fsm["state"] = FsmState.CRAFT_MED_TYPE
            await vk_craft_ask_med_type(message)
            return

        case FsmState.CRAFT_MED_TYPE:
            await vk_craft_med_type_next(message, normalized_text)
            return

        case FsmState.CRAFT_INGREDIENT1:
            await vk_craft_ingredient1_next(message, normalized_text)
            return

        case FsmState.CRAFT_INGREDIENT2:
            await vk_craft_ingredient2_next(message, normalized_text)
            return

        case FsmState.RESEARCH_CONFIRM_LAB:
            if normalized_text.lower() not in ("да", "yes", "1"):
                fsm["state"] = None
                fsm["data"] = {}
                await message.answer("Действие отменено.")
                return
            fsm["state"] = FsmState.RESEARCH_REAGENT_CODE
            await message.answer("Введите код реагента (число):")
            return

        case FsmState.RESEARCH_REAGENT_CODE:
            try:
                code = int(normalized_text)
            except ValueError:
                await message.answer("Введите число — код реагента.")
                return
            fsm["data"]["research_reagent_code"] = code
            fsm["state"] = FsmState.RESEARCH_OBJECT_TYPE
            await message.answer(
                "Выберите объект исследования (введите номер или название):\n"
                "  1. Ингредиент\n  2. Лекарство\n  3. Код трупа"
            )
            return

        case FsmState.RESEARCH_OBJECT_TYPE:
            await vk_research_object_type_next(message, normalized_text)
            return

        case FsmState.RESEARCH_INGREDIENT_NAME:
            await vk_research_ingredient_next(message, normalized_text)
            return

        case FsmState.RESEARCH_BLUE_MARK:
            await vk_research_blue_mark_next(message, normalized_text)
            return

        case FsmState.RESEARCH_MEDICINE_CODE:
            await vk_research_medicine_code_next(message, normalized_text)
            return

        case FsmState.RESEARCH_CORPSE_ID:
            await vk_research_corpse_next(message, normalized_text)
            return


async def vk_research_object_type_next(message: Message, normalized_text: str):
    """Выбор объекта исследования: Ингредиент / Лекарство / Код трупа."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    t = normalized_text.lower().strip()
    if t in ("1", "ингредиент"):
        fsm["state"] = FsmState.RESEARCH_INGREDIENT_NAME
        async with async_session() as session:
            r = await session.execute(select(Ingredient).order_by(Ingredient.name))
            ings = list(r.scalars().all())
        names = ", ".join(ing.name.value for ing in ings)
        await message.answer(f"Введите название ингредиента: {names}")
        return
    if t in ("2", "лекарство"):
        fsm["state"] = FsmState.RESEARCH_MEDICINE_CODE
        await message.answer("Введите код лекарства (число):")
        return
    if t in ("3", "код трупа", "труп"):
        fsm["state"] = FsmState.RESEARCH_CORPSE_ID
        await message.answer("Введите код трупа или @username пациента:")
        return
    await message.answer("Введите 1 (Ингредиент), 2 (Лекарство) или 3 (Код трупа).")


async def vk_research_ingredient_next(message: Message, normalized_text: str):
    """Введено название ингредиента. Если кровь — уточнить синюю метку."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    async with async_session() as session:
        r = await session.execute(select(Ingredient).where(Ingredient.name == IngredientName.BLOOD))
        blood_ing = r.scalar_one_or_none()
        # поиск по названию (value)
        r2 = await session.execute(select(Ingredient))
        all_ings = list(r2.scalars().all())
    name_lower = normalized_text.lower().strip()
    chosen = None
    for ing in all_ings:
        if (ing.name.value or "").lower() == name_lower:
            chosen = ing
            break
    if not chosen:
        await message.answer("Ингредиент с таким названием не найден. Введите название из списка.")
        return
    fsm["data"]["research_ingredient_id"] = chosen.id
    fsm["data"]["research_ingredient_name"] = chosen.name.value
    if chosen.name == IngredientName.BLOOD:
        fsm["state"] = FsmState.RESEARCH_BLUE_MARK
        await message.answer("Есть ли на ленте синяя метка? (да / нет)")
        return
    fsm["state"] = None
    fsm["data"] = {}
    desc = f"Ингредиент: {chosen.name.value}, категория: {chosen.category.value}."
    await message.answer(desc)


async def vk_research_blue_mark_next(message: Message, normalized_text: str):
    """Ответ про синюю метку для крови → выдать описание «Особая кровь» или «Кровь»."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    fsm["state"] = None
    fsm["data"] = {}
    has_blue = normalized_text.lower().strip() in ("да", "yes", "1")
    desc = "Особая кровь." if has_blue else "Кровь."
    await message.answer(desc)


async def vk_research_medicine_code_next(message: Message, normalized_text: str):
    """Введён код лекарства → выдать описание лекарства."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    fsm["state"] = None
    fsm["data"] = {}
    try:
        code = int(normalized_text.strip())
    except ValueError:
        await message.answer("Введите число — код лекарства.")
        return
    async with async_session() as session:
        r = await session.execute(
            select(Medicine).where(Medicine.code == code).options(
                selectinload(Medicine.ingredient1),
                selectinload(Medicine.ingredient2),
            )
        )
        med = r.scalar_one_or_none()
    if not med:
        await message.answer("Лекарство с таким кодом не найдено.")
        return
    parts = [
        f"Код: {med.code}",
        f"Тип: {med.med_type.value}",
        f"Слои лечения: 1={med.cure_layer_1}, 2={med.cure_layer_2}, 3={med.cure_layer_3}",
        f"Боль: {med.pain}",
    ]
    if med.ingredient1:
        parts.append(f"Ингредиент 1: {med.ingredient1.name.value}")
    if med.ingredient2:
        parts.append(f"Ингредиент 2: {med.ingredient2.name.value}")
    await message.answer("\n".join(parts))


async def vk_research_corpse_next(message: Message, normalized_text: str):
    """Введён код трупа / username → описание пациента, статус заражения, болячки."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    fsm["state"] = None
    fsm["data"] = {}
    target = normalized_text.replace("@", "").strip()
    if not target:
        await message.answer("Введите @username или код трупа.")
        return
    async with async_session() as session:
        user = await get_user_from_vk(session, None, target)
        if not user and target.isdigit():
            r = await session.execute(
                select(User).where(User.id == int(target)).options(
                    selectinload(User.slots).selectinload(Slot.skill),
                    selectinload(User.slots).selectinload(Slot.disease),
                )
            )
            user = r.scalar_one_or_none()
    if not user:
        await message.answer("Пациент с таким кодом или именем не найден.")
        return
    lines = [
        f"Пациент: {user.character_name or user.tg_username or 'Неизвестный'}",
        f"Статус заражения: {user.infection_status.value if user.infection_status else 'Здоров'}",
    ]
    for slot in user.slots or []:
        if slot.disease:
            d = slot.disease
            lines.append(f"Болячка: {d.name} (тип: {d.type.value})")
    await message.answer("\n".join(lines) if lines else "Нет данных.")


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


SPECIAL_MEDICINE_KIND_BY_CODE = {
    # В БД нет отдельного поля "тип особого лекарства", поэтому фиксируем по коду.
    41: "Панацея",
    51: "Вакцина",
    61: "Порошочек",
}


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
    await message.answer("Введите 1 код особого лекарства (41 / 51 / 61):")


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
        await message.answer("Введите код числом (например 41).")
        return

    kind = SPECIAL_MEDICINE_KIND_BY_CODE.get(code)
    if not kind:
        await message.answer("Неизвестный код особого лекарства. Допустимые коды: 41, 51, 61.")
        return

    async with async_session() as session:
        med_result = await session.execute(select(Medicine).where(Medicine.code == code))
        medicine = med_result.scalar_one_or_none()
        if not medicine:
            await message.answer("Лекарство с таким кодом не найдено в базе.")
            return
        if medicine.med_type != MedType.SPECIAL:
            await message.answer("Нельзя использовать обычные лекарства в этом действии. Нужен код особого лекарства.")
            return

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

        def clear_symptoms() -> int:
            removed = 0
            for slot in patient.slots:
                if slot.disease and slot.disease.type == DiseaseType.SYMPTOM:
                    slot.disease_id = None
                    removed += 1
            return removed

        consequence_msgs: list[str] = []
        if kind == "Панацея":
            removed = clear_symptoms()
            patient.infection_status = InfectionStatus.HEALTHY
            consequence_msgs.append(f"Снято симптомов: {removed}.")

        elif kind == "Вакцина":
            if patient.infection_status == InfectionStatus.HEALTHY:
                patient.infection_status = InfectionStatus.VACCINATED
                consequence_msgs.append("Статус изменён на «Вакцинирован».")
            else:
                consequence_msgs.append("На заражённого вакцина не действует.")

        elif kind == "Порошочек":
            removed = clear_symptoms()
            patient.infection_status = InfectionStatus.HEALTHY
            consequence_msgs.append(f"Снято симптомов: {removed}.")

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

    msg = f"🧬 <b>Особое лечение</b> проведено: <b>{kind}</b>."
    if consequence_msgs:
        msg += "\n\n" + "\n".join(consequence_msgs)
    await message.answer(msg)


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
    location,
) -> tuple[list[str], bool]:
    """
    Проводит хирургическую операцию: боль, осложнения, снятие болячек с операция=да.
    Возвращает (список строк для сообщения, patient_died).
    """
    from datetime import datetime

    X = PAIN_DEATH_THRESHOLD  # 10
    Y = PAIN_CONSEQUENCE_DIVISOR  # 3
    pain_wound_mod = location.pain_wound_mod if location else 0
    light_comp_mod = location.light_comp_mod if location else 0
    severe_comp_mod = location.severe_comp_mod if location else 0

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
    if pain_sum > X + Y:
        heavy_pain = 1
        light_pain = 1
    elif pain_sum > X:
        heavy_pain = 1
    elif pain_sum > Y:
        light_pain = 1

    if pain_sum > X:
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

    patient.last_cure_time = datetime.utcnow()
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
        location_result = await session.execute(
            select(Location).where(Location.name.ilike("%госпиталь%"))
        )
        location = location_result.scalars().first()
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
        msgs, patient_died = await _do_surgery_finalize(
            session, patient, immunics_count, antibiotics_count, painkillers_count, location
        )
        await session.commit()

    result = "🏥 Хирургическая операция проведена."
    if msgs:
        result += "\n\nОсложнения:\n" + "\n".join(msgs)
    await message.answer(result)
    if patient_died:
        await message.answer(
            "Пациент умер в результате операции. Напоминание: необходимо получить метку репутации."
        )


def _resolve_ingredient_from_list(ingredients: list, text: str):
    """По номеру (1-based) или названию возвращает Ingredient или None."""
    text = (text or "").strip()
    try:
        idx = int(text)
        if 1 <= idx <= len(ingredients):
            return ingredients[idx - 1]
    except ValueError:
        pass
    text_lower = text.lower()
    for ing in ingredients:
        if (ing.name.value or "").lower() == text_lower:
            return ing
    return None


async def vk_craft_ask_med_type(message: Message):
    """Запросить тип лекарства (только из рецептов игрока)."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    craft_peer_id = fsm.get("data", {}).get("craft_peer_id", peer_id)
    async with async_session() as session:
        user = await get_user_from_vk(session, craft_peer_id, None)
    if not user:
        fsm["state"] = None
        fsm["data"] = {}
        await message.answer("Ошибка: пользователь не найден.")
        return
    recipes = _user_medicine_recipes(user)
    if not recipes:
        fsm["state"] = None
        fsm["data"] = {}
        await message.answer("Нет доступных рецептов.")
        return
    type_names = []
    for r in recipes:
        mt = RECIPE_TO_MED_TYPE.get(r)
        if mt:
            type_names.append(mt.value)
    fsm["data"]["craft_recipe_types"] = [RECIPE_TO_MED_TYPE[r].name for r in recipes if RECIPE_TO_MED_TYPE.get(r)]
    await message.answer(
        "Выберите тип создаваемого лекарства (введите название): " + ", ".join(type_names) + "."
    )


async def vk_craft_med_type_next(message: Message, normalized_text: str):
    """Выбран тип лекарства → запрос первого компонента."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    allowed = fsm.get("data", {}).get("craft_recipe_types", [])
    med_map = {"иммуники": "IMMUNIC", "антибиотики": "ANTIBIOTIC", "обезболивающие": "PAINKILLER", "особое": "SPECIAL"}
    med_name = med_map.get(normalized_text.lower())
    if not med_name or med_name not in allowed:
        await message.answer("Введите тип из ваших рецептов: " + ", ".join(MedType[mn].value for mn in allowed) + ".")
        return
    fsm["data"]["craft_med_type"] = med_name
    med_type = MedType[med_name]
    async with async_session() as session:
        if med_type == MedType.SPECIAL:
            result = await session.execute(select(Ingredient).where(Ingredient.name == IngredientName.OTHER))
        else:
            result = await session.execute(select(Ingredient).where(Ingredient.category == IngredientCategory.HERB))
        ingredients = list(result.scalars().all())
    if not ingredients:
        fsm["state"] = None
        fsm["data"] = {}
        await message.answer("В базе нет подходящих ингредиентов для первого компонента.")
        return
    fsm["data"]["craft_ing1_list"] = [(ing.id, ing.name.value) for ing in ingredients]
    fsm["state"] = FsmState.CRAFT_INGREDIENT1
    lines = ["Выберите первый компонент (введите номер или название):"]
    for i, (_, name) in enumerate(fsm["data"]["craft_ing1_list"], 1):
        lines.append(f"  {i}. {name}")
    await message.answer("\n".join(lines))


async def vk_craft_ingredient1_next(message: Message, normalized_text: str):
    """Выбран первый компонент → запрос второго."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    ing_list = fsm.get("data", {}).get("craft_ing1_list", [])
    if not ing_list:
        fsm["state"] = None
        await message.answer("Ошибка сессии. Начните крафт заново.")
        return
    async with async_session() as session:
        ingredients = []
        for ing_id, _ in ing_list:
            r = await session.execute(select(Ingredient).where(Ingredient.id == ing_id))
            ing = r.scalar_one_or_none()
            if ing:
                ingredients.append(ing)
    chosen = _resolve_ingredient_from_list(ingredients, normalized_text)
    if not chosen:
        await message.answer("Не найдено. Введите номер из списка или название ингредиента.")
        return
    fsm["data"]["craft_ing1_id"] = chosen.id
    med_name = fsm.get("data", {}).get("craft_med_type", "IMMUNIC")
    med_type = MedType[med_name] if med_name else MedType.IMMUNIC
    async with async_session() as session:
        if med_type == MedType.SPECIAL:
            result = await session.execute(select(Ingredient).where(Ingredient.name == IngredientName.OTHER))
        elif med_type == MedType.IMMUNIC:
            result = await session.execute(select(Ingredient).where(Ingredient.category == IngredientCategory.HERB))
        elif med_type == MedType.ANTIBIOTIC:
            result = await session.execute(select(Ingredient).where(Ingredient.category == IngredientCategory.ORGAN))
        elif med_type == MedType.PAINKILLER:
            result = await session.execute(select(Ingredient).where(Ingredient.name == IngredientName.BLOOD))
        else:
            result = await session.execute(select(Ingredient))
        ingredients2 = list(result.scalars().all())
    if not ingredients2:
        fsm["state"] = None
        fsm["data"] = {}
        await message.answer("В базе нет подходящих ингредиентов для второго компонента.")
        return
    fsm["data"]["craft_ing2_list"] = [(ing.id, ing.name.value) for ing in ingredients2]
    fsm["state"] = FsmState.CRAFT_INGREDIENT2
    lines = ["Выберите второй компонент (введите номер или название):"]
    for i, (_, name) in enumerate(fsm["data"]["craft_ing2_list"], 1):
        lines.append(f"  {i}. {name}")
    await message.answer("\n".join(lines))


async def vk_craft_ingredient2_next(message: Message, normalized_text: str):
    """Второй компонент выбран → выдать код(ы) лекарства."""
    peer_id = message.peer_id
    fsm = get_fsm(peer_id)
    ing_list = fsm.get("data", {}).get("craft_ing2_list", [])
    ing1_id = fsm.get("data", {}).get("craft_ing1_id")
    med_name = fsm.get("data", {}).get("craft_med_type", "IMMUNIC")
    use_alchemy = fsm.get("data", {}).get("use_alchemy", False)
    craft_peer_id = fsm.get("data", {}).get("craft_peer_id", peer_id)
    fsm["state"] = None
    fsm["data"] = {}

    if not ing_list or not ing1_id:
        await message.answer("Ошибка сессии. Начните крафт заново.")
        return
    async with async_session() as session:
        ingredients = []
        for ing_id, _ in ing_list:
            r = await session.execute(select(Ingredient).where(Ingredient.id == ing_id))
            ing = r.scalar_one_or_none()
            if ing:
                ingredients.append(ing)
    chosen = _resolve_ingredient_from_list(ingredients, normalized_text)
    if not chosen:
        await message.answer("Не найдено. Введите номер из списка или название ингредиента.")
        return
    ing2_id = chosen.id
    med_type = MedType[med_name] if med_name else MedType.IMMUNIC

    async with async_session() as session:
        result = await session.execute(
            select(Medicine).where(
                Medicine.med_type == med_type,
                or_(
                    and_(Medicine.ingredient1_id == ing1_id, Medicine.ingredient2_id == ing2_id),
                    and_(Medicine.ingredient1_id == ing2_id, Medicine.ingredient2_id == ing1_id),
                )
            )
        )
        medicine = result.scalar_one_or_none()

        if medicine:
            code = medicine.code if medicine.code is not None else medicine.id
            codes_msg = f"Код лекарства: {code}"
        elif med_type == MedType.SPECIAL:
            code = None
            codes_msg = "Код лекарства: Нерабочее"
        else:
            code = None
            codes_msg = "Лекарство с таким составом не найдено в базе."

        extra_codes = []
        if (code is not None or med_type == MedType.SPECIAL) and craft_peer_id:
            user = await get_user_from_vk(session, craft_peer_id, None)
            if user:
                skills = _user_active_skill_names(user)
                if "Менху" in skills or "Степной знахарь" in skills:
                    same_type_result = await session.execute(select(Medicine).where(Medicine.med_type == med_type))
                    same_list = list(same_type_result.scalars().all())
                    if same_list:
                        extra = random.choice(same_list)
                        ec = extra.code if extra.code is not None else extra.id
                        extra_codes.append(f"Дополнительный код (навык Менху/Степной знахарь): {ec}")
                if use_alchemy and med_type != MedType.SPECIAL:
                    same_type_result = await session.execute(select(Medicine).where(Medicine.med_type == med_type))
                    same_list = list(same_type_result.scalars().all())
                    if same_list:
                        extra = random.choice(same_list)
                        ec = extra.code if extra.code is not None else extra.id
                        extra_codes.append(f"Дополнительный код (алхимический стол): {ec}")

    if extra_codes:
        codes_msg += "\n" + "\n".join(extra_codes)
    await message.answer(codes_msg)


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
    if len(medicines) != len(medicine_codes):
        found_codes = {medicine.code for medicine in medicines}
        missing_codes = [code for code in medicine_codes if code not in found_codes]
        await message.answer(f"Не найдены лекарства с кодами: {missing_codes}. Введите коды снова.")
        return
    if any(m.med_type == MedType.SPECIAL for m in medicines):
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
