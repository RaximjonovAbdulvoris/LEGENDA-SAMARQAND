import asyncio
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from bot import background_ocr as bg


class BackgroundOCRTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        record = dict(code='LG-000001', applicant_id=9, kind='driver', group_chat_id=-2001,
            kb_msg_id=100, ocr_status='pending', ocr_input={'passport_front': 'file'},
            ocr_prefix='PREFIX\n', ocr_suffix='\nSUFFIX', archive_state={})
        key = (-2001, 100)
        app = N(bot_data={'applications': {key: record}}, persistence=None,
            bot=N(edit_message_text=AsyncMock(), send_message=AsyncMock()))
        return app, key, record

    async def test_existing_card_enriched_with_current_progress(self):
        app, key, record = self.app()
        app.bot_data['progress_state'] = {key: True}
        with patch.object(bg, 'extract_documents', AsyncMock(return_value={
                'status': 'partial', 'values': {'pinfl': '12345678901234'}})):
            await bg.enrich(app, record['code'], 9)
        args = app.bot.edit_message_text.await_args.kwargs
        self.assertEqual(args['chat_id'], -2001)
        self.assertIn('12345678901234', args['text'])
        self.assertTrue(any('🟡' in button.text for row in args['reply_markup'].inline_keyboard for button in row))
        self.assertEqual(record['ocr_status'], 'needs_review')
        self.assertNotIn('ocr_input', record)
        app.bot.send_message.assert_not_awaited()

    async def test_deleted_application_never_recreated(self):
        app, key, record = self.app()
        async def extract(*args):
            app.bot_data['applications'].clear()
            return {'status': 'partial', 'values': {}}
        with patch.object(bg, 'extract_documents', side_effect=extract):
            await bg.enrich(app, record['code'], 9)
        app.bot.edit_message_text.assert_not_awaited()
        app.bot.send_message.assert_not_awaited()

    async def test_move_follows_destination_and_preserves_review_buttons(self):
        app, key, record = self.app()
        child = dict(record, group_chat_id=-2002, kb_msg_id=200, queue_kind='incomplete')
        async def extract(*args):
            record['_transfer'] = {'destination_key': (-2002, 200)}
            app.bot_data['applications'][(-2002, 200)] = child
            return {'status': 'partial', 'values': {}}
        with patch.object(bg, 'extract_documents', side_effect=extract):
            await bg.enrich(app, record['code'], 9)
        args = app.bot.edit_message_text.await_args.kwargs
        self.assertEqual((args['chat_id'], args['message_id']), (-2002, 200))
        self.assertEqual(sum(len(row) for row in args['reply_markup'].inline_keyboard), 3)
        self.assertEqual(child['ocr_status'], 'needs_review')

    async def test_archive_in_progress_not_overwritten(self):
        app, key, record = self.app()
        record['archive_state'] = {'kb': True}
        with patch.object(bg, 'extract_documents', AsyncMock(return_value={'values': {}})):
            await bg.enrich(app, record['code'], 9)
        app.bot.edit_message_text.assert_not_awaited()

    async def test_action_lock_prevents_stale_edit(self):
        app, key, record = self.app()
        lock = bg.control_lock(*key)
        await lock.acquire()
        with patch.object(bg, 'extract_documents', AsyncMock(return_value={'values': {}})):
            task = asyncio.create_task(bg.enrich(app, record['code'], 9))
            await asyncio.sleep(.01)
            app.bot.edit_message_text.assert_not_awaited()
            app.bot_data['applications'].clear()
            lock.release()
            await task
        app.bot.edit_message_text.assert_not_awaited()

    async def test_persisted_pending_records_resume_once(self):
        app, key, record = self.app()
        app.bot_data['applications'][(-2002, 200)] = dict(record)
        with patch.object(bg, 'schedule_ocr') as schedule:
            await bg.resume_pending(N(application=app))
        schedule.assert_called_once_with(app, record['code'], 9)

    async def test_inference_uses_application_snapshot(self):
        app, key, record = self.app()
        with patch.object(bg, 'extract_documents', AsyncMock(return_value={'values': {}})) as extract:
            await bg.enrich(app, record['code'], 9)
        self.assertEqual(extract.await_args.args[1], {'passport_front': 'file'})

