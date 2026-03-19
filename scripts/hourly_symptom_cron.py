#!/usr/bin/env python3
"""
Крон-задача: каждые 5 минут проверяет заражённых игроков и добавляет симптом,
если с момента последнего заражения/симптома прошёл хотя бы 1 час. В ночь не добавляет.

Настройка cron (каждые 5 минут):
  */5 * * * * cd /path/to/tg_bot && python scripts/hourly_symptom_cron.py

В Docker Compose отдельный сервис (цикл раз в 5 минут):
  docker compose up -d symptom_cron

Уведомления: после применения симптома отправляется сообщение в VK (если у игрока есть vk_id)
и/или в Telegram (если есть telegram_id). Нужны VK_TOKEN и/или BOT_TOKEN в .env.

Или одноразовый запуск:
  docker compose run --rm bot python /app/scripts/hourly_symptom_cron.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import async_session
from game_logic import apply_hourly_symptoms
from hourly_symptom_notify import notify_hourly_symptom

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    async with async_session() as session:
        try:
            applied = await apply_hourly_symptoms(session, skip_if_night=True)
        except Exception as e:
            logger.exception("Ошибка при применении почасовых симптомов: %s", e)
            raise
        if applied:
            notify_queue = [
                (msg, user.vk_id, user.telegram_id, user.id, user.character_name or user.tg_username or user.vk_username)
                for user, msg in applied
            ]
            await session.commit()
            for msg, vk_id, telegram_id, uid, name in notify_queue:
                logger.info("Симптом применён: %s — %s", name, msg.split("\n")[0])
                try:
                    await notify_hourly_symptom(
                        msg,
                        vk_id=vk_id,
                        telegram_id=telegram_id,
                        user_db_id=uid,
                    )
                except Exception as e:
                    logger.warning("Ошибка уведомления пользователю id=%s: %s", uid, e)
        else:
            logger.debug("Нет заражённых для добавления симптома (или ночь)")


if __name__ == "__main__":
    asyncio.run(main())
