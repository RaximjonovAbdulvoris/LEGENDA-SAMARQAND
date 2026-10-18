"""Park-specific routing and form contracts; no real Telegram requests."""
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch

os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123:offline')
from bot.handlers import operator
from bot.queue_moves import move_to_review
from test_archive_albums import Bot, albums
from scripts.check_release_routes import problems


class LegendaUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_city_moves_to_its_own_queue_and_deletes_only_source_zip(self):
        for city, target in (('tashkent', '-801'), ('andijon', '-802')):
            for action in ('incomplete', 'otherpark'):
                with self.subTest(city=city, action=action), tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                    'PERSIST_DIR': tmp,
                    'TASHKENT_INCOMPLETE_GROUP': '-801', 'ANDIJON_INCOMPLETE_GROUP': '-802',
                    'TASHKENT_OTHER_PARK_GROUP': '-801', 'ANDIJON_OTHER_PARK_GROUP': '-802',
                }):
                    bot = Bot()
                    ctx = N(bot=bot, bot_data={})
                    media = albums()
                    ids = [i for album in media for i in album['source_message_ids']]
                    key = operator.register_application(ctx, applicant_id=9, region=city, kind='driver',
                        group_chat_id=-100, photo_msg_ids=ids, kb_msg_id=20,
                        media_albums=media, code='LG-000001')
                    record = ctx.bot_data['applications'][key]
                    record['zip_message_ids'] = [999]
                    success, _ = await move_to_review(ctx, key, record, action)
                    self.assertTrue(success)
                    self.assertTrue(all(str(call['chat_id']) == target for call in bot.groups))
                    self.assertIn({'chat_id': -100, 'message_id': 999}, bot.deletes)
                    child = next(iter(ctx.bot_data['applications'].values()))
                    self.assertEqual(child['region'], city)
                    self.assertNotIn('zip_message_ids', child)

    def test_missing_city_queue_never_uses_shared_or_other_city(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'PERSIST_DIR': tmp, 'TASHKENT_INCOMPLETE_GROUP': '',
            'ANDIJON_INCOMPLETE_GROUP': '-999', 'INCOMPLETE_GROUP': '-888',
        }):
            self.assertTrue(any('tashkent / chala' in error for error in problems()))

    def test_duplicate_destinations_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'PERSIST_DIR': tmp, 'TASHKENT_INCOMPLETE_GROUP': '-999',
            'ANDIJON_OTHER_PARK_GROUP': '-999',
        }):
            self.assertTrue(any('bir xil guruh' in error for error in problems()))

    def test_no_sheet_or_brand_review_jobs_are_installed(self):
        from bot.main import register_handlers
        from unittest.mock import MagicMock
        app = MagicMock()
        register_handlers(app)
        patterns = [str(getattr(call.args[0], 'pattern', '')) for call in app.add_handler.call_args_list]
        self.assertFalse(any('brandreview:' in pattern for pattern in patterns))
        root = Path(__file__).resolve().parents[1] / 'bot'
        self.assertFalse((root / 'brand_sheets.py').exists())
        self.assertFalse((root / 'brand_review.py').exists())

    async def test_uncertain_brand_send_keeps_data_and_prevents_duplicate(self):
        from bot.handlers import brand
        from telegram.error import TimedOut
        from unittest.mock import AsyncMock
        from test_regions import context, update
        ctx = context('andijon')
        ctx.user_data.update(_application_region='andijon', b_name='Test Person',
            b_phone='998901234567', b_model='Cobalt', b_year='2024', b_color='White')
        ctx.bot.send_message = AsyncMock(side_effect=TimedOut())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PERSIST_DIR': tmp}), \
             patch.object(brand, 'application_group', return_value='-999'):
            self.assertEqual(await brand.brand_get_plate(update('01A123BC'), ctx), brand.BRAND_PLATE)
            self.assertEqual(await brand.brand_get_plate(update('01A123BC'), ctx), brand.BRAND_PLATE)
        self.assertEqual(ctx.bot.send_message.await_count, 1)
        self.assertEqual(ctx.user_data['b_name'], 'Test Person')
        self.assertTrue(ctx.user_data['_delivery_uncertain'])
