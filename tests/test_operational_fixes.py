import asyncio
import os
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123:test')
from telegram.error import BadRequest, Forbidden, TimedOut
from bot import background_download as downloads
from bot.handlers import operator, driver, brand
from bot.vehicle_numbers import normalize_plate
from bot.neural_ocr import concurrency
from test_operator_safety import FakeBot, FakeMessage, _update
from test_regions import context, update


class OperationalFixesTests(unittest.IsolatedAsyncioTestCase):
    def setup_case(self):
        bot = FakeBot()
        bot.id = 777
        bot.send_document = AsyncMock(return_value=N(message_id=88))
        ctx = N(bot=bot, bot_data={})
        key = operator.register_application(ctx, applicant_id=9, region='tashkent', kind='driver',
            group_chat_id=-1001, photo_msg_ids=[10], kb_msg_id=20, code='LG-000001')
        return bot, ctx, key, ctx.bot_data['applications'][key], FakeMessage(20, -1001)

    async def jobs(self):
        await asyncio.gather(*list(downloads._JOBS.get(asyncio.get_running_loop(), {}).values()))

    def test_plate_validation_covers_supported_formats(self):
        self.assertEqual(normalize_plate('01 a 123 bc'), '01A123BC')
        self.assertEqual(normalize_plate('60-123-ABC'), '60123ABC')
        for value in ('AAAAA', '!!!!!', '01А123BC', '12345', '<b>01A123BC</b>'):
            self.assertIsNone(normalize_plate(value))

    async def test_bad_plate_stays_in_both_forms_without_sending(self):
        for function, state in ((driver.get_car_plate, driver.CAR_PLATE), (brand.brand_get_plate, brand.BRAND_PLATE)):
            ctx = context('tashkent')
            self.assertEqual(await function(update('AAAAA'), ctx), state)
            ctx.bot.send_message.assert_not_awaited()

    async def test_zip_callback_returns_while_slow_download_is_running(self):
        bot, ctx, key, record, message = self.setup_case()
        entered, release = asyncio.Event(), asyncio.Event()
        async def prepare(*args):
            entered.set()
            await release.wait()
            return 'zip', record['code']
        with patch('bot.application_download.prepare_archive', side_effect=prepare):
            await asyncio.wait_for(operator.on_operator_button(_update('op:download:9', message), ctx), .5)
            await asyncio.wait_for(entered.wait(), .5)
            # A second action on this same application responds instead of
            # holding the bot's update dispatcher on the download lock.
            await asyncio.wait_for(operator.on_operator_button(_update('op:ready:9', message), ctx), .5)
            self.assertFalse(bot.copies)
            self.assertFalse(bot.deletes)
            release.set()
            await self.jobs()
        self.assertEqual(record['zip_message_ids'], [88])
        self.assertNotIn('zip_delivery_state', record)

    async def test_timeout_is_durable_and_blocks_duplicate_send_and_move(self):
        bot, ctx, key, record, message = self.setup_case()
        bot.send_document.side_effect = TimedOut()
        states = []
        async def checkpoint(_):
            states.append(record.get('zip_delivery_state'))
            return True
        with patch('bot.application_download.prepare_archive', AsyncMock(return_value=('zip', record['code']))), \
             patch('bot.queue_moves.checkpoint', side_effect=checkpoint):
            await operator.on_operator_button(_update('op:download:9', message), ctx)
            await self.jobs()
            await operator.on_operator_button(_update('op:download:9', message), ctx)
            await operator.on_operator_button(_update('op:incomplete:9', message), ctx)
        self.assertEqual(states[:2], ['sending', 'unknown'])
        self.assertEqual(record['zip_delivery_state'], 'unknown')
        self.assertEqual(bot.send_document.await_count, 1)
        self.assertFalse(bot.copies)
        self.assertFalse(bot.deletes)

    async def test_failed_checkpoint_never_uploads_zip(self):
        bot, ctx, key, record, message = self.setup_case()
        with patch('bot.application_download.prepare_archive', AsyncMock(return_value=('zip', record['code']))), \
             patch('bot.queue_moves.checkpoint', AsyncMock(return_value=False)):
            await operator.on_operator_button(_update('op:download:9', message), ctx)
            await self.jobs()
        bot.send_document.assert_not_awaited()

    async def test_admin_can_bind_unknown_zip_then_source_cleanup_deletes_it(self):
        bot, ctx, key, record, message = self.setup_case()
        record['zip_delivery_state'] = 'unknown'
        target = N(message_id=88, from_user=N(id=777), document=N(file_name='LG-000001_hujjatlar.zip'))
        event = N(effective_message=N(text='/zip_boglash', reply_to_message=target, sender_chat=None,
                                     reply_text=AsyncMock()), effective_chat=N(id=-1001), effective_user=N(id=1))
        await downloads.recover_zip(event, ctx)
        self.assertEqual(record['zip_message_ids'], [88])
        self.assertNotIn('zip_delivery_state', record)
        self.assertTrue(await operator._delete_operator_application(ctx, key, record))
        self.assertIn({'chat_id': -1001, 'message_id': 88}, bot.deletes)

    async def test_nonadmin_or_foreign_bot_zip_cannot_unlock_unknown_delivery(self):
        bot, ctx, key, record, message = self.setup_case()
        record['zip_delivery_state'] = 'unknown'
        event = N(effective_message=N(text='/zip_boglash', sender_chat=None, reply_text=AsyncMock(),
            reply_to_message=N(message_id=88, from_user=N(id=999), document=N(file_name='LG-000001_hujjatlar.zip'))),
            effective_chat=N(id=-1001), effective_user=N(id=1))
        await downloads.recover_zip(event, ctx)
        self.assertEqual(record['zip_delivery_state'], 'unknown')
        bot.get_chat_member = AsyncMock(return_value=N(status='member'))
        event.effective_message.reply_to_message.from_user.id = 777
        await downloads.recover_zip(event, ctx)
        self.assertEqual(record['zip_delivery_state'], 'unknown')

    async def test_admin_confirms_file_absent_before_retry(self):
        bot, ctx, key, record, message = self.setup_case()
        record['zip_delivery_state'] = 'sending'  # recovered after a process restart
        event = N(effective_message=N(text='/zip_qayta', sender_chat=None, reply_text=AsyncMock(),
            reply_to_message=N(message_id=20)), effective_chat=N(id=-1001), effective_user=N(id=1))
        await downloads.recover_zip(event, ctx)
        self.assertNotIn('zip_delivery_state', record)

    def test_ocr_concurrency_cannot_exceed_two_workers(self):
        for configured, expected in (('1', 1), ('2', 2), ('99', 2), ('0', 1)):
            with patch.dict(os.environ, {'DOCUMENT_OCR_WORKERS': configured}):
                self.assertEqual(concurrency(), expected)

    def test_refresh_logs_classify_error_without_exposing_raw_text(self):
        self.assertEqual(operator._refresh_reason(BadRequest('Message to edit not found')), 'message_missing')
        self.assertEqual(operator._refresh_reason(BadRequest('private contents')), 'bad_request')
        self.assertEqual(operator._refresh_reason(Forbidden('private contents')), 'forbidden')
        self.assertEqual(operator._refresh_reason(TimedOut()), 'network')

    async def test_neural_worker_pool_runs_two_jobs_without_dropping_third(self):
        from bot import neural_ocr
        running = maximum = 0
        async def worker(payload):
            nonlocal running, maximum
            running += 1
            maximum = max(maximum, running)
            await asyncio.sleep(.01)
            running -= 1
            return payload
        with patch.object(neural_ocr, '_worker', side_effect=worker), patch.object(neural_ocr, 'concurrency', return_value=2):
            results = await asyncio.gather(*(neural_ocr.worker(n) for n in range(3)))
        self.assertEqual(results, [0, 1, 2])
        self.assertEqual(maximum, 2)

    async def test_deleted_application_is_not_uploaded_from_queued_zip(self):
        bot, ctx, key, record, message = self.setup_case()
        ctx.bot_data['applications'].clear()
        ctx.bot_data['app_messages'].clear()
        with patch('bot.application_download.prepare_archive', new_callable=AsyncMock) as prepare:
            await downloads._run(ctx, key, record)
        prepare.assert_not_awaited()
        bot.send_document.assert_not_awaited()
