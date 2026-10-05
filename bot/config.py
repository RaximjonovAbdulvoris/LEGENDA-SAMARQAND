import os

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

CHANNEL = os.environ.get("CHANNEL", "@WB_HUMO_TAXI")

# Legacy unprefixed settings remain usable for the replaced city.
DRIVER_GROUPS = [
    os.environ.get(f"ANDIJON_DRIVER_GROUP_{i}", os.environ.get(f"DRIVER_GROUP_{i}", ""))
    for i in range(1, 5)
]
BRAND_GROUP = os.environ.get("ANDIJON_BRAND_GROUP", os.environ.get("BRAND_GROUP", ""))
ARCHIVE_GROUP = os.environ.get("ANDIJON_ARCHIVE_GROUP", os.environ.get("ARCHIVE_GROUP", ""))

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def template_path(name: str) -> str | None:
    for ext in ("jpg", "jpeg", "png"):
        p = os.path.join(TEMPLATES_DIR, f"{name}.{ext}")
        if os.path.exists(p):
            return p
    return None
