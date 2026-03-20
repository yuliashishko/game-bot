import enum
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import String, Integer, Boolean, ForeignKey, DateTime, Enum, BigInteger
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class WeakZone(str, enum.Enum):
    HEAD = "Голова"
    CHEST = "Корпус"
    LEFT_ARM = "Левая рука"
    RIGHT_ARM = "Правая рука"
    LEFT_LEG = "Левая нога"
    RIGHT_LEG = "Правая нога"

class InfectionStatus(str, enum.Enum):
    VACCINATED = "Вакцинирован"
    HEALTHY = "Здоров"
    INFECTED = "Заражён"

class Recipe(str, enum.Enum):
    REPAIR_CLOAK = "Ремонт плащей"
    REPAIR_BOOTS = "Ремонт сапогов"
    REPAIR_GLOVES = "Ремонт перчаток"
    REPAIR_MASKS = "Ремонт масок"
    REPAIR_BANDIT_MASKS = "Ремонт бандитских масок"
    REPAIR_KNIVES = "Ремонт ножей"
    REPAIR_GUNS = "Ремонт пистолетов"
    CRAFT_LOCKPICKS = "Создание отмычек"
    CRAFT_TWYRINE = "Создание твириновых настоек"
    UPGRADE_LOCKS = "Улучшение замков"
    CRAFT_IMMUNICS = "Создание иммуников"
    CRAFT_ANTIBIOTICS = "Создание антибиотиков"
    CRAFT_PAINKILLERS = "Создание обезболивающих"
    CRAFT_CLOAKS = "Создание плащей"
    CRAFT_BOOTS = "Создание сапогов"
    CRAFT_GLOVES = "Создание перчаток"
    CRAFT_MASKS = "Создание масок"
    CRAFT_BANDIT_MASKS = "Создание бандитских масок"
    CRAFT_KNIVES = "Создание ножей"
    CRAFT_LOCKS = "Создание замков"
    REPAIR_ARMY_CLOAKS = "Ремонт армейских плащей"
    CRAFT_SPECIAL_MEDS = "Создание особых лекарств"

class DiseaseType(str, enum.Enum):
    WOUND = "Рана"
    TRAUMA = "Травма"
    SYMPTOM = "Симптом"

class DiseaseKind(str, enum.Enum):
    NON_COMBAT = "Небоевая"
    KNIFE = "Ножевая"
    BULLET = "Пулевая"

class MedType(str, enum.Enum):
    ANTIBIOTIC = "Антибиотики"
    IMMUNIC = "Иммуники"
    PAINKILLER = "Обезболивающие"
    SPECIAL = "Особое"
    NON_WORKING = "Нерабочее лекарство"
    POWDER = "Порошочек"
    PANACEA = "Панацея"
    VACCINE = "Вакцина"

class IngredientCategory(str, enum.Enum):
    HERB = "Трава"
    ORGAN = "Орган"

class IngredientName(str, enum.Enum):
    BROWN_TWYRINE = "Бурая твирь"
    BLOOD_TWYRINE = "Кровавая твирь"
    BLACK_TWYRINE = "Чёрная твирь"
    SAVYUR = "Савьюр"
    WHITE_WHIP = "Белая плеть"
    SECH = "Сечь"
    BRAIN = "Мозг"
    TEETH = "Зубы"
    HEART = "Сердце"
    BLOOD = "Кровь"
    OTHER = "Чётещё"

class ComplicationSource(str, enum.Enum):
    DISEASE = "Болезнь"
    TRAUMA = "Травма"

class DiseaseCompType(str, enum.Enum):
    LIGHT = "Лёгкое"
    SEVERE = "Тяжёлое"

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_name: Mapped[str] = mapped_column(String)
    tg_username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    vk_username: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)  # VK screen_name или отображаемое имя
    # Идентификаторы диалогов для отправки сообщений и флаги подключения платформ
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, unique=True)  # TG user id, в личке = chat_id
    vk_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, unique=True)  # VK peer_id (user id в личке)
    tg_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    vk_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True)
    is_child: Mapped[bool] = mapped_column(Boolean, default=False)
    
    weak_zones: Mapped[list[WeakZone]] = mapped_column(ARRAY(Enum(WeakZone)), default=list)
    twyrine_addiction: Mapped[bool] = mapped_column(Boolean, default=False)
    
    last_infection_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_cure_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    infection_status: Mapped[InfectionStatus] = mapped_column(Enum(InfectionStatus), default=InfectionStatus.HEALTHY)

    slots: Mapped[List["Slot"]] = relationship(
        "Slot", back_populates="user", cascade="all, delete-orphan"
    )

