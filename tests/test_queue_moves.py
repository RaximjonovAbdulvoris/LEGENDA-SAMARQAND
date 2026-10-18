from copy import deepcopy
import os
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, patch
from telegram.error import Forbidden

from test_archive_albums import Bot, Message, albums
from test_operator_safety import _update
from bot.handlers import operator
from bot import presentation


class QueueMoveTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_review_queue_uses_only_three_controls(self):
        for stage in ('incomplete', 'other'):
            record = {'queue_stage': stage}
            self.assertTrue(operator.is_review_record(record))
            buttons = [button.callback_data for row in
                       operator.build_operator_keyboard(9, review=operator.is_review_record(record)).inline_keyboard
                       for button in row]
            self.assertEqual(set(buttons), {'op:ready:9', 'op:delete:9', 'op:progress:9'})
        self.assertFalse(operator.is_review_record({'queue_stage': 'main'}))

    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env=patch.dict(os.environ,{'PERSIST_DIR':tmp.name})
        env.start(); self.addCleanup(env.stop)
        routing=patch('bot.queue_moves.destination', side_effect=lambda key:
            '-2001' if key.endswith('INCOMPLETE_GROUP') else '-2002')
        routing.start(); self.addCleanup(routing.stop)

    def case(self):
        bot=Bot(); ctx=N(bot=bot,bot_data={})
        media=albums()
        key=operator.register_application(ctx,applicant_id=9,region='andijon',kind='driver',
             group_chat_id=100,photo_msg_ids=[i for a in media for i in a['source_message_ids']],
             kb_msg_id=20,media_albums=media,code='LG-000023')
        return bot,ctx,key,Message(20)

    def query(self,action,msg,chat):
        return _update(f'op:{action}:9',msg,chat_id=chat)

    async def test_separate_mode_never_falls_back_to_shared_review_group(self):
        from bot.queue_moves import move_to_review
        bot, ctx, key, _ = self.case()
        record = ctx.bot_data['applications'][key]
        def only_shared(route):
            return '-2001' if route == 'INCOMPLETE_GROUP' else ''
        with patch.dict(os.environ, {'SEPARATE_REGION_GROUPS': '1'}), \
             patch('bot.queue_moves.destination', side_effect=only_shared):
            success, reason = await move_to_review(ctx, key, record, 'incomplete')
        self.assertFalse(success)
        self.assertIn('sozlanmagan', reason)
        self.assertFalse(bot.groups)
        record['queue_destinations'] = {'incomplete': '-2001'}
        with patch.dict(os.environ, {'SEPARATE_REGION_GROUPS': '1'}), \
             patch('bot.queue_moves.destination', side_effect=lambda route: '-3001' if route == 'ANDIJON_INCOMPLETE_GROUP' else ''):
            success, reason = await move_to_review(ctx, key, record, 'incomplete')
        self.assertFalse(success)
        self.assertIn('mos emas', reason)
        self.assertFalse(bot.groups)

    async def move(self,action='incomplete'):
        bot,ctx,key,msg=self.case()
        await operator.on_operator_button(self.query(action,msg,100),ctx)
        child_key=next(iter(ctx.bot_data['applications']))
        return bot,ctx,key,child_key,ctx.bot_data['applications'][child_key]

    async def test_both_review_groups_receive_complete_controllable_application(self):
        for action,chat in [('incomplete',-2001),('otherpark',-2002)]:
            bot,ctx,key,child_key,child=await self.move(action)
            self.assertNotIn(key,ctx.bot_data['applications'])
            self.assertEqual(child['group_chat_id'],chat)
            self.assertEqual(child['code'],'LG-000023')
            self.assertEqual(child['region'],'andijon')
            self.assertEqual(child['queue_kind'],action)
            self.assertEqual(len(child['photo_msg_ids']),12)
            self.assertEqual(len(set(child['photo_msg_ids'])),12)
            self.assertEqual([len(a['source_message_ids']) for a in child['media_albums']],[10,2])
            buttons=[b.callback_data for row in bot.copies[0]['reply_markup'].inline_keyboard for b in row]
            self.assertEqual(set(buttons),{'op:ready:9','op:delete:9','op:progress:9'})
            self.assertEqual(len(bot.deletes),13)
            self.assertEqual(len(bot.sent),1)
            self.assertEqual(bot.sent[0]['chat_id'],9)

    async def test_ready_in_review_archives_then_removes_review_application(self):
        for action in ('incomplete','otherpark'):
            bot,ctx,key,child_key,child=await self.move(action)
            bot.deletes.clear()
            q=self.query('ready',Message(child['kb_msg_id']),child['group_chat_id'])
            with patch('bot.handlers.operator.archive_group',return_value='-999'):
                await operator.on_operator_button(q,ctx)
            self.assertEqual([int(g['chat_id']) for g in bot.groups],[child['group_chat_id']]*2+[-999]*2)
            self.assertEqual({d['message_id'] for d in bot.deletes},set(child['photo_msg_ids']+[child['kb_msg_id']]))
            self.assertIn({'chat_id':child['group_chat_id'],'message_id':child['kb_msg_id']},bot.deletes)
            self.assertNotIn(child_key,ctx.bot_data['applications'])
            self.assertEqual(len([s for s in bot.sent if s['chat_id']==9]),2)

    async def test_delete_in_review_removes_all_destination_messages_without_archiving(self):
        for action in ('incomplete','otherpark'):
            bot,ctx,key,child_key,child=await self.move(action)
            bot.deletes.clear()
            await operator.on_operator_button(self.query('delete',Message(child['kb_msg_id']),child['group_chat_id']),ctx)
            self.assertEqual({d['message_id'] for d in bot.deletes},set(child['photo_msg_ids']+[child['kb_msg_id']]))
            self.assertTrue(all(d['chat_id']==child['group_chat_id'] for d in bot.deletes))
            self.assertEqual(len(bot.groups),2)
            self.assertNotIn(child_key,ctx.bot_data['applications'])

    async def test_progress_remains_reversible_with_only_three_buttons(self):
        bot,ctx,key,child_key,child=await self.move()
        q=self.query('progress',Message(child['kb_msg_id']),child['group_chat_id'])
        q.callback_query.edit_message_reply_markup=AsyncMock()
        await operator.on_operator_button(q,ctx)
        self.assertTrue(ctx.bot_data['progress_state'][child_key])
        await operator.on_operator_button(q,ctx)
        self.assertFalse(ctx.bot_data['progress_state'][child_key])
        self.assertEqual(sum(map(len,q.callback_query.edit_message_reply_markup.await_args.kwargs['reply_markup'].inline_keyboard)),3)

    async def test_source_cleanup_failure_resumes_without_duplicate_destination(self):
        bot,ctx,key,msg=self.case()
        bot.delete_message=AsyncMock(side_effect=Forbidden('no permission'))
        q=self.query('incomplete',msg,100)
        await operator.on_operator_button(q,ctx)
        self.assertIn(key,ctx.bot_data['applications'])
        self.assertEqual(len(bot.groups),2)
        self.assertEqual(bot.sent,[])
        child_key=ctx.bot_data['applications'][key]['_transfer']['destination_key']
        self.assertIn('transfer_source_key',ctx.bot_data['applications'][child_key])
        bot.delete_message=AsyncMock(return_value=True)
        await operator.on_operator_button(q,ctx)
        self.assertNotIn(key,ctx.bot_data['applications'])
        self.assertEqual(len(bot.groups),2)
        self.assertEqual(len(bot.copies),1)
        self.assertEqual(len(bot.sent),1)

    async def test_destination_action_can_finish_interrupted_source_cleanup(self):
        bot,ctx,key,msg=self.case()
        bot.delete_message=AsyncMock(side_effect=Forbidden('no permission'))
        await operator.on_operator_button(self.query('otherpark',msg,100),ctx)
        child_key=ctx.bot_data['applications'][key]['_transfer']['destination_key']
        child=ctx.bot_data['applications'][child_key]
        bot.delete_message=AsyncMock(return_value=True)
        await operator.on_operator_button(self.query('progress',Message(child['kb_msg_id']),child['group_chat_id']),ctx)
        self.assertNotIn(key,ctx.bot_data['applications'])
        self.assertNotIn('transfer_source_key',child)
        self.assertTrue(ctx.bot_data['progress_state'][child_key])

    async def test_persistence_failure_blocks_source_deletion(self):
        bot,ctx,key,msg=self.case()
        ctx.application=N(update_persistence=AsyncMock(),persistence=N(flush=AsyncMock(side_effect=OSError())))
        await operator.on_operator_button(self.query('incomplete',msg,100),ctx)
        self.assertIn(key,ctx.bot_data['applications'])
        self.assertEqual(len(ctx.bot_data['applications']),2)
        self.assertEqual(bot.deletes,[])
        self.assertEqual(bot.sent,[])

    async def test_malformed_destination_album_never_deletes_source(self):
        bot,ctx,key,msg=self.case(); bot.malformed_group=1
        await operator.on_operator_button(self.query('incomplete',msg,100),ctx)
        self.assertEqual(bot.deletes,[])
        self.assertIn(key,ctx.bot_data['applications'])
        await operator.on_operator_button(self.query('incomplete',msg,100),ctx)
        self.assertEqual(len(bot.groups),2)

    async def test_old_callback_cannot_archive_a_moved_application(self):
        bot,ctx,key,child_key,child=await self.move()
        before=len(bot.groups)
        await operator.on_operator_button(self.query('ready',Message(20),100),ctx)
        self.assertIn(child_key,ctx.bot_data['applications'])
        self.assertEqual(len(bot.groups),before)

    async def test_review_has_no_further_review_routes_even_for_forged_callback(self):
        bot,ctx,key,child_key,child=await self.move()
        before=len(bot.groups)
        await operator.on_operator_button(self.query('otherpark',Message(child['kb_msg_id']),child['group_chat_id']),ctx)
        self.assertEqual(len(bot.groups),before)
        self.assertIn(child_key,ctx.bot_data['applications'])

    async def test_restart_refresh_keeps_review_layout(self):
        bot,ctx,key,child_key,child=await self.move()
        ctx.bot_data=deepcopy(ctx.bot_data)
        bot.edit_message_reply_markup=AsyncMock()
        await operator.refresh_driver_keyboards(ctx)
        self.assertEqual(sum(map(len,bot.edit_message_reply_markup.await_args.kwargs['reply_markup'].inline_keyboard)),3)


class EmojiPackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        presentation._PACK_ICONS.clear()
        self.addCleanup(presentation._PACK_ICONS.clear)

    async def test_real_set_ids_are_used_and_cached(self):
        assets=[N(emoji='✅',custom_emoji_id='123'),N(emoji='🗑',custom_emoji_id='456')]
        app=N(bot=N(get_sticker_set=AsyncMock(return_value=N(stickers=assets))),bot_data={})
        await presentation.load_emoji_packs(app)
        self.assertEqual(app.bot.get_sticker_set.await_count,7)
        with patch.dict(os.environ,{'PREMIUM_ICON_READY':''}):
            self.assertNotIn('icon_custom_emoji_id', presentation.button('✅ Tayyor',action='x',icon='READY').api_kwargs)
        self.assertEqual(app.bot_data['premium_pack_icons']['DELETE'],'456')

    async def test_unavailable_sets_discard_unverified_cached_icons(self):
        app=N(bot=N(get_sticker_set=AsyncMock(side_effect=RuntimeError())),
              bot_data={'premium_pack_icons':{'READY':'123'}})
        await presentation.load_emoji_packs(app)
        self.assertNotIn('READY', presentation._PACK_ICONS)
