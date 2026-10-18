import os
import tempfile
import unittest
from unittest.mock import patch
from telegram.ext import ConversationHandler
from bot.handlers import brand
from test_regions import context, update


class SpectreFormTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_form_sends_plain_text_to_selected_city_without_number(self):
        for city, target in [('andijon', '-801'), ('tashkent', '-802')]:
            with self.subTest(city=city), tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'PERSIST_DIR': tmp, f'{city.upper()}_SPECTRE_GROUP': target}):
                ctx = context(city)
                msg = update()
                self.assertEqual(await brand.start_spectre(msg, ctx), brand.BRAND_NAME)
                self.assertNotIn('SPARK', msg.message.reply_text.call_args.args[0])
                for handler, text, expected in [
                    (brand.brand_get_name, 'Test Person', brand.BRAND_PHONE),
                    (brand.brand_get_phone, '+998901234567', brand.BRAND_MODEL),
                    (brand.brand_get_model, 'Spark', brand.BRAND_YEAR),
                    (brand.brand_get_year, '2014', brand.BRAND_COLOR),
                    (brand.brand_get_color, 'White', brand.BRAND_PLATE),
                    (brand.brand_get_plate, '01A123BC', ConversationHandler.END)]:
                    self.assertEqual(await handler(update(text), ctx), expected)
                call = ctx.bot.send_message.await_args.kwargs
                self.assertEqual(call['chat_id'], target)
                self.assertIsNone(call['reply_markup'])
                self.assertIn('SPECTRE ENERGY', call['text'])
                for value in ('Test Person', '998901234567', 'Spark', '2014', 'White', '01A123BC'):
                    self.assertIn(value, call['text'])
                self.assertNotIn('LG-', call['text'])
                self.assertFalse(ctx.bot_data.get('applications'))
                self.assertFalse(ctx.bot_data.get('brand_sheets_outbox'))
                self.assertEqual(ctx.user_data, {'region': city})

    async def test_missing_route_does_not_use_brand_or_other_region(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'PERSIST_DIR': tmp, 'TASHKENT_SPECTRE_GROUP': '',
            'ANDIJON_SPECTRE_GROUP': '-801', 'TASHKENT_BRAND_GROUP': '-802'}):
            ctx = context('tashkent')
            self.assertEqual(await brand.start_spectre(update(), ctx), ConversationHandler.END)
            ctx.bot.send_message.assert_not_awaited()
