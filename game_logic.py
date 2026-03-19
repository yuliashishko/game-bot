"""Общая игровая логика для TG и VK ботов (без привязки к платформе)."""
import random
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload

from database import (
    async_session,
    User,
    Disease,
    Skill,
    Slot,
    Location,
    Medicine,
    Complication,
    GameSettings,
    DiseaseType,
    DiseaseKind,
    InfectionStatus,
    MedType,
    ComplicationSource,
    Recipe,
    IngredientCategory,
    IngredientName,
)

# --- Слои слотов (при распределении всегда 3 слоя) ---
# Слой 1: ячейки 1 и 4 по порядку (position 0 и 3)
# Слой 2: ячейки 2 и 5 (position 1 и 4)
# Слой 3: ячейки 3 и 6 (position 2 и 5)
SLOT_POSITIONS_BY_LAYER = {
    1: (0, 3),
    2: (1, 4),
    3: (2, 5),
}


def slot_layer_from_position(position: int) -> int:
    """Возвращает номер слоя (1, 2 или 3) по позиции слота (0..5 и далее по циклу)."""
    return (position % 3) + 1


# Рецепты создания лекарств
MEDICINE_RECIPES = {Recipe.CRAFT_IMMUNICS, Recipe.CRAFT_ANTIBIOTICS, Recipe.CRAFT_PAINKILLERS, Recipe.CRAFT_SPECIAL_MEDS}
RECIPE_TO_MED_TYPE = {
    Recipe.CRAFT_IMMUNICS: MedType.IMMUNIC,
    Recipe.CRAFT_ANTIBIOTICS: MedType.ANTIBIOTIC,
    Recipe.CRAFT_PAINKILLERS: MedType.PAINKILLER,
    Recipe.CRAFT_SPECIAL_MEDS: MedType.SPECIAL,
}

PAIN_DEATH_THRESHOLD = 10
PAIN_CONSEQUENCE_DIVISOR = 3
CURE_COOLDOWN_HOURS = 1


def _user_has_medicine_recipe(user) -> bool:
    for slot in user.slots or []:
        if slot.disease_id is not None:
            continue
        if not slot.skill or not getattr(slot.skill, "recipes", None):
            continue
        for r in slot.skill.recipes:
            if r in MEDICINE_RECIPES:
                return True
    return False


def _user_medicine_recipes(user) -> list:
    seen = set()
    out = []
    for slot in user.slots or []:
        if slot.disease_id is not None or not slot.skill or not getattr(slot.skill, "recipes", None):
            continue
        for r in slot.skill.recipes:
            if r in MEDICINE_RECIPES and r not in seen:
                seen.add(r)
                out.append(r)
    return out


def _user_active_skill_names(user) -> list:
    return [s.skill.name for s in user.slots if s.skill and s.disease_id is None]


async def _resolve_location(session, text: str):
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


def _apply_trauma(session, user, disease) -> tuple:
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


async def get_symptom(session, user) -> str:
    result = await session.execute(select(Disease).where(Disease.type == DiseaseType.SYMPTOM))
    symptoms = list(result.scalars().all())
    if not symptoms:
        return "Вы уже заражены. Автодействие «Получить симптом»: в базе нет симптомов."
    disease = random.choice(symptoms)
    chosen_slot, remaining_health, skill_name = _apply_trauma(session, user, disease)
    if chosen_slot is None:
        return "Вы уже заражены. Автодействие «Получить симптом»: нет свободной ячейки для симптома."
    if remaining_health == 0:
        user.is_alive = False
    msg = f"Вы уже заражены. Выполнено автодействие «Получить симптом»: получен симптом {disease.name}."
    if skill_name and skill_name != "Здоровье":
        msg += f"\n⚠️ Навык {skill_name} временно недоступен."
    if remaining_health == 0:
        msg += "\n💀 Здоровье = 0. Вы мертвы."
    else:
        msg += f"\n❤️ Осталось здоровья: {remaining_health}"
    return msg


HOURLY_SYMPTOM_INTERVAL_HOURS = 1.0