class Skill(Base):
    __tablename__ = "skills"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    is_health: Mapped[bool] = mapped_column(Boolean, default=False)
    pain: Mapped[int] = mapped_column(Integer, default=0)
    recipes: Mapped[list[Recipe]] = mapped_column(ARRAY(Enum(Recipe)), default=list)

class Disease(Base):
    __tablename__ = "diseases"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    type: Mapped[DiseaseType] = mapped_column(Enum(DiseaseType))
    trauma_code: Mapped[Optional[int]] = mapped_column(Integer) 
    operation: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[Optional[DiseaseKind]] = mapped_column(Enum(DiseaseKind))
    energy: Mapped[bool] = mapped_column(Boolean, default=False)
    
    layers: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    health_only: Mapped[bool] = mapped_column(Boolean, default=False)
    strength: Mapped[int] = mapped_column(Integer, default=0)
    pain: Mapped[int] = mapped_column(Integer, default=0)
    
    light_complication: Mapped[bool] = mapped_column(Boolean, default=False)
    severe_complication: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_from_getting: Mapped[bool] = mapped_column(Boolean, default=False)

class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    
    position: Mapped[int] = mapped_column(Integer) # 0..5
    layer: Mapped[int] = mapped_column(Integer, default=1) # 1, 2, 3
    
    skill_id: Mapped[Optional[int]] = mapped_column(ForeignKey("skills.id"))
    disease_id: Mapped[Optional[int]] = mapped_column(ForeignKey("diseases.id"))

    user: Mapped["User"] = relationship("User", back_populates="slots")
    skill: Mapped[Optional["Skill"]] = relationship("Skill")
    disease: Mapped[Optional["Disease"]] = relationship("Disease")

class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[IngredientName] = mapped_column(Enum(IngredientName), unique=True)
    category: Mapped[IngredientCategory] = mapped_column(Enum(IngredientCategory))

class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    med_type: Mapped[MedType] = mapped_column(Enum(MedType))
    cure_layer_1: Mapped[int] = mapped_column(Integer, default=0)
    cure_layer_2: Mapped[int] = mapped_column(Integer, default=0)
    cure_layer_3: Mapped[int] = mapped_column(Integer, default=0)
    pain: Mapped[int] = mapped_column(Integer, default=0)

class Complication(Base):
    __tablename__ = "complications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    source_type: Mapped[ComplicationSource] = mapped_column(Enum(ComplicationSource))
    
    disease_comp_type: Mapped[Optional[DiseaseCompType]] = mapped_column(Enum(DiseaseCompType))
    trauma_code: Mapped[Optional[int]] = mapped_column(Integer)

class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # название для ввода игроком (например "Степь")
    infection_chance: Mapped[int] = mapped_column(Integer, default=0) # 0 - 100
    capacity: Mapped[int] = mapped_column(Integer, default=0)
    quality: Mapped[bool] = mapped_column(Boolean, default=False)
    
    pain_disease_mod: Mapped[int] = mapped_column(Integer, default=0)
    pain_wound_mod: Mapped[int] = mapped_column(Integer, default=0)
    cure_mod: Mapped[int] = mapped_column(Integer, default=0)
    light_comp_mod: Mapped[int] = mapped_column(Integer, default=0) # -1, 0, 1
    severe_comp_mod: Mapped[int] = mapped_column(Integer, default=0) # -1, 0, 1


class UserAction(Base):
    """Журнал действий пользователей в боте (сообщения и нажатия кнопок)."""
    __tablename__ = "user_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)  # Telegram user id
    username: Mapped[Optional[str]] = mapped_column(String)
    chat_id: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String)  # "message" | "callback"
    details: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class GameSettings(Base):
    """Глобальные настройки игры (одна строка). Режим ночи — админы включают/выключают."""
    __tablename__ = "game_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    night_active: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_active: Mapped[bool] = mapped_column(Boolean, default=False)
    pain_disease_mod: Mapped[int] = mapped_column(Integer, default=1)
    pain_wound_mod: Mapped[int] = mapped_column(Integer, default=3)
    cure_mod: Mapped[int] = mapped_column(Integer, default=-1)
    light_comp_mod: Mapped[int] = mapped_column(Integer, default=1)
    severe_comp_mod: Mapped[int] = mapped_column(Integer, default=1)
    pain_death_threshold: Mapped[int] = mapped_column(Integer, default=10)
    pain_consequence_divisor: Mapped[int] = mapped_column(Integer, default=3)


class NightPeriod(Base):
    """Период ночи: когда был включен и когда завершен."""
    __tablename__ = "night_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class NightStay(Base):
    """Факт ночёвки игрока в рамках периода ночи."""
    __tablename__ = "night_stays"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("night_periods.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    stayed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    auto_applied: Mapped[bool] = mapped_column(Boolean, default=False)
