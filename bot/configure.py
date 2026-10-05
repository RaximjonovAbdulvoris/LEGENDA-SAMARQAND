"""Configure local Telegram destination overrides from a terminal."""
import argparse
import asyncio
import logging
import os

from bot.route_settings import (
    ANDIJON_KEYS, TASHKENT_KEYS, destination, read_settings, save_settings,
    settings_path, validate,
)

LABELS = {
    "driver1": "Haydovchi 1-guruh",
    "driver2": "Haydovchi 2-guruh",
    "driver3": "Haydovchi 3-guruh",
    "driver4": "Haydovchi 4-guruh",
    "brand": "Brend guruhi",
    "archive": "Arxiv guruhi",
}


async def check_routes(scope: str = "tashkent") -> int:
    """Read-only Telegram checks; never poll, send applications or reveal tokens."""
    from telegram import Bot
    from telegram.error import InvalidToken, TelegramError

    logging.getLogger("httpx").setLevel(logging.WARNING)
    scoped_keys = {
        "tashkent": TASHKENT_KEYS,
        "andijon": ANDIJON_KEYS,
        "all": {
            **{f"tashkent_{name}": key for name, key in TASHKENT_KEYS.items()},
            **{f"andijon_{name}": key for name, key in ANDIJON_KEYS.items()},
        },
    }[scope]
    routes = {name: destination(key) for name, key in scoped_keys.items()}
    failures = 0
    for name, value in routes.items():
        try:
            validate(scoped_keys[name], value)
        except ValueError as exc:
            print(exc)
            failures += 1
    if failures:
        return 1
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("TELEGRAM_BOT_TOKEN sozlanmagan. Tokenni xavfsiz muhit sozlamasida kiriting.")
        return 1
    try:
        async with Bot(token=token) as bot:
            for name, value in routes.items():
                if not value:
                    continue
                try:
                    chat = await bot.get_chat(int(value))
                    member = await bot.get_chat_member(chat.id, bot.id)
                    if chat.type not in ("group", "supergroup"):
                        raise ValueError("ID guruhga tegishli emas.")
                    if member.status not in ("administrator", "creator"):
                        raise ValueError("Botni guruh administratori qiling.")
                    if not name.endswith("archive") and member.status != "creator" and not getattr(
                        member, "can_delete_messages", False
                    ):
                        raise ValueError("Botga xabarlarni o'chirish ruxsatini bering.")
                    print(f"{scoped_keys[name]}: OK")
                except ValueError as exc:
                    print(f"{scoped_keys[name]}: {exc}")
                    failures += 1
                except TelegramError as exc:
                    print(f"{scoped_keys[name]}: {type(exc).__name__}; ID va bot ruxsatlarini tekshiring.")
                    failures += 1
    except InvalidToken:
        print("Telegram tokenni qabul qilmadi. TELEGRAM_BOT_TOKEN ni xavfsiz sozlamada yangilang.")
        return 1
    except TelegramError as exc:
        print(f"Telegram ulanish xatosi: {type(exc).__name__}.")
        return 1
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Guruhlarni terminaldan sozlash.")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("tashkent", help="IDlarni kiritish yoki o'zgartirish")
    for name in TASHKENT_KEYS:
        setup.add_argument(f"--{name}", help=LABELS[name])
    andijon = commands.add_parser("andijon", help="Andijon guruhlarini sozlash")
    for name in ANDIJON_KEYS:
        andijon.add_argument(f"--{name}", help=LABELS[name])
    commands.add_parser("show", help="Amaldagi IDlar va ularning manbasini ko'rish")
    check = commands.add_parser("check", help="Token, guruhlar va bot ruxsatlarini tekshirish")
    check.add_argument(
        "--scope", choices=("tashkent", "andijon", "all"), default="tashkent",
        help="Tekshiriladigan shahar (standart: tashkent)",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return asyncio.run(check_routes(args.scope))
        if args.command == "show":
            local = read_settings()
            print(f"Sozlama fayli: {settings_path()}")
            for key in (*TASHKENT_KEYS.values(), *ANDIJON_KEYS.values()):
                source = "CMD/fayl" if key in local else "muhit"
                print(f"{key}: {destination(key) or 'sozlanmagan'} ({source})")
            return 0
        keys = TASHKENT_KEYS if args.command == "tashkent" else ANDIJON_KEYS
        changes = {
            key: getattr(args, name) for name, key in keys.items()
            if getattr(args, name) is not None
        }
        if not changes:
            print("Mavjud IDni saqlash uchun Enter bosing. Token kiritmang.")
            for name, key in keys.items():
                current = destination(key)
                answer = input(f"{LABELS[name]} [{current or 'sozlanmagan'}]: ").strip()
                changes[key] = answer or current
        # Validate the complete effective setup before saving any change.
        for name, key in keys.items():
            validate(key, changes.get(key, destination(key)))
        save_settings(changes)
        print(f"Saqlandi: {settings_path()}")
        print("Bu IDlar muhit sozlamalaridan ustun. Bot shu kompyuterda ishlashi kerak.")
        print(f"Tekshirish: python -m bot.configure check --scope {args.command}")
        return 0
    except (ValueError, OSError) as exc:
        print(f"Sozlash xatosi: {exc}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nBekor qilindi; IDlar o'zgartirilmadi.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
