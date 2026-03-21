import asyncio
import html
import logging
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, Update
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram import BaseMiddleware

import random
from collections import defaultdict
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from config import BOT_TOKEN
from database import async_session, User, Disease, Skill, Slot, DiseaseType, DiseaseKind, InfectionStatus, Location, Medicine, Complication, MedType, ComplicationSource, UserAction, GameSettings

# Инициализация бота и диспетчера
dp = Dispatcher(storage=MemoryStorage())

# Режим ночи хранится в БД (game_settings.night_active), переключают админы


class NightStates(StatesGroup):
    location = State()
    use_melkiy = State()
    food = State()
    immunics = State()
    painkiller = State()
    use_restless = State()


class TreatStates(StatesGroup):
    target = State()
    medicines = State()


SPECIAL_MED_TYPES = {
    MedType.SPECIAL,
    MedType.NON_WORKING,
    MedType.POWDER,
    MedType.PANACEA,
    MedType.VACCINE,
}


PAIN_DEATH_THRESHOLD = 10
PAIN_CONSEQUENCE_DIVISOR = 3
CURE_COOLDOWN_HOURS = 1


# Логгер действий пользователей
user_actions_logger = logging.getLogger("user_actions")


class UserActionLoggingMiddleware(BaseMiddleware):
    """Логирует все действия пользователя: сообщения и нажатия кнопок."""

    async def __call__(self, handler, event: Update, data: dict):
        user_id = None
        username = None
        chat_id = None
        action = None
        details = ""

        if event.message:
            msg = event.message
            user_id = msg.from_user.id if msg.from_user else None
            username = (msg.from_user.username or msg.from_user.full_name) if msg.from_user else ""
            chat_id = msg.chat.id
            action = "message"
            details = (msg.text or msg.caption or "[медиа/без текста]")[:200]
        if event.callback_query:
            cb = event.callback_query
            user_id = cb.from_user.id if cb.from_user else None
            username = (cb.from_user.username or cb.from_user.full_name) if cb.from_user else ""
            chat_id = cb.message.chat.id if cb.message else (cb.from_user.id if cb.from_user else 0)
            action = "callback"
            details = (cb.data or "")[:200]

        if user_id is not None:
            user_actions_logger.info(
                "user_id=%s username=%s chat_id=%s action=%s details=%s",
                user_id, username, chat_id, action, details
            )
            try:
                async with async_session() as session:
                    session.add(
                        UserAction(
                            user_id=user_id,
                            username=username or None,
                            chat_id=chat_id,
                            action_type=action,
                            details=details or None,
                        )
                    )
                    await session.commit()
            except Exception as e:
                user_actions_logger.warning("Не удалось сохранить действие в БД: %s", e)

        try:
            return await handler(event, data)
        except Exception as e:
            user_actions_logger.exception("Ошибка в обработчике: %s", e)
            try:
                msg = event.message or (event.callback_query.message if event.callback_query else None)
                if msg:
                    await msg.answer("Произошла ошибка. Попробуйте позже или обратитесь к администратору.")
            except Exception:
                pass
            raise


