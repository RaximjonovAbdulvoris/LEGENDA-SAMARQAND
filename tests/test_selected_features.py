import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

from bot.handlers import brand, operator, start
from bot.regions import application_group
from bot.route_settings import regional_keys


class SelectedFeatures(unittest.IsolatedAsyncioTestCase):
    def test_old_menu_keeps_only_spectre_extension(self):
        labels = [row[0].text for row in start.main_keyboard('andijon').keyboard]
        self.assertIn(start.MENU_SPECTRE, labels)
        self.assertIn(start.MENU_BRAND, labels)
        self.assertNotIn('Chala rasm', ' '.join(labels))
        keyboard = operator.build_operator_keyboard(123)
        button_labels = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn('💬 Izoh berish', button_labels)
        self.assertIn('🚕 Boshqa park', button_labels)
        self.assertNotIn('📷 Chala rasm', button_labels)
        self.assertNotIn('📦 Hujjatlarni yuklab olish', button_labels)
        review = operator.build_operator_keyboard(123, review=True)
        self.assertNotIn('🚕 Boshqa park', [button.text for row in review.inline_keyboard for button in row])

    def test_spectre_uses_separate_region_routes(self):
        with patch('bot.regions.destination', side_effect=lambda key, fallback='': key):
            self.assertEqual(application_group('andijon', 'spectre'), 'ANDIJON_SPECTRE_GROUP')
            self.assertEqual(application_group('tashkent', 'spectre'), 'TASHKENT_SPECTRE_GROUP')
        self.assertIn('otherpark', regional_keys('ANDIJON'))
        self.assertNotIn('incomplete', regional_keys('ANDIJON'))

    def test_old_chala_setting_is_ignored_without_breaking_routes(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from bot.route_settings import read_settings
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'PERSIST_DIR': directory}):
            Path(directory, 'bot_routes.json').write_text(
                '{"ANDIJON_INCOMPLETE_GROUP":"-101", "ANDIJON_OTHER_PARK_GROUP":"-102"}')
            self.assertEqual(read_settings(), {'ANDIJON_OTHER_PARK_GROUP': '-102'})

    async def test_spectre_starts_without_brand_warning(self):
        update = N(effective_message=N(reply_text=AsyncMock()))
        context = N(user_data={'region': 'andijon'})
        with patch.object(brand, '_require_region', AsyncMock(return_value=True)), \
             patch.object(brand, 'application_group', return_value='-100123'):
            state = await brand.start_spectre(update, context)
        self.assertEqual(state, brand.BRAND_NAME)
        self.assertEqual(context.user_data['_application_kind'], 'spectre')
        self.assertEqual(update.effective_message.reply_text.await_count, 1)

    async def test_other_park_never_deletes_unconfirmed_copy(self):
        context = N(bot=AsyncMock(), bot_data={'applications': {}, 'app_messages': {}}, application=None)
        record = dict(applicant_id=123, region='andijon', kind='driver',
                      group_chat_id=-111, kb_msg_id=77, photo_msg_ids=[71, 72])
        with patch.object(operator, 'destination', return_value='-222'), \
             patch.object(operator, '_archive_application', AsyncMock(return_value=False)):
            moved = await operator._move_other_park(context, (-111, 77), record)
        self.assertFalse(moved)
        context.bot.delete_message.assert_not_awaited()

    async def test_other_park_moves_exact_record_after_confirmed_copy(self):
        context = N(bot=AsyncMock(), bot_data={'applications': {}, 'app_messages': {}}, application=None)
        key = (-111, 77)
        record = dict(applicant_id=123, region='andijon', kind='driver',
                      group_chat_id=-111, kb_msg_id=77, photo_msg_ids=[71, 72])
        context.bot_data['applications'][key] = record
        async def copied(bot, shadow, group):
            self.assertEqual(group, '-222')
            state = shadow['archive_state']
            state.update(photo_batch={'destination_message_ids': [81, 82]},
                         destination_kb_id=87)
            return True
        with patch.object(operator, 'destination', return_value='-222'), \
             patch.object(operator, '_archive_application', side_effect=copied):
            self.assertTrue(await operator._move_other_park(context, key, record))
        child = context.bot_data['applications'][(-222, 87)]
        self.assertEqual(child['photo_msg_ids'], [81, 82])
        self.assertEqual(child['queue_kind'], 'otherpark')
        self.assertNotIn(key, context.bot_data['applications'])
        self.assertEqual(context.bot.delete_message.await_count, 3)
        context.bot.send_message.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
