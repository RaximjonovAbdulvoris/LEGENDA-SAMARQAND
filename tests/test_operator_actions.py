import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from telegram.error import BadRequest, Forbidden
from test_operator_safety import FakeBot, FakeMessage, _update
from bot.handlers import operator

class OperatorActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("bot.queue_moves.destination", side_effect=lambda key: "-2001" if key.endswith("INCOMPLETE_GROUP") else "-2002")
        patcher.start()
        self.addCleanup(patcher.stop)

    def setup_case(self):
        bot = FakeBot()
        ctx = N(bot=bot, bot_data={})
        key = operator.register_application(ctx, applicant_id=9, region='tashkent', kind='driver',
                    group_chat_id=-1001, photo_msg_ids=[10,11,12], kb_msg_id=20)
        msg = FakeMessage(20, -1001)
        return bot, ctx, key, msg

    def test_layout_has_five_actions_without_comment(self):
        buttons = [b for row in operator.build_operator_keyboard(9).inline_keyboard for b in row]
        self.assertEqual({b.callback_data for b in buttons},
                         {f'op:{a}:9' for a in ('ready','progress','incomplete','otherpark','delete','download')})

    async def test_download_callback_uses_only_the_resolved_application(self):
        bot,ctx,key,msg=self.setup_case()
        with patch('bot.application_download.send_archive', new_callable=AsyncMock) as send:
            await operator.on_operator_button(_update('op:download:9',msg),ctx)
        send.assert_awaited_once_with(bot, ctx.bot_data['applications'][key], -1001, 20)

    async def test_zip_deleted_after_successful_move_to_review_or_archive(self):
        with patch('bot.application_download.send_archive', new_callable=AsyncMock, return_value=N(message_id=88)):
            bot, ctx, key, msg = self.setup_case()
            await operator.on_operator_button(_update('op:download:9', msg), ctx)
            self.assertEqual(ctx.bot_data['applications'][key]['zip_message_ids'], [88])
            await operator.on_operator_button(_update('op:incomplete:9', msg), ctx)
            self.assertIn({'chat_id': -1001, 'message_id': 88}, bot.deletes)

            bot, ctx, key, msg = self.setup_case()
            await operator.on_operator_button(_update('op:download:9', msg), ctx)
            with patch('bot.handlers.operator.archive_group', return_value='-3001'):
                await operator.on_operator_button(_update('op:ready:9', msg), ctx)
            self.assertIn({'chat_id': -1001, 'message_id': 88}, bot.deletes)

    async def test_failed_move_preserves_zip(self):
        bot, ctx, key, msg = self.setup_case()
        ctx.bot_data['applications'][key]['zip_message_ids'] = [88]
        with patch('bot.handlers.operator._archive_application', new_callable=AsyncMock, return_value=False):
            await operator.on_operator_button(_update('op:otherpark:9', msg), ctx)
        self.assertIn(key, ctx.bot_data['applications'])
        self.assertNotIn(88, [entry['message_id'] for entry in bot.deletes])

    async def test_notice_follows_move_and_old_buttons_cannot_duplicate_it(self):
        for action in ('incomplete','otherpark'):
            bot,ctx,key,msg=self.setup_case()
            query=_update(f'op:{action}:9',msg)
            await operator.on_operator_button(query,ctx)
            await operator.on_operator_button(query,ctx)
            self.assertEqual(len(bot.sent),1)
            self.assertEqual(bot.sent[0]['chat_id'],9)
            self.assertIn('+998 78 150-50-50',bot.sent[0]['text'])
            self.assertIn('Toshkent shahri',bot.sent[0]['text'])
            self.assertEqual(len(bot.deletes),4)
            self.assertNotIn(key,ctx.bot_data['applications'])
            self.assertEqual(next(iter(ctx.bot_data['applications'].values()))['queue_kind'],action)

    async def test_failed_notice_retains_application_without_success_marker(self):
        bot,ctx,key,msg=self.setup_case()
        bot.send_message=AsyncMock(side_effect=Forbidden('blocked'))
        await operator.on_operator_button(_update('op:incomplete:9',msg),ctx)
        self.assertNotIn(key,ctx.bot_data['applications'])
        child=next(iter(ctx.bot_data['applications'].values()))
        self.assertFalse(child['sent_notices'])
        self.assertEqual(child['notification_status'],'failed')

    async def test_delete_removes_only_this_application_without_notifying_or_archiving(self):
        bot,ctx,key,msg=self.setup_case()
        other=operator.register_application(ctx,applicant_id=9,region='andijon',kind='driver',
                   group_chat_id=-1002,photo_msg_ids=[40],kb_msg_id=50)
        await operator.on_operator_button(_update('op:delete:9',msg),ctx)
        self.assertEqual(bot.deletes,[{'chat_id':-1001,'message_id':i} for i in (10,11,12,20)])
        self.assertNotIn(key,ctx.bot_data['applications'])
        self.assertIn(other,ctx.bot_data['applications'])
        self.assertEqual(bot.sent,[])
        self.assertEqual(bot.copies,[])

    async def test_partial_delete_keeps_button_and_retries_only_remaining(self):
        bot,ctx,key,msg=self.setup_case()
        bot.delete_message=AsyncMock(side_effect=[True,Forbidden('no permission')])
        await operator.on_operator_button(_update('op:delete:9',msg),ctx)
        self.assertIn(key,ctx.bot_data['applications'])
        self.assertEqual(ctx.bot_data['applications'][key]['deleted_source_ids'],[10])
        bot.delete_message=AsyncMock(return_value=True)
        await operator.on_operator_button(_update('op:delete:9',msg),ctx)
        self.assertEqual([c.kwargs['message_id'] for c in bot.delete_message.await_args_list],[11,12,20])
        self.assertNotIn(key,ctx.bot_data['applications'])

    async def test_already_missing_photo_does_not_block_deletion(self):
        bot,ctx,key,msg=self.setup_case()
        bot.delete_message=AsyncMock(side_effect=[BadRequest('Message to delete not found'),True,True,True])
        await operator.on_operator_button(_update('op:delete:9',msg),ctx)
        self.assertNotIn(key,ctx.bot_data['applications'])

    async def test_wrong_message_or_non_admin_cannot_use_new_actions(self):
        for action in ('incomplete','otherpark','delete'):
            for wrong_message in (True,False):
                bot,ctx,key,msg=self.setup_case()
                if wrong_message: msg.message_id=21
                else: bot.get_chat_member=AsyncMock(return_value=N(status='member'))
                await operator.on_operator_button(_update(f'op:{action}:9',msg),ctx)
                self.assertEqual(bot.sent,[])
                self.assertEqual(bot.deletes,[])

    async def test_startup_refresh_deduplicates_aliases_and_preserves_progress(self):
        bot,ctx,key,msg=self.setup_case()
        ctx.bot_data['progress_state']={key:True}
        bot.edit_message_reply_markup=AsyncMock()
        await operator.refresh_driver_keyboards(ctx)
        bot.edit_message_reply_markup.assert_awaited_once()
        keyboard=bot.edit_message_reply_markup.await_args.kwargs['reply_markup']
        self.assertTrue(any('🟡' in b.text for row in keyboard.inline_keyboard for b in row))