def get_main_keyboard(night_visible: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню. Заночевать — при ночи."""
    rows = [
        [KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="🩸 Получить ранение"), KeyboardButton(text="🦴 Получить травму")],
        [KeyboardButton(text="🦠 Получить заражение"), KeyboardButton(text="💊 Лечиться обычными лекарствами")]
    ]
    if night_visible:
        rows.append([KeyboardButton(text="🌙 Заночевать")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, persistent=True)


async def get_night_active() -> bool:
    """Читает из БД, включён ли режим ночи (кнопка «Заночевать»)."""
    async with async_session() as session:
        result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
        row = result.scalar_one_or_none()
        return row.night_active if row else False


async def get_user_from_telegram(session, telegram_id: int | None, tg_username: str | None):
    """
    Находит персонажа по telegram_id или tg_username. При нахождении по username
    проставляет telegram_id и tg_connected=True. Загружает slots + skill + disease.
    """
    opts = [
        selectinload(User.slots).selectinload(Slot.skill),
        selectinload(User.slots).selectinload(Slot.disease),
    ]
    q = select(User).options(*opts)
    if telegram_id is not None:
        r = await session.execute(q.where(User.telegram_id == telegram_id))
        u = r.scalar_one_or_none()
        if u:
            return u
    if tg_username:
        r = await session.execute(q.where(User.tg_username == tg_username))
        u = r.scalar_one_or_none()
        if u and telegram_id is not None:
            u.telegram_id = telegram_id
            u.tg_connected = True
        return u
    return None


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """
    Отвечает на команду /start
    """
    username = message.from_user.username
    if not username:
        await message.answer("Для работы с ботом у вас должен быть установлен <b>@username</b> в Telegram.")
        return

    telegram_id = message.from_user.id
    async with async_session() as session:
        user = await get_user_from_telegram(session, telegram_id, username)
        if not user:
            await message.answer("Извините, вы не подключены к системе (ваш username не найден в базе).")
            return
        user.is_active = True
        await session.commit()

    night_active = await get_night_active()
    await message.answer(
        f"Добро пожаловать, <b>@{username}</b>! Ваш профиль успешно активирован.",
        reply_markup=get_main_keyboard(night_active)
    )


@dp.message(F.text == "👤 Мой профиль")
@dp.message(Command("me"))
async def command_me_handler(message: Message) -> None:
    """
    Отправляет игроку его текущие статусы, здоровье, навыки и т.д.
    """
    username = message.from_user.username
    if not username:
        await message.answer("Для работы с ботом у вас должен быть установлен <b>@username</b> в Telegram.")
        return

    telegram_id = message.from_user.id
    async with async_session() as session:
        user = await get_user_from_telegram(session, telegram_id, username)
        if not user:
            await message.answer("Вы не подключены к системе (ваш username не найден в базе).")
            return
        await session.commit()

        health_max = 0
        health_current = 0
        wounds = 0
        
        traumas_text = []
        symptoms_text = []
        skills = []

        # Считаем показатели по слотам
        for slot in user.slots:
            is_blocked = slot.disease is not None
            
            if slot.disease:
                d = slot.disease
                if d.type.value == "Рана":
                    wounds += 1
                elif d.type.value == "Травма":
                    traumas_text.append(f"🦴 {d.name}")
                elif d.type.value == "Симптом":
                    symptoms_text.append(f"🤒 {d.name}")

            if slot.skill:
                s = slot.skill
                if s.is_health:
                    health_max += 1
                    if not is_blocked:
                        health_current += 1
                else:
                    if not is_blocked and (s.name or "").strip().lower() != "здоровье":
                        skills.append(s.name)

        # Собираем финальное сообщение
        status_text = user.infection_status.value if user.infection_status else "Здоров"
        char_name = user.character_name if user.character_name else "Неизвестный"
        vk_disp = (user.vk_username or "").strip() or "не указан"
        msg = f"👤 <b>Профиль игрока {char_name} (@{username})</b>\n"
        msg += f"🔗 <b>VK (логин в базе):</b> {html.escape(vk_disp)}\n\n"
        msg += f"🦠 <b>Статус инфекции:</b> {status_text}\n"
        msg += f"❤️ <b>Здоровье:</b> {health_current} / {health_max}\n"
        msg += f"🩸 <b>Ран:</b> {wounds}\n\n"

        msg += "<b>Травмы:</b>\n"
        msg += ("\n".join(set(traumas_text)) if traumas_text else "Нет") + "\n\n"

        msg += "<b>Симптомы:</b>\n"
        msg += ("\n".join(set(symptoms_text)) if symptoms_text else "Нет") + "\n\n"

        msg += "<b>Активные навыки:</b>\n"
        if skills:
            msg += "\n".join([f"🧠 {s}" for s in skills])
        else:
            msg += "Нет активных навыков"

        night_active = await get_night_active()
        await message.answer(msg, reply_markup=get_main_keyboard(night_active))


# ---------- Заночевать (FSM) ----------

async def _resolve_location(session, text: str):
    """Находит локацию по коду (число) или по названию (например Степь)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        code = int(text)
        r = await session.execute(select(Location).where(Location.code == code))
        return r.scalar_one_or_none()
    except ValueError:
        pass
    r = await session.execute(select(Location).where(Location.name.ilike(text)))
    return r.scalars().first()


def _user_active_skill_names(user) -> list:
    """Имена активных навыков игрока (слот не заблокирован болячкой)."""
    return [s.skill.name for s in user.slots if s.skill and s.disease_id is None]


@dp.message(F.text == "🌙 Заночевать")
async def night_start_handler(message: Message, state: FSMContext) -> None:
    if not message.from_user.username:
        await message.answer("Для работы с ботом у вас должен быть установлен <b>@username</b> в Telegram.")
        return
    await state.set_state(NightStates.location)
    await message.answer("Введите код или название места ночёвки:")


@dp.message(StateFilter(NightStates.location), F.text)
async def night_location_handler(message: Message, state: FSMContext) -> None:
    username = message.from_user.username
    if not username:
        await state.clear()
        await message.answer("Нет username.")
        return

    async with async_session() as session:
        loc = await _resolve_location(session, message.text)
        if not loc:
            await message.answer("Локация не найдена. Введите код или название места ночёвки:")
            return

        loc_name_normalized = (loc.name or "").strip().lower()
        telegram_id = message.from_user.id if message.from_user else None
        if loc_name_normalized == "степь":
            user = await get_user_from_telegram(session, telegram_id, username)
            if user and "Привычный к степи" not in _user_active_skill_names(user):
                await state.clear()
                await message.answer("Вы не можете ночевать здесь: у вас нет навыка «Привычный к степи».")
                return

        await state.update_data(location_id=loc.id)
        user = await get_user_from_telegram(session, telegram_id, username)

    if not user:
        await state.clear()
        await message.answer("Вы не подключены к системе.")
        return

    if loc.quality and "Мелкий" in _user_active_skill_names(user):
        await state.set_state(NightStates.use_melkiy)
        await message.answer("Использовать навык «Мелкий»? (да / нет)")
        return

    await state.set_state(NightStates.food)
    await message.answer("Введите количество съеденной еды (0–3):")


@dp.message(StateFilter(NightStates.use_melkiy), F.text)
async def night_use_melkiy_handler(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    await state.update_data(use_melkiy=text in ("да", "yes", "1"))
    await state.set_state(NightStates.food)
    await message.answer("Введите количество съеденной еды (0–3):")


@dp.message(StateFilter(NightStates.food), F.text)
async def night_food_handler(message: Message, state: FSMContext) -> None:
    try:
        food = max(0, min(3, int((message.text or "0").strip())))
    except ValueError:
        await message.answer("Введите число от 0 до 3.")
        return
    await state.update_data(food=food)
    await state.set_state(NightStates.immunics)
    await message.answer("Введите количество использованных иммуников:")


@dp.message(StateFilter(NightStates.immunics), F.text)
async def night_immunics_handler(message: Message, state: FSMContext) -> None:
    try:
        immunics = max(0, int((message.text or "0").strip()))
    except ValueError:
        await message.answer("Введите неотрицательное число.")
        return
    await state.update_data(immunics=immunics)
    await state.set_state(NightStates.painkiller)
    await message.answer("Использовали обезболивающее? (да / нет)")


@dp.message(StateFilter(NightStates.painkiller), F.text)
async def night_painkiller_handler(message: Message, state: FSMContext) -> None:
    painkiller = (message.text or "").strip().lower() in ("да", "yes", "1")
    await state.update_data(painkiller=painkiller)

    username = message.from_user.username
    if not username:
        await state.clear()
        return

    telegram_id = message.from_user.id if message.from_user else None
    async with async_session() as session:
        user = await get_user_from_telegram(session, telegram_id, username)
        await session.commit()

    if user and "Непоседа" in _user_active_skill_names(user):
        await state.set_state(NightStates.use_restless)
        await message.answer("Использовали навык «Непоседа»? (да / нет)")
        return

    await state.update_data(use_restless=False)
    await _night_finalize(message, state)


@dp.message(StateFilter(NightStates.use_restless), F.text)
async def night_use_restless_handler(message: Message, state: FSMContext) -> None:
    use_restless = (message.text or "").strip().lower() in ("да", "yes", "1")
    await state.update_data(use_restless=use_restless)
    await _night_finalize(message, state)


async def _night_finalize(message: Message, state: FSMContext) -> None:
    username = message.from_user.username
    if not username:
        await state.clear()
        return

    data = await state.get_data()
    location_id = data.get("location_id")
    food = data.get("food", 0)
    immunics = data.get("immunics", 0)
    painkiller = data.get("painkiller", False)
    use_restless = data.get("use_restless", False)
    await state.clear()

    telegram_id = message.from_user.id if message.from_user else None
    async with async_session() as session:
        result = await session.execute(select(Location).where(Location.id == location_id))
        location = result.scalar_one_or_none()
        if not location:
            await message.answer("Локация не найдена.")
            return

        user = await get_user_from_telegram(session, telegram_id, username)
        if not user or not user.is_alive:
            await message.answer("Персонаж не найден или мёртв.")
            return

        skills = _user_active_skill_names(user)

        energy = 0
        if location.quality:
            energy += 1 if "Привычный к улице" in skills else 2
        if painkiller:
            energy += 2
        if food > 0:
            energy += min(food, 3)
        else:
            energy -= 1
        energy_drain = sum(1 for s in user.slots if s.disease and getattr(s.disease, "energy", False))
        energy -= energy_drain
        if "Непоседа" in skills:
            energy += 2
        if use_restless:
            energy -= 1
        energy = min(6, energy)

        msg = f"🌙 <b>Ночёвка</b>\n\nВосстановлено энергии: <b>{energy}</b>"

        if "Густая кровь" in skills:
            wound_slots = [s for s in user.slots if s.disease and s.disease.type == DiseaseType.WOUND and s.disease.kind != DiseaseKind.BULLET]
            if wound_slots:
                slot = wound_slots[0]
                wound_name = slot.disease.name
                slot.disease_id = None
                msg += f"\n\nНавык «Густая кровь»: вылечена рана <b>{wound_name}</b>."

        roll = random.randint(0, max(0, location.infection_chance))
        if user.is_child:
            roll -= 10
        if "Крепыш" in skills:
            roll -= 10
        roll -= 10 * immunics
        if roll > 0:
            infection_msg = await apply_infection(session, user)
            msg += f"\n\n🦠 Заражение: {infection_msg}"
        else:
            msg += "\n\n🦠 Заражения не произошло."

        msg += "\n\nВы можете посмотреть 1 сон из имеющихся у вас."
        await session.commit()

    await message.answer(msg)


async def get_wound_keyboard() -> InlineKeyboardMarkup:
    async with async_session() as session:
        result = await session.execute(
            select(Disease).where(
                Disease.type == DiseaseType.WOUND,
                Disease.hidden_from_getting == False,
            )
        )
        wounds = list(result.scalars().all())
    rows = [
        [InlineKeyboardButton(text=d.name, callback_data=f"wound_id_{d.id}")]
        for d in wounds
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(F.text == "🩸 Получить ранение")
@dp.message(Command("wound"))
async def command_wound_handler(message: Message) -> None:
    """
    Позволяет игроку получить ранение. Выводит кнопки с выбором типа ранения.
    """
    keyboard = await get_wound_keyboard()
    if not keyboard.inline_keyboard:
        await message.answer("Сейчас нет доступных для получения ранений.")
        return
    await message.answer("Выберите тип ранения:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data and c.data.startswith('wound_'))
async def process_wound_callback(callback_query: CallbackQuery) -> None:
    if not callback_query.data.startswith("wound_id_"):
        await callback_query.answer("Неверный формат выбора ранения.", show_alert=True)
        return
    try:
        disease_id = int(callback_query.data.replace("wound_id_", ""))
    except ValueError:
        await callback_query.answer("Неверный код ранения.", show_alert=True)
        return
    
    username = callback_query.from_user.username
    if not username:
        await callback_query.answer("Нет username", show_alert=True)
        return

    telegram_id = callback_query.from_user.id
    async with async_session() as session:
        user = await get_user_from_telegram(session, telegram_id, username)
        await session.commit()

        if not user:
            await callback_query.answer("Вы не зарегистрированы.", show_alert=True)
            return

        if not user.is_alive:
            await callback_query.message.answer("Ваш персонаж уже мёртв.")
            await callback_query.answer()
            return

        # Find empty slots where skill.is_health == True
        available_slots = [s for s in user.slots if s.disease is None and s.skill and s.skill.is_health]

        if not available_slots:
            await callback_query.message.answer("Нет свободных ячеек здоровья для получения ранения! Персонаж при смерти или мёртв.")
            await callback_query.answer()
            return

        # Pick random slot
        chosen_slot = random.choice(available_slots)

        # Find or create Disease
        d_res = await session.execute(
            select(Disease).where(
                Disease.id == disease_id,
                Disease.type == DiseaseType.WOUND,
                Disease.hidden_from_getting == False,
            )
        )
        disease = d_res.scalar_one_or_none()
        if not disease:
            await callback_query.answer("Ранение этого типа скрыто от получения.", show_alert=True)
            return

        # Assign disease
        chosen_slot.disease_id = disease.id
        # Update available slots count to reflect the damage
        remaining_health = len(available_slots) - 1

        skill_name = chosen_slot.skill.name
        
        if remaining_health == 0:
            user.is_alive = False
        
        await session.commit()

        # Messages
        await callback_query.message.delete() # remove keyboard
        
        msg = f"Вы получили ранение: <b>{disease.name}</b>.\n"
        
        if skill_name != "Здоровье":
            msg += f"⚠️ Навык <b>{skill_name}</b> временно недоступен из-за ранения!\n"
            
        if remaining_health == 0:
            msg += "\n💀 <b>Ваше здоровье упало до 0. Вы мертвы!</b>"
        else:
            msg += f"\n❤️ Осталось здоровья: {remaining_health}"
            
        await callback_query.message.answer(msg)
        await callback_query.answer()


async def get_symptom(session, user) -> str:
    """
    Автодействие «Получить симптом»: выбирает случайный симптом из БД и накладывает на слот.
    Возвращает текст сообщения для игрока.
    """
    result = await session.execute(select(Disease).where(Disease.type == DiseaseType.SYMPTOM))
    symptoms = list(result.scalars().all())
    if not symptoms:
        return "Болезнь прогрессирует. Автодействие «Получить симптом»: в базе нет симптомов."
    disease = random.choice(symptoms)
    chosen_slot, remaining_health, skill_name = _apply_trauma(session, user, disease)
    if chosen_slot is None:
        return "Болезнь прогрессирует. Автодействие «Получить симптом»: нет свободной ячейки для симптома."
    if remaining_health == 0:
        user.is_alive = False
    msg = f"Болезнь прогрессирует. Выполнено автодействие «Получить симптом»: получен симптом <b>{disease.name}</b>."
    if skill_name and skill_name != "Здоровье":
        msg += f"\n⚠️ Навык <b>{skill_name}</b> временно недоступен."
    if remaining_health == 0:
        msg += "\n💀 Здоровье = 0. Вы мертвы."
    else:
        msg += f"\n❤️ Осталось здоровья: {remaining_health}"
    return msg


async def apply_infection(session, user) -> str:
    """
    Действие «Получить заражение»: проверка статуса и применение логики.
    — Вакцинирован: действие завершается, ничего не происходит.
    — Здоров: статус → Заражён, время последнего заражения = сейчас.
    — Заражён: выполняется автодействие «Получить симптом».
    """
    if user.infection_status == InfectionStatus.VACCINATED:
        return "Вы вакцинированы. Действие завершено, ничего не происходит."
    if user.infection_status == InfectionStatus.HEALTHY:
        user.infection_status = InfectionStatus.INFECTED
        user.last_infection_time = datetime.utcnow()
        return "Вы заразились. Статус: <b>Заражён</b>."
    # INFECTED
    return await get_symptom(session, user)


def _apply_trauma(session, user, disease) -> tuple:
    """
    Подбирает случайную подходящую ячейку и записывает в неё травму.
    Возвращает (chosen_slot, remaining_health, skill_name) или (None, 0, "") при ошибке.
    """
    if disease.health_only:
        available = [s for s in user.slots if s.disease_id is None and s.skill and s.skill.is_health]
    else:
        layers = disease.layers or [1]
        available = [s for s in user.slots if s.disease_id is None and s.layer in layers]

    if not available:
        return None, 0, ""

    chosen = random.choice(available)
    chosen.disease_id = disease.id
    skill_name = chosen.skill.name if chosen.skill else ""

    remaining_health = sum(
        1 for s in user.slots
        if s.skill and s.skill.is_health and s.disease_id is None
    )
    return chosen, remaining_health, skill_name


def get_trauma_keyboard(traumas):
    rows = []
    for d in traumas:
        code = d.trauma_code if d.trauma_code is not None else d.id
        rows.append([InlineKeyboardButton(text=d.name, callback_data=f"trauma_{code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(F.text == "🦴 Получить травму")
@dp.message(Command("trauma"))
async def command_trauma_handler(message: Message) -> None:
    username = message.from_user.username
    if not username:
        await message.answer("Для работы с ботом у вас должен быть установлен <b>@username</b> в Telegram.")
        return

    async with async_session() as session:
        result = await session.execute(
            select(Disease).where(
                Disease.type == DiseaseType.TRAUMA,
                Disease.hidden_from_getting == False,
            ).order_by(Disease.trauma_code)
        )
        traumas = list(result.scalars().all())

    if not traumas:
        await message.answer(
            "В базе нет доступных травм. Добавьте травмы в БД."
        )
        return

    await message.answer("Выберите травму:", reply_markup=get_trauma_keyboard(traumas))


@dp.callback_query(lambda c: c.data and c.data.startswith("trauma_"))
async def process_trauma_callback(callback_query: CallbackQuery) -> None:
    code_str = callback_query.data.replace("trauma_", "")
    try:
        trauma_code = int(code_str)
    except ValueError:
        await callback_query.answer("Неверный код травмы.", show_alert=True)
        return

    username = callback_query.from_user.username
    if not username:
        await callback_query.answer("Нет username", show_alert=True)
        return

    telegram_id = callback_query.from_user.id
    async with async_session() as session:
        result = await session.execute(
            select(Disease).where(
                Disease.type == DiseaseType.TRAUMA,
                Disease.trauma_code == trauma_code
            )
        )
        disease = result.scalar_one_or_none()
        if not disease:
            await callback_query.answer("Травма с таким кодом не найдена.", show_alert=True)
            return

        user = await get_user_from_telegram(session, telegram_id, username)
        await session.commit()

        if not user:
            await callback_query.answer("Вы не зарегистрированы.", show_alert=True)
            return

        if not user.is_alive:
            try:
                await callback_query.message.edit_text("Ваш персонаж уже мёртв.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception:
                await callback_query.message.answer("Ваш персонаж уже мёртв.")
            await callback_query.answer()
            return

        chosen_slot, remaining_health, skill_name = _apply_trauma(session, user, disease)

        if chosen_slot is None:
            try:
                await callback_query.message.edit_text("Нет подходящей свободной ячейки для этой травмы.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
            except Exception:
                await callback_query.message.answer("Нет подходящей свободной ячейки для этой травмы.")
            await callback_query.answer()
            return

        if remaining_health == 0:
            user.is_alive = False

        await session.commit()

    await callback_query.message.delete()
    msg = f"Вы получили травму: <b>{disease.name}</b>.\n"
    if skill_name and skill_name != "Здоровье":
        msg += f"⚠️ Навык <b>{skill_name}</b> временно недоступен из-за травмы!\n"
    if remaining_health == 0:
        msg += "\n💀 <b>Здоровье = 0. Вы мертвы.</b>"
    else:
        msg += f"\n❤️ Осталось здоровья: {remaining_health}"
    await callback_query.message.answer(msg)
    await callback_query.answer()


@dp.message(F.text == "🦠 Получить заражение")
@dp.message(Command("infection"))
async def command_infection_handler(message: Message) -> None:
    """
    Действие «Получить заражение» (запуск игроком или в результате действия «Заночевать»).
    Проверка статуса: Вакцинирован → ничего; Здоров → Заражён + время; Заражён → Получить симптом.
    """
    username = message.from_user.username
    if not username:
        await message.answer("Для работы с ботом у вас должен быть установлен <b>@username</b> в Telegram.")
        return

    telegram_id = message.from_user.id
    async with async_session() as session:
        user = await get_user_from_telegram(session, telegram_id, username)
        if not user:
            await message.answer("Вы не подключены к системе (ваш username не найден в базе).")
            return
        await session.commit()
        if not user.is_alive:
            await message.answer("Ваш персонаж уже мёртв.")
            return

        msg = await apply_infection(session, user)
        await session.commit()

    await message.answer(msg)


@dp.message(Command("medicines"))
async def command_medicines_handler(message: Message) -> None:
    """Выводит коды и виды лекарств для лечения."""
    async with async_session() as session:
        result = await session.execute(
            select(Medicine).where(Medicine.med_type.notin_(SPECIAL_MED_TYPES)).order_by(Medicine.code)
        )
        medicines = list(result.scalars().all())
    if not medicines:
        await message.answer("В базе пока нет обычных лекарств (таблица пуста или только «Особое»).")
        return
    lines = ["💊 <b>Коды лекарств</b> (для ввода при лечении):\n"]
    for m in medicines:
        lines.append(f"  <b>{m.code}</b> — {m.med_type.value} (слой1: {m.cure_layer_1}, слой2: {m.cure_layer_2}, слой3: {m.cure_layer_3}, боль: {m.pain})")
    await message.answer("\n".join(lines))


@dp.message(Command("night"))
async def command_night_toggle_handler(message: Message) -> None:
    """Только для админов: включить/выключить режим ночи (кнопка «Заночевать» у всех игроков)."""
    username = message.from_user.username
    if not username:
        await message.answer("Нет @username.")
        return

    telegram_id = message.from_user.id
    async with async_session() as session:
        user = await get_user_from_telegram(session, telegram_id, username)
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
        f"🌙 Ночь <b>{status}</b>. Кнопка «Заночевать» теперь {'видна' if new_state else 'скрыта'} у всех игроков.",
        reply_markup=get_main_keyboard(new_state)
    )


# ---------- Лечиться обычными лекарствами (FSM) ----------

async def _apply_trauma_by_code(session, user, trauma_code: int):
    """Применить травму по коду. Возвращает (success, msg)."""
    res = await session.execute(
        select(Disease).where(
            Disease.type == DiseaseType.TRAUMA,
            Disease.trauma_code == trauma_code
        )
    )
    disease = res.scalar_one_or_none()
    if not disease:
        return False, f"Травма с кодом {trauma_code} не найдена."
    chosen_slot, remaining_health, skill_name = _apply_trauma(session, user, disease)
    if chosen_slot is None:
        return False, "Нет подходящей свободной ячейки для травмы."
    if remaining_health == 0:
        user.is_alive = False
    return True, disease.name


@dp.message(F.text == "💊 Лечиться обычными лекарствами")
@dp.message(Command("treat"))
async def treat_start_handler(message: Message, state: FSMContext) -> None:
    if not message.from_user.username:
        await message.answer("Для работы с ботом у вас должен быть установлен <b>@username</b> в Telegram.")
        return
    await state.set_state(TreatStates.target)
    await state.update_data(initiator_chat_id=message.chat.id)
    await message.answer("Укажите, кого лечите: введите @username игрока или «себя» для лечения себя.")


@dp.message(StateFilter(TreatStates.target), F.text)
async def treat_target_handler(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower().replace("@", "")
    if not text:
        await message.answer("Введите @username или «себя».")
        return

    initiator_username = message.from_user.username
    target_username = initiator_username if text == "себя" else text

    async with async_session() as session:
        patient = await get_user_from_telegram(session, None, target_username)

    if not patient:
        await message.answer("Игрок с таким username не найден.")
        return

    if patient.infection_status != InfectionStatus.INFECTED:
        await state.clear()
        await message.answer("Невозможно провести лечение: у выбранного игрока нет статуса «Заражён».")
        return

    if patient.last_cure_time:
        now = datetime.utcnow()
        last = patient.last_cure_time
        if last.tzinfo:
            last = last.replace(tzinfo=None)
        if (now - last).total_seconds() < CURE_COOLDOWN_HOURS * 3600:
            await state.clear()
            await message.answer("Невозможно провести лечение: выбранный игрок лечился менее часа назад.")
            return

    await state.update_data(target_username=target_username)
    await state.set_state(TreatStates.medicines)
    await message.answer("Введите коды всех использованных лекарств через пробел или запятую (например: 1 2 3).")


@dp.message(StateFilter(TreatStates.medicines), F.text)
async def treat_medicines_handler(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(",", " ").split()
    codes = []
    for x in raw:
        try:
            codes.append(int(x.strip()))
        except ValueError:
            continue
    if not codes:
        await message.answer("Введите хотя бы один код лекарства (числа через пробел или запятую).")
        return

    async with async_session() as session:
        distinct_codes = sorted(set(codes))
        result = await session.execute(select(Medicine).where(Medicine.code.in_(distinct_codes)))
        medicines = list(result.scalars().all())

    found_codes = {m.code for m in medicines}
    missing = [c for c in distinct_codes if c not in found_codes]
    if missing:
        await message.answer(f"Не найдены лекарства с кодами: {missing}. Введите коды снова.")
        return

    if any(m.med_type in SPECIAL_MED_TYPES for m in medicines):
        await state.clear()
        await message.answer("Невозможно провести лечение: использовано лекарство вида «Особое».")
        return

    await state.update_data(medicine_codes=codes)
    await _treat_finalize(message, state)


async def _treat_finalize(message: Message, state: FSMContext) -> None:
    """Расчёт боли, лечения, парные/непарные симптомы, последствия."""
    data = await state.get_data()
    target_username = data.get("target_username")
    medicine_codes = data.get("medicine_codes", [])
    initiator_chat_id = data.get("initiator_chat_id", message.chat.id)
    await state.clear()

    async with async_session() as session:
        gs_result = await session.execute(select(GameSettings).where(GameSettings.id == 1))
        settings = gs_result.scalar_one_or_none()
        pain_disease_mod = settings.pain_disease_mod if settings else 0
        cure_mod = settings.cure_mod if settings else 0
        pain_death_threshold = settings.pain_death_threshold if settings else PAIN_DEATH_THRESHOLD
        pain_consequence_divisor = settings.pain_consequence_divisor if settings else PAIN_CONSEQUENCE_DIVISOR

        distinct_codes = sorted(set(medicine_codes))
        result = await session.execute(select(Medicine).where(Medicine.code.in_(distinct_codes)))
        medicines = list(result.scalars().all())
        if not medicines:
            await message.answer("Лекарства не найдены.")
            return

        medicines_by_code = {m.code: m for m in medicines if m.code is not None}
        missing_codes = [c for c in distinct_codes if c not in medicines_by_code]
        if missing_codes:
            await message.answer(f"Не найдены лекарства с кодами: {missing_codes}.")
            return

        # Важно: учитываем кратность по введенным кодам
        meds_ordered = [medicines_by_code.get(code) for code in medicine_codes]
        if any(m is None for m in meds_ordered):
            await message.answer("Лекарства не найдены.")
            return

        patient = await get_user_from_telegram(session, None, target_username)
        if not patient:
            await message.answer("Пациент не найден.")
            return

        pain_sum = 0
        for s in patient.slots:
            if s.skill and s.disease_id is None:
                pain_sum += s.skill.pain or 0
            if s.disease:
                pain_sum += s.disease.pain or 0
        for m in meds_ordered:
            pain_sum += m.pain or 0
        pain_sum += pain_disease_mod

        if pain_sum > pain_death_threshold:
            patient.is_alive = False
            await session.commit()
            await message.answer(
                "Сумма боли превысила порог. Пациент умер.\n"
                "Напоминание: необходимо получить метку репутации."
            )
            return

        cure_1 = sum(m.cure_layer_1 or 0 for m in meds_ordered) + cure_mod
        cure_2 = sum(m.cure_layer_2 or 0 for m in meds_ordered) + cure_mod
        cure_3 = sum(m.cure_layer_3 or 0 for m in meds_ordered) + cure_mod

        symptom_slots = [
            (s, s.disease)
            for s in patient.slots
            if s.disease and s.disease.type == DiseaseType.SYMPTOM
        ]
        by_layer_strength = defaultdict(list)
        for slot, disease in symptom_slots:
            layer = slot.layer
            strength = disease.strength or 0
            by_layer_strength[(layer, strength)].append((slot, disease))

        pairs = []
        unpaired = []
        for (layer, strength), group in by_layer_strength.items():
            if len(group) >= 2:
                pairs.append((layer, strength, group[:2]))
                if len(group) > 2:
                    unpaired.extend(group[2:])
            else:
                unpaired.extend(group)

        def cure_for(layer):
            if layer == 1:
                return cure_1
            if layer == 2:
                return cure_2
            return cure_3

        def subtract_cure(layer, amount):
            nonlocal cure_1, cure_2, cure_3
            if layer == 1:
                cure_1 -= amount
            elif layer == 2:
                cure_2 -= amount
            else:
                cure_3 -= amount

        pairs_sorted = sorted(pairs, key=lambda x: (-sum(d.strength or 0 for _, d in x[2]), x[0]))
        for layer, strength, group in pairs_sorted:
            total_strength = sum(d.strength or 0 for _, d in group)
            if cure_for(layer) >= total_strength:
                for slot, _ in group:
                    slot.disease_id = None
                subtract_cure(layer, total_strength)

        unpaired_sorted = sorted(unpaired, key=lambda x: (-(x[1].strength or 0),))
        for slot, disease in unpaired_sorted:
            layer = slot.layer
            strength = disease.strength or 0
            if cure_for(layer) >= strength:
                slot.disease_id = None
                subtract_cure(layer, strength)

        n_consequences = pain_sum // max(1, pain_consequence_divisor)
        comp_result = await session.execute(
            select(Complication).where(Complication.source_type == ComplicationSource.DISEASE)
        )
        complications = list(comp_result.scalars().all())
        consequence_msgs = []
        for _ in range(n_consequences):
            if not complications:
                break
            comp = random.choice(complications)
            if comp.trauma_code is not None:
                ok, trauma_msg = await _apply_trauma_by_code(session, patient, comp.trauma_code)
                if ok:
                    consequence_msgs.append(f"Применена травма: <b>{trauma_msg}</b>")
            else:
                consequence_msgs.append(f"Осложнение: <b>{comp.name}</b> — {comp.description}")

        patient.last_cure_time = datetime.utcnow()
        patient_died = not patient.is_alive
        await session.commit()

    msg = "💊 <b>Лечение проведено.</b>"
    if consequence_msgs:
        msg += "\n\n<b>Последствия:</b>\n" + "\n".join(consequence_msgs)
    await message.answer(msg)

    if patient_died:
        await message.answer(
            "Пациент умер в результате лечения.\n"
            "Напоминание: необходимо получить метку репутации."
        )


async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logging.error("BOT_TOKEN не задан или равен заглушке. Проверьте .env и переменные окружения.")
        return

    # Логирование действий пользователей в stdout
    if not user_actions_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        ))
        user_actions_logger.addHandler(handler)
        user_actions_logger.setLevel(logging.INFO)
        user_actions_logger.propagate = False

    dp.update.outer_middleware(UserActionLoggingMiddleware())

    logging.info("Запуск TG-бота (token: %s...)", BOT_TOKEN[:15] if len(BOT_TOKEN) > 15 else "***")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