async def apply_hourly_symptoms(session, *, skip_if_night: bool = True):
    """
    Для всех заражённых, у кого от last_infection_time прошёл хотя бы час,
    выполняет «Получить симптом» и обновляет last_infection_time.
    Если skip_if_night и включена ночь (game_settings.night_active), ничего не делает.
    Возвращает список пар (user, message) по каждому применённому симптому.
    """
    if skip_if_night:
        r = await session.execute(select(GameSettings).where(GameSettings.id == 1))
        gs = r.scalar_one_or_none()
        if gs and gs.night_active:
            return []
    now = datetime.utcnow()
    threshold = now - timedelta(hours=HOURLY_SYMPTOM_INTERVAL_HOURS)
    q = (
        select(User)
        .options(
            selectinload(User.slots).selectinload(Slot.skill),
            selectinload(User.slots).selectinload(Slot.disease),
        )
        .where(
            User.infection_status == InfectionStatus.INFECTED,
            User.last_infection_time.is_not(None),
            User.last_infection_time <= threshold,
            User.is_alive == True,
        )
    )
    result = await session.execute(q)
    users = list(result.scalars().unique().all())
    out = []
    for user in users:
        msg = await get_symptom(session, user)
        user.last_infection_time = now
        out.append((user, msg))
    return out


async def apply_infection(session, user) -> str:
    if user.infection_status == InfectionStatus.VACCINATED:
        return "Вы вакцинированы. Действие завершено, ничего не происходит."
    if user.infection_status == InfectionStatus.HEALTHY:
        user.infection_status = InfectionStatus.INFECTED
        user.last_infection_time = datetime.utcnow()
        return "Вы заразились. Статус: Заражён."
    return await get_symptom(session, user)


async def _apply_trauma_by_code(session, user, trauma_code: int):
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


async def do_treat_finalize(session, target_username: str, medicine_codes: list) -> tuple:
    """
    Проводит лечение пациента. Возвращает (msg, patient_died).
    target_username — tg_username или vk_username игрока.
    """
    result = await session.execute(select(Medicine).where(Medicine.code.in_(medicine_codes)))
    medicines = list(result.scalars().all())
    if not medicines:
        return "Лекарства не найдены.", False

    r = await session.execute(
        select(User)
        .options(
            selectinload(User.slots).selectinload(Slot.skill),
            selectinload(User.slots).selectinload(Slot.disease),
        )
        .where(or_(User.tg_username == target_username, User.vk_username == target_username))
    )
    patient = r.scalar_one_or_none()
    if not patient:
        return "Пациент не найден.", False

    pain_disease_mod = 0
    cure_mod = 0
    pain_sum = 0
    for s in patient.slots:
        if s.skill and s.disease_id is None:
            pain_sum += s.skill.pain or 0
        if s.disease:
            pain_sum += s.disease.pain or 0
    for m in medicines:
        pain_sum += m.pain or 0
    pain_sum += pain_disease_mod

    if pain_sum > PAIN_DEATH_THRESHOLD:
        patient.is_alive = False
        await session.commit()
        return (
            "Сумма боли превысила порог. Пациент умер.\nНапоминание: необходимо получить метку репутации.",
            True,
        )

    cure_1 = sum(m.cure_layer_1 or 0 for m in medicines) + cure_mod
    cure_2 = sum(m.cure_layer_2 or 0 for m in medicines) + cure_mod
    cure_3 = sum(m.cure_layer_3 or 0 for m in medicines) + cure_mod

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

    n_consequences = pain_sum // PAIN_CONSEQUENCE_DIVISOR
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
                consequence_msgs.append(f"Применена травма: {trauma_msg}")
        else:
            consequence_msgs.append(f"Осложнение: {comp.name} — {comp.description}")

    patient.last_cure_time = datetime.utcnow()
    patient_died = not patient.is_alive
    await session.commit()

    msg = "💊 Лечение проведено."
    if consequence_msgs:
        msg += "\n\nПоследствия:\n" + "\n".join(consequence_msgs)
    if patient_died:
        msg += "\n\nПациент умер в результате лечения.\nНапоминание: необходимо получить метку репутации."
    return msg, patient_died
