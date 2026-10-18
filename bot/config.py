import os

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


# Legacy unprefixed settings remain usable for the replaced city.
DRIVER_GROUPS = [
    os.environ.get(f"ANDIJON_DRIVER_GROUP_{i}", os.environ.get(f"DRIVER_GROUP_{i}", ""))
    for i in range(1, 5)
]
BRAND_GROUP = os.environ.get("ANDIJON_BRAND_GROUP", os.environ.get("BRAND_GROUP", ""))
ARCHIVE_GROUP = os.environ.get("ANDIJON_ARCHIVE_GROUP", os.environ.get("ARCHIVE_GROUP", ""))

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def template_path(name: str) -> str | None:
    # Preserve the park's original samples, including its legacy car collage.
    for original in ((name, 'car_sides') if name.startswith('car_') else (name,)):
        for ext in ('jpg', 'jpeg', 'png', 'webp'):
            p = os.path.join(TEMPLATES_DIR, f'{original}.{ext}')
            if os.path.isfile(p):
                return p
    from bot.template_cards import generated_directory
    generated = generated_directory() / f'{name}.png'
    if generated.is_file():
        return str(generated)
    return None
