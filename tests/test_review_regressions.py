"""Regression checks for bot migration and unexpected form input."""
import os
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")

from bot.handlers import brand, driver
from bot.warmup import warmup_templates


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_bot_discards_only_photo_caches_even_without_routes(self):
        record = {"region": "tashkent"}
        app = N(bot=N(id=22), bot_data={
            "photo_cache_bot_id": 11,
            "template_file_ids": {"selfie": "old-bot-id"},
            "template_file_hashes": {"selfie": "unchanged-image"},
            "tashkent_office_photo_file_id": "old-office-id",
            "tashkent_office_photo_hash": "unchanged-office",
            "applications": {"test": record},
        })
        with patch("bot.warmup.driver_groups", return_value=[]):
            await warmup_templates(app)
        self.assertEqual(app.bot_data, {
            "photo_cache_bot_id": 22, "applications": {"test": record},
        })

    async def test_same_bot_keeps_cached_photo_ids(self):
        app = N(bot=N(id=22), bot_data={
            "photo_cache_bot_id": 22, "template_file_ids": {"selfie": "valid-id"},
        })
        with patch("bot.warmup.driver_groups", return_value=[]):
            await warmup_templates(app)
        self.assertEqual(app.bot_data["template_file_ids"], {"selfie": "valid-id"})

    async def test_wrong_brand_input_stays_at_every_current_question(self):
        conversation = brand.build_brand_conversation()
        for state in (brand.BRAND_NAME, brand.BRAND_PHONE, brand.BRAND_MODEL,
                      brand.BRAND_YEAR, brand.BRAND_COLOR, brand.BRAND_PLATE):
            ctx = N(user_data={"_brand_state": brand.BRAND_NAME})
            update = N(message=N(reply_text=AsyncMock()))
            result = await conversation.states[state][-1].callback(update, ctx)
            self.assertEqual(result, state)

    async def test_missing_first_car_template_does_not_shift_cache_names(self):
        ctx = N(bot_data={"template_file_ids": {"car_back": "back",
                "car_left": "left", "car_right": "right"}})
        message = N(reply_text=AsyncMock(), reply_media_group=AsyncMock(
            return_value=[N(photo=[N(file_id=name)]) for name in
                          ("new-back", "new-left", "new-right")]))
        with patch("bot.handlers.driver.template_path", return_value=None):
            await driver._send_car_photo_prompt(N(message=message), ctx)
        self.assertEqual(ctx.bot_data["template_file_ids"], {
            "car_back": "new-back", "car_left": "new-left", "car_right": "new-right",
        })
        message.reply_text.assert_not_awaited()
