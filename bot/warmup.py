"""Pre-upload all template images on bot startup so conversations are instant.

Each template is uploaded once to a "warmup" chat (the first driver group),
the resulting Telegram file_id is cached in `bot_data["template_file_ids"]`,
and the temporary message is deleted right after.
After this, every photo prompt during a conversation reuses the cached
file_id instead of re-uploading from disk — much faster and immune to
local network slowness.
"""
import logging
import os
from hashlib import sha256
from io import BytesIO

from telegram.ext import Application

from bot.config import TEMPLATES_DIR, template_path
from bot.regions import ANDIJON, TASHKENT, driver_groups

logger = logging.getLogger(__name__)

TEMPLATE_NAMES = [
    "passport_front",
    "passport_back",
    "license_front",
    "license_back",
    "tech_front",
    "tech_back",
    "selfie",
    "litsenziya",
    "car_front",
    "car_back",
    "car_left",
    "car_right",
]


async def warmup_templates(app: Application) -> None:
    """Pre-upload templates and cache file_ids."""
    groups = driver_groups(ANDIJON) or driver_groups(TASHKENT)
    if not groups:
        logger.warning("warmup: DRIVER_GROUPS empty, skipping")
        return

    warmup_chat = groups[0]
    cache: dict[str, str] = app.bot_data.setdefault("template_file_ids", {})
    hashes: dict[str, str] = app.bot_data.setdefault("template_file_hashes", {})

    for name in TEMPLATE_NAMES:
        path = template_path(name)
        if not path or not os.path.exists(path):
            logger.warning("warmup: template '%s' not found, skipping", name)
            continue

        with open(path, "rb") as template_file:
            data = template_file.read()
        file_hash = sha256(data).hexdigest()
        if cache.get(name) and hashes.get(name) == file_hash:
            continue

        # Do not let a persisted file_id for an older image survive a change.
        cache.pop(name, None)
        hashes.pop(name, None)
        try:
            upload = BytesIO(data)
            upload.name = os.path.basename(path)
            msg = await app.bot.send_photo(
                chat_id=warmup_chat,
                photo=upload,
                caption="🔄 _warmup_",
                parse_mode="Markdown",
                disable_notification=True,
            )
            if msg and msg.photo:
                cache[name] = msg.photo[-1].file_id
                hashes[name] = file_hash
                logger.info("warmup: '%s' cached", name)
            # Try to delete the warmup message — if it fails, no big deal
            try:
                await app.bot.delete_message(
                    chat_id=warmup_chat, message_id=msg.message_id
                )
            except Exception as e:
                logger.warning("warmup: could not delete temp message: %s", e)
        except Exception as e:
            logger.warning("warmup: '%s' upload failed: %s", name, e)

    logger.info("warmup: %d/%d templates cached",
                len(cache), len(TEMPLATE_NAMES))
