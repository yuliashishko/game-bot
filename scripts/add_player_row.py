#!/usr/bin/env python3
"""
Импорт одной строки игрока в формате CSV (как в «Стартовый бот - Игроки.csv»).
По умолчанию создаёт или **обновляет** пользователя с тем же tg_username.

Запуск:
  python scripts/add_player_row.py путь/к/строке.txt
  cat одна_строка.csv | python scripts/add_player_row.py

Только **новый** персонаж (ошибка, если tg уже занят): scripts/add_new_character_row.py

Логи в stderr: -v DEBUG, -q только WARNING+.
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

LOG = logging.getLogger("add_player_row")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Импорт одной строки игрока (CSV); обновление по tg_username"
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
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Не обновлять существующего пользователя с тем же tg_username",
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
            replace_existing=not args.no_replace,
            reject_if_existing_tg=False,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