class OCRResilienceTests(unittest.IsolatedAsyncioTestCase):
    app = BackgroundOCRTests.app

    async def test_transient_retry_keeps_values_and_stops_when_technical_failure_resolves(self):
        app, _, record = self.app()
        responses = [
            {'status': 'partial', 'values': {'pinfl': '12345678901234'}, 'failed_sources': ['tech_back']},
            {'status': 'partial', 'values': {'birth_date': '01.02.1990'}, 'failed_sources': []},
        ]
        with patch.object(bg, 'extract_documents', AsyncMock(side_effect=responses)) as extract:
            await bg.enrich(app, record['code'], 9)
            self.assertEqual(record['ocr_status'], 'retry')
            self.assertIn('ocr_input', record)
            await bg.enrich(app, record['code'], 9)
        self.assertEqual(extract.await_count, 2)
        self.assertEqual(record['ocr_status'], 'needs_review')
        self.assertEqual(record['ocr_result']['values']['pinfl'], '12345678901234')
        self.assertEqual(record['ocr_result']['values']['birth_date'], '01.02.1990')
        self.assertNotIn('ocr_input', record)

    async def test_retry_is_bounded_and_persistent(self):
        app, _, record = self.app()
        with patch.object(bg, 'extract_documents', AsyncMock(return_value={'status': 'unavailable', 'values': {}})) as extract:
            for _ in range(3):
                await bg.enrich(app, record['code'], 9)
            await bg.enrich(app, record['code'], 9)
        self.assertEqual(extract.await_count, 3)
        self.assertEqual(record['ocr_attempts'], 3)
        self.assertEqual(record['ocr_status'], 'needs_review')
        self.assertNotIn('ocr_input', record)

    async def test_card_delivery_retries_cached_result_without_running_ocr_again(self):
        app, _, record = self.app()
        app.bot.edit_message_text.side_effect = [RuntimeError('test'), None]
        with patch.object(bg, 'extract_documents', AsyncMock(return_value={'status': 'partial', 'values': {}})) as extract:
            await bg.enrich(app, record['code'], 9)
            self.assertTrue(record['ocr_delivery_pending'])
            await bg.enrich(app, record['code'], 9)
        extract.assert_awaited_once()
        self.assertFalse(record['ocr_delivery_pending'])

    async def test_concurrent_applications_are_serialized_without_dropping_any(self):
        app, key, record = self.app()
        for number in (2, 3):
            app.bot_data['applications'][(-2001, 100 + number)] = dict(record,
                code=f'LG-{number:06d}', kb_msg_id=100 + number)
        active = maximum = processed = 0
        async def extract(*args):
            nonlocal active, maximum, processed
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(.01)
            active -= 1
            processed += 1
            return {'status': 'partial', 'values': {}}
        with patch.object(bg, 'extract_documents', side_effect=extract):
            await asyncio.gather(*(bg.enrich(app, f'LG-{number:06d}', 9) for number in (1, 2, 3)))
        self.assertEqual(maximum, 1)
        self.assertEqual(processed, 3)
        self.assertEqual(app.bot.edit_message_text.await_count, 3)

    def test_conflicts_remain_unknown_across_retries(self):
        old = {'values': {'pinfl': '12345678901234'}}
        result = bg.merge_results(old, {'values': {'pinfl': '12345678901235'}})
        result = bg.merge_results(result, {'values': {'pinfl': '12345678901234'}})
        self.assertIsNone(result['values']['pinfl'])
        self.assertIn('pinfl', result['conflicting_fields'])

    async def test_retry_scheduler_respects_due_time(self):
        app, _, record = self.app()
        record.update(ocr_status='retry', ocr_retry_at=bg.time.time()+3600)
        with patch.object(bg, 'schedule_ocr') as schedule:
            await bg.resume_pending(N(application=app))
            schedule.assert_not_called()
            record['ocr_retry_at'] = 0
            await bg.resume_pending(N(application=app))
            schedule.assert_called_once()

class OCRRestartSafetyTests(unittest.IsolatedAsyncioTestCase):
    app = BackgroundOCRTests.app

    async def test_restart_does_not_exceed_persisted_attempt_limit(self):
        app, _, record = self.app()
        record['ocr_attempts'] = 3
        with patch.object(bg, 'extract_documents', AsyncMock()) as extract:
            await bg.enrich(app, record['code'], 9)
        extract.assert_not_awaited()
        self.assertEqual(record['ocr_status'], 'needs_review')
        self.assertNotIn('ocr_input', record)

    async def test_scheduler_uses_destination_state_after_move(self):
        app, key, record = self.app()
        child_key = (-2002, 200)
        record['_transfer'] = {'destination_key': child_key}
        child = dict(record, group_chat_id=-2002, kb_msg_id=200, ocr_status='needs_review')
        child.pop('_transfer')
        child.pop('ocr_input')
        app.bot_data['applications'][child_key] = child
        with patch.object(bg, 'schedule_ocr') as schedule:
            await bg.resume_pending(N(application=app))
        schedule.assert_not_called()
