"""
Общая логика CLI: одна строка игрока (CSV) → parse → upsert_player_from_row_dict.
Используется в add_player_row.py и add_new_character_row.py.
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from database import async_session
from player_import import parse_player_row_text, upsert_player_from_row_dict


def pick_data_line(content: str, log: logging.Logger) -> tuple[str, list[str]]:
    """Одна строка данных; опционально первая строка — заголовок character_name,..."""
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    warnings: list[str] = []
    if not lines:
        return "", warnings
    if lines[0].startswith("character_name,"):
        log.info("Обнаружен заголовок CSV, берём следующую строку как данные.")
        lines = lines[1:]
    if not lines:
        return "", warnings
    if len(lines) > 1:
        warnings.append(
            f"Найдено {len(lines)} непустых строк; обрабатывается только первая строка данных."
        )
    return lines[0], warnings


async def run_one_player_row(
    path: str | None,
    *,
    log: logging.Logger,
    replace_existing: bool,
    reject_if_existing_tg: bool,
) -> int:
    """
    reject_if_existing_tg: если upsert вернул user=None (уже есть tg_username), считать ошибкой (код 1).
    """
    if path:
        p = Path(path)
        log.info("Чтение файла: %s (существует=%s)", p.resolve(), p.is_file())
        if not p.is_file():
            log.error("Файл не найден: %s", p.resolve())
            return 1
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            log.exception("Не удалось прочитать файл")
            return 1
    else:
        log.info("Чтение одной строки из stdin (UTF-8)")
        try:
            raw = sys.stdin.read()
        except Exception:
            log.exception("Ошибка чтения stdin")
            return 1

    log.debug("Сырые данные: %d символов", len(raw))
    line, extra_warnings = pick_data_line(raw, log)
    for w in extra_warnings:
        log.warning("%s", w)

    if not line:
        log.error("Пустая строка данных (нет содержимого для импорта).")
        return 1

    if log.isEnabledFor(logging.DEBUG):
        preview = line[:500] + ("…" if len(line) > 500 else "")
        log.debug("Первые символы строки (до 500): %r", preview)

    try:
        row = parse_player_row_text(line)
    except ValueError as e:
        log.error("Ошибка разбора строки как CSV-строки игрока: %s", e)
        log.debug("Полный traceback:\n%s", traceback.format_exc())
        return 1
    except Exception:
        log.exception("Неожиданная ошибка при parse_player_row_text")
        return 1

    cn = (row.get("character_name") or "").strip()
    tg = (row.get("tg_username") or "").strip()
    log.info(
        "Распарсено: character_name=%r, tg_username=%r, vk (сырое поле)=%r",
        cn,
        tg,
        (row.get("vk_username") or "")[:120],
    )
    if not tg:
        log.error("После разбора пустой tg_username — импорт невозможен.")
        return 1

    async with async_session() as session:
        try:
            user, info = await upsert_player_from_row_dict(
                session, row, replace_existing=replace_existing
            )
        except Exception:
            log.exception(
                "Ошибка БД в upsert_player_from_row_dict (до commit). "
                "Проверьте уникальные ключи, enum рецептов, целостность JSON навыков."
            )
            try:
                await session.rollback()
            except Exception:
                log.exception("Дополнительная ошибка при rollback")
            return 1

        try:
            await session.commit()
        except Exception:
            log.exception("Ошибка commit — транзакция откатана")
            try:
                await session.rollback()
            except Exception:
                log.exception("Ошибка при rollback после неудачного commit")
            return 1

    if user is None:
        if reject_if_existing_tg:
            log.error(
                "Новый персонаж не создан: уже есть пользователь с tg_username=%r "
                "(или импорт отклонён по политике replace_existing). Текст: %s. "
                "Чтобы обновить существующую запись, используйте: python scripts/add_player_row.py …",
                tg,
                info,
            )
            print(info, file=sys.stdout)
            return 1
        log.warning("Импорт не выполнен (пропуск): %s", info)
        print(info, file=sys.stdout)
        return 2

    log.info(
        "Успех: user.id=%s, character_name=%r, tg_username=%r",
        user.id,
        user.character_name,
        user.tg_username,
    )
    print(info, file=sys.stdout)
    return 0
