"""Use the actual serialized button label to exercise handler dispatch."""
from datetime import datetime, timezone
import os
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, patch
from telegram import Chat, Message, Update
from bot.handlers import brand, driver, start
from bot import presentation
from bot.main import intercept_pending_reply
from bot.premium_emoji import premium_keyboard


def update(text):
    return Update(1, message=Message(1, datetime.now(timezone.utc), Chat(123, 'private'), text=text))


class PremiumMenuDispatchTests(unittest.IsolatedAsyncioTestCase):
    def test_serialized_premium_menu_reaches_every_handler(self):
        icons = {f'PREMIUM_ICON_{kind}': '12345' for kind in ('DRIVER', 'BRAND', 'LOCATION')}
        with patch.dict(os.environ, icons):
            keyboard = premium_keyboard(start.main_keyboard('tashkent'))['keyboard']
        handlers = [driver.build_driver_conversation().entry_points[0],
                    brand.build_brand_conversation().entry_points[0],
                    start.build_contact_handler(), start.build_office_handler()]
        for row, handler in zip(keyboard, handlers):
            with self.subTest(label=row[0]['text']):
                self.assertTrue(handler.check_update(update(row[0]['text'])))
        self.assertTrue(start.build_office_handler().check_update(update(start.MENU_OFFICE)))
        self.assertTrue(handlers[1].check_update(update(start.MENU_BRAND)))

    def test_serialized_brand_continue_button_is_accepted(self):
        with patch.dict(presentation._PACK_ICONS, {'READY': '12345'}):
            label = premium_keyboard(brand.CONTINUE_KB)['keyboard'][0][0]['text']
        handler = brand.build_brand_conversation().states[brand.BRAND_WARN][0]
        self.assertEqual(label, 'Davom etish')
        self.assertTrue(handler.check_update(update(label)))
        self.assertTrue(handler.check_update(update(brand.CONTINUE_BTN)))

    async def test_plain_menu_action_cancels_pending_relay(self):
        for label in ('Ofis manzili', 'Brend Ariza', "Bog'lanish uchun", 'Hududni almashtirish'):
            ctx = N(bot_data={'pending_user_replies': {123: ('old', 1)}})
            message = N(text=label)
            event = N(effective_user=N(id=123), message=message)
            with patch('bot.main.on_user_reply_message', new_callable=AsyncMock) as relay:
                await intercept_pending_reply(event, ctx)
            relay.assert_not_awaited()
            self.assertNotIn(123, ctx.bot_data['pending_user_replies'])
