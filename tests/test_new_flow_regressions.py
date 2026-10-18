"""Offline regression checks for the October review fixes."""
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from telegram.error import BadRequest, TimedOut
from test_regions import context, update
from test_operator_safety import FakeBot, FakeMessage, _update
from bot.handlers import brand, driver, operator
from bot.main import build_application_conversation
from bot.template_cards import CARDS


class ReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_passport_front_advances_directly_to_license(self):
        ctx = context('tashkent')
        msg = update()
        msg.message.photo = [N(file_id='passport-front')]
        with patch('bot.handlers.driver._send_prompt', AsyncMock()) as prompt:
            state = await driver.get_passport_front(msg, ctx)
        self.assertEqual(state, driver.LICENSE_FRONT)
        self.assertEqual(ctx.user_data['passport_front'], 'passport-front')
        self.assertNotIn('passport_back', ctx.user_data)
        self.assertEqual(prompt.await_args.args[3], 'license_front')

    def test_instruction_cards_have_eleven_consecutive_steps(self):
        self.assertNotIn('passport_back', CARDS)
        self.assertEqual([step for step, *_ in CARDS.values()],
                         [f'{number:02d}' for number in range(1, 12)])

    async def test_wrong_media_keeps_year_step(self):
        ctx = context('tashkent')
        handler = brand._text_state(brand.brand_get_model, brand.BRAND_MODEL)[0]
        self.assertEqual(await handler.callback(update('Cobalt'), ctx), brand.BRAND_YEAR)
        self.assertEqual(await brand.wrong_text(update(), ctx), brand.BRAND_YEAR)

    async def test_wrong_media_keeps_phone_step(self):
        ctx = context('tashkent')
        await brand.brand_get_name(update('Ali Valiyev'), ctx)
        self.assertEqual(await brand.wrong_text(update(), ctx), brand.BRAND_PHONE)

    async def test_member_cannot_mark_ready(self):
        bot = FakeBot()
        bot.get_chat_member = AsyncMock(return_value=N(status='member'))
        ctx = N(bot=bot, bot_data={})
        operator.register_application(ctx, applicant_id=123, region='tashkent', kind='driver',
                                      group_chat_id=-1001, photo_msg_ids=[10], kb_msg_id=20)
        msg = FakeMessage(20, -1001)
        await operator.on_operator_button(_update('op:ready:123', msg), ctx)
        self.assertEqual(bot.copies, [])
        self.assertEqual(bot.deletes, [])
        self.assertIn('administratorlari', msg.replies[-1])

    async def test_partial_send_retries_only_missing_album_in_same_group(self):
        ctx = context('tashkent')
        ctx.user_data.update(_application_region='tashkent', user_id=123, name='Ali',
                             passport_front='p1', passport_back='p2', selfie='s', litsenziya='l')
        ctx.bot.send_media_group.side_effect = [
            [N(message_id=10), N(message_id=11)], BadRequest('send blocked'),
            [N(message_id=12), N(message_id=13)],
        ]
        with patch('bot.handlers.driver.driver_groups', return_value=['-1001','-1002']):
            with self.assertRaises(BadRequest):
                await driver._send_to_driver_group(ctx)
            await driver._send_to_driver_group(ctx)
        calls = ctx.bot.send_media_group.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.kwargs['media'][0].media for c in calls], ['p1', 's', 's'])
        self.assertEqual(len({c.kwargs['chat_id'] for c in calls}), 1)
        self.assertEqual(len(ctx.bot_data['applications']), 1)

    async def test_failed_submission_keeps_form_and_fields(self):
        ctx = context('tashkent')
        ctx.user_data.update(_application_region='tashkent', name='Ali', passport_front='photo')
        with patch('bot.handlers.driver._send_to_driver_group', AsyncMock(side_effect=BadRequest('blocked'))):
            result = await driver.get_car_plate(update('01A123BC'), ctx)
        self.assertEqual(result, driver.CAR_PLATE)
        self.assertEqual(ctx.user_data['passport_front'], 'photo')

    async def test_timeout_does_not_blindly_resend_application(self):
        ctx = context('tashkent')
        ctx.user_data.update(_application_region='tashkent', name='Ali')
        sender = AsyncMock(side_effect=TimedOut())
        with patch('bot.handlers.driver._send_to_driver_group', sender):
            await driver.get_car_plate(update('01A123BC'), ctx)
            await driver.get_car_plate(update('01A123BC'), ctx)
        self.assertEqual(sender.await_count, 1)
        self.assertTrue(ctx.user_data['_delivery_uncertain'])

    def test_form_state_is_persistent(self):
        conv = build_application_conversation()
        self.assertTrue(conv.persistent)
        self.assertEqual(conv.name, 'application_form')
