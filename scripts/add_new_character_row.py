#!/usr/bin/env python3
"""
Подгрузка **только нового** персонажа: одна строка CSV (как в таблице игроков).

Если в БД уже есть пользователь с таким **tg_username** — скрипт завершится с ошибкой (код 1)
и подробным логом; существующую запись **не** перезаписывает.

Конфликты по чужому vk/tg (другой персонаж держит тот же vk) обрабатываются как в player_import:
у старого записи tg → ЗАМЕНЕН_<id>, VK отвязан — чтобы новый персонаж мог занять идентификаторы.

Для создания **или обновления** по tg_username используйте: scripts/add_player_row.py

Примеры:
  python scripts/add_new_character_row.py новый_игрок.txt
  cat строка.csv | python scripts/add_new_character_row.py
  docker compose exec bot python scripts/add_new_character_row.py -v /app/data/row.txt
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
for p in (_ROOT, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import player_row_cli  # noqa: E402

LOG = logging.getLogger("add_new_character_row")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Добавить только нового персонажа (ошибка, если tg_username уже занят)"
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Файл с одной строкой данных. Без аргумента — stdin.",
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="file_opt",
        metavar="PATH",
        help="Явный путь к файлу",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG")
    parser.add_argument("-q", "--quiet", action="store_true", help="Только WARNING+")
    args = parser.parse_args()
    path = args.file_opt or args.file

    if args.verbose and args.quiet:
        print("Нельзя одновременно -v и -q", file=sys.stderr)
        sys.exit(1)

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s [%(name)s] %(message)s", stream=sys.stderr)
    if not args.verbose:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    code = asyncio.run(
        player_row_cli.run_one_player_row(
            path,
            log=LOG,
            replace_existing=False,
            reject_if_existing_tg=True,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
