import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, patch

from test_operator_safety import FakeBot, FakeMessage, _update
from test_regions import context
from bot.application_ids import application_code
from bot.handlers import driver, operator
from bot.document_ocr import normalize
from bot.presentation import button, send_brand_sticker


class ApplicationRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch.dict(os.environ, {'PERSIST_DIR': str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def application(self):
        bot = FakeBot()
        ctx = N(bot=bot, bot_data={})
        key = operator.register_application(ctx, applicant_id=9, region='tashkent',
               kind='driver', group_chat_id=-1001, photo_msg_ids=[10,11], kb_msg_id=20,
               code='LG-000001')
        return bot, ctx, key, FakeMessage(20, -1001)

    def test_numbers_survive_new_context_and_retry(self):
        first = {}
        self.assertEqual(application_code(first), 'LG-000001')
        self.assertEqual(application_code(first), 'LG-000001')
        self.assertEqual(application_code({}), 'LG-000002')
        self.assertTrue((self.root / 'application_numbers.sqlite3').is_file())

    def test_concurrent_allocations_are_unique(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            codes = list(executor.map(lambda _: application_code({}), range(24)))
        self.assertEqual(len(set(codes)),24)
        self.assertEqual(sorted(codes)[-1], 'LG-000024')

    async def test_progress_toggles_in_both_directions(self):
        bot,ctx,key,msg = self.application()
        update = _update('op:progress:9',msg)
        update.callback_query.edit_message_reply_markup = AsyncMock()
        await operator.on_operator_button(update,ctx)
        self.assertTrue(ctx.bot_data['progress_state'][key])
        await operator.on_operator_button(update,ctx)
        self.assertFalse(ctx.bot_data['progress_state'][key])
        markup = update.callback_query.edit_message_reply_markup.await_args.kwargs['reply_markup']
        self.assertTrue(any('🔴' in b.text for row in markup.inline_keyboard for b in row))

    async def test_failed_progress_edit_does_not_change_state(self):
        bot,ctx,key,msg = self.application()
        update = _update('op:progress:9',msg)
        update.callback_query.edit_message_reply_markup = AsyncMock(side_effect=RuntimeError())
        await operator.on_operator_button(update,ctx)
        self.assertFalse(ctx.bot_data.get('progress_state',{}).get(key,False))

    async def test_queue_missing_never_notifies_or_deletes(self):
        bot,ctx,key,msg = self.application()
        with patch('bot.queue_moves.destination', return_value=''):
            await operator.on_operator_button(_update('op:incomplete:9',msg),ctx)
        self.assertEqual(bot.sent,[])
        self.assertEqual(bot.copies,[])
        self.assertIn(key,ctx.bot_data['applications'])

    async def test_queue_failure_never_notifies_and_preserves_source(self):
        bot,ctx,key,msg = self.application()
        bot.batch_failures=1
        with patch('bot.queue_moves.destination', return_value='-2001'):
            await operator.on_operator_button(_update('op:incomplete:9',msg),ctx)
        self.assertEqual(bot.sent,[])
        self.assertEqual(bot.deletes,[])
        self.assertIn(key,ctx.bot_data['applications'])

    async def test_notice_retry_does_not_duplicate_queue_copies(self):
        bot,ctx,key,msg = self.application()
        bot.send_message=AsyncMock(side_effect=[RuntimeError('blocked'),N(message_id=100)])
        with patch('bot.queue_moves.destination',return_value='-2001'):
            await operator.on_operator_button(_update('op:incomplete:9',msg),ctx)
            await operator.on_operator_button(_update('op:incomplete:9',msg),ctx)
        self.assertEqual(len(bot.batch_copies),1)
        self.assertEqual(len(bot.copies),1)
        self.assertEqual(bot.send_message.await_count,2)
        self.assertEqual(len(bot.deletes),3)
        self.assertNotIn(key,ctx.bot_data['applications'])
        child=next(iter(ctx.bot_data['applications'].values()))
        self.assertEqual(child['notification_status'],'failed')

    async def test_previous_notice_is_forwarded_without_notifying_again(self):
        bot,ctx,key,msg = self.application()
        ctx.bot_data['applications'][key]['sent_notices']={'otherpark':True}
        with patch('bot.queue_moves.destination',return_value='-2002'):
            await operator.on_operator_button(_update('op:otherpark:9',msg),ctx)
        self.assertEqual(len(bot.batch_copies),1)
        self.assertEqual(bot.sent,[])
        self.assertNotIn(key,ctx.bot_data['applications'])
        self.assertEqual(next(iter(ctx.bot_data['applications'].values()))['queue_kind'],'otherpark')

    async def test_combined_card_and_album_share_number(self):
        ctx=context('tashkent')
        ctx.user_data.update(_application_region='tashkent',user_id=123,name='Ali',
                             phone='+998901234567',car_plate='01A123BC',
                             passport_front='p1',passport_back='p2',selfie='s',litsenziya='l')
        with patch('bot.handlers.driver.driver_groups',return_value=['-1001']), patch(
            'bot.handlers.driver.prepare_document_summary',AsyncMock(return_value='AI SUMMARY')):
            await driver._send_to_driver_group(ctx)
        info=ctx.bot.send_message.await_args.kwargs['text']
        code=ctx.user_data['_application_code']
        self.assertIn('Familiya: ⚠️ Aniqlanmadi',info)
        self.assertIn('Ism: ⚠️ Aniqlanmadi',info)
        self.assertNotIn('Ali',info)
        self.assertIn(code,info)
        album=ctx.bot.send_media_group.await_args_list[0].kwargs['media'][0]
        self.assertIn(code,album.caption)
        self.assertNotIn('Ali',album.caption)
        self.assertEqual(next(iter(ctx.bot_data['applications'].values()))['code'],code)

    def test_certificate_series_spacing_does_not_create_false_conflict(self):
        a=normalize({'vehicle_registration':'ААF 0012345'})
        b=normalize({'vehicle_registration':'AAF-0012345'})
        self.assertEqual(a['vehicle_registration'],b['vehicle_registration'])
        self.assertEqual(a['vehicle_registration'],'AAF0012345')

    def test_button_premium_icon_is_opt_in(self):
        with patch.dict(os.environ,{'PREMIUM_ICON_READY':'123456789'}):
            b=button('Tayyor',action='op:ready:9',style='success',icon='READY')
        self.assertEqual(b.api_kwargs['style'],'success')
        self.assertEqual(b.api_kwargs['icon_custom_emoji_id'],'123456789')
        with patch.dict(os.environ,{'PREMIUM_ICON_READY':'not-an-id'}):
            self.assertNotIn('icon_custom_emoji_id',button('Tayyor',action='x',icon='READY').api_kwargs)

    async def test_sticker_error_does_not_block_flow(self):
        msg=N(reply_sticker=AsyncMock(side_effect=RuntimeError()))
        with patch.dict(os.environ,{'SUCCESS_STICKER_ID':'test-file-id'}):
            await send_brand_sticker(msg,'success')
        msg.reply_sticker.assert_awaited_once()

    def test_generate_uniform_cards_without_personal_data(self):
        from PIL import Image
        from bot.template_cards import create_instruction_cards, CARDS
        directory=create_instruction_cards()
        self.assertEqual(len(list(directory.glob('*.png'))),11)
        for name in CARDS:
            with Image.open(directory/f'{name}.png') as image:
                self.assertEqual(image.size,(1280,800))

    async def test_rejected_premium_icon_falls_back_to_working_controls(self):
        from telegram.error import BadRequest
        from telegram import InlineKeyboardMarkup
        from bot.presentation import with_keyboard_fallback
        call=AsyncMock(side_effect=[BadRequest('custom emoji unavailable'),N(message_id=1)])
        with patch.dict(os.environ,{'PREMIUM_ICON_READY':'12345'}):
            markup=InlineKeyboardMarkup([[button('Tayyor',action='op:ready:9',icon='READY')]])
        await with_keyboard_fallback(call,chat_id=-1001,text='card',reply_markup=markup)
        self.assertEqual(call.await_count,2)
        fallback=call.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(fallback.callback_data,'op:ready:9')
        self.assertFalse(fallback.api_kwargs)
