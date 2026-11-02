import os
import tempfile
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
for key in ("DRIVER_GROUP_1", "DRIVER_GROUP_2", "DRIVER_GROUP_3", "DRIVER_GROUP_4", "BRAND_GROUP"):
    os.environ.setdefault(key, "-1001")
from bot import regions
from bot.handlers import start, operator
from bot.route_settings import FARGONA_KEYS, save_settings
from bot.configure import main

class FargonaTests(unittest.IsolatedAsyncioTestCase):
    def test_routes_are_isolated_and_missing_routes_stay_empty(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PERSIST_DIR": folder}, clear=True):
            save_settings({"ANDIJON_DRIVER_GROUP_1": "-111", "TASHKENT_BRAND_GROUP": "-222"})
            self.assertEqual(regions.driver_groups("fargona"), [])
            self.assertEqual(regions.application_group("fargona", "brand"), "")
            self.assertEqual(regions.archive_group("fargona"), "")
            args = ["fargona"] + [f"--{name}={-300-i}" for i, name in enumerate(FARGONA_KEYS)]
            self.assertEqual(main(args), 0)
            self.assertEqual(regions.driver_groups("fargona"), ["-300", "-301", "-302", "-303"])
            self.assertEqual(regions.application_group("fargona", "brand"), "-304")
            self.assertEqual(regions.application_group("fargona", "spectre"), "-305")
            self.assertEqual(regions.archive_group("fargona"), "-306")
            self.assertEqual(regions.application_group("tashkent", "brand"), "-222")

    async def test_office_contact_do_not_use_another_city(self):
        message = N(reply_text=AsyncMock(), reply_photo=AsyncMock())
        update = N(message=message, effective_chat=N(type="private"))
        await start.show_contact(update, N(user_data={"region": "fargona"}, bot_data={}))
        text = message.reply_text.await_args.args[0]
        self.assertIn("Farg‘ona", text.replace("FARG‘ONA", "Farg‘ona"))
        self.assertNotIn("ANDIJON", text)
        message.reply_photo.assert_not_awaited()

        message = N(reply_text=AsyncMock(), reply_photo=AsyncMock(return_value=N(photo=[])))
        update = N(message=message, effective_chat=N(type="private"))
        await start.show_office(update, N(user_data={"region": "fargona"}, bot_data={}))
        message.reply_photo.assert_awaited_once()
        kwargs = message.reply_photo.await_args.kwargs
        self.assertEqual(kwargs["caption"], start.FARGONA_OFFICE_TEXT)
        self.assertIs(kwargs["reply_markup"], start.FARGONA_OFFICE_KEYBOARD)
        self.assertEqual(kwargs["photo"].name, str(start.FARGONA_OFFICE_PHOTO))
        message.reply_text.assert_not_awaited()

    async def test_otherpark_uses_fargona_destination(self):
        ctx = N(bot=AsyncMock(), bot_data={})
        record = {"region": "fargona", "applicant_id": 123, "group_chat_id": -11}
        with patch.object(operator, "destination", return_value="-88") as dest, patch.object(operator, "_archive_application", AsyncMock(return_value=False)):
            self.assertFalse(await operator._move_other_park(ctx, (-11, 1), record))
        dest.assert_called_once_with("FARGONA_OTHER_PARK_GROUP")
        ctx.bot.delete_message.assert_not_awaited()
