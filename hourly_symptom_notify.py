"""
Уведомления игроку о почасовом симптоме заражения (Telegram и/или VK).
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Optional

import aiohttp

from config import BOT_TOKEN, VK_TOKEN

if TYPE_CHECKING:
    from database.models import User

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.199"


async def notify_hourly_symptom(
    message_text: str,
    *,
    vk_id: Optional[int] = None,
    telegram_id: Optional[int] = None,
    user_db_id: Optional[int] = None,
) -> None:
    """
    Отправляет текст игроку в VK и/или Telegram, если заданы идентификаторы.
    Вызывать после commit сессии (передавать примитивы, не ORM-объект).
    """
    prefix = "🦠 Автоматически применён новый симптом заражения:\n\n"
    body = prefix + message_text
    log_suffix = f"user_db_id={user_db_id}" if user_db_id is not None else ""

    if vk_id and VK_TOKEN:
        try:
            await _send_vk(vk_id, body)
            logger.info("Уведомление о симптоме отправлено в VK peer_id=%s %s", vk_id, log_suffix)
        except Exception as e:
            logger.warning("Не удалось отправить VK %s: %s", log_suffix, e)

    if telegram_id and BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
        try:
            await _send_telegram(telegram_id, body)
            logger.info("Уведомление о симптоме отправлено в TG chat_id=%s %s", telegram_id, log_suffix)
        except Exception as e:
            logger.warning("Не удалось отправить Telegram %s: %s", log_suffix, e)


async def notify_hourly_symptom_from_user(user: "User", message_text: str) -> None:
    """Обертка: взять id из ORM-объекта (до expire после commit лучше не использовать)."""
    await notify_hourly_symptom(
        message_text,
        vk_id=user.vk_id,
        telegram_id=user.telegram_id,
        user_db_id=user.id,
    )


async def _send_vk(peer_id: int, text: str) -> None:
    params = {
        "peer_id": peer_id,
        "message": text[:4096],
        "random_id": random.randint(1, 2**31 - 1),
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.vk.com/method/messages.send", params=params) as resp:
            data = await resp.json()
    if "error" in data:
        raise RuntimeError(data["error"])


async def _send_telegram(chat_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text[:4096]}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", data))
