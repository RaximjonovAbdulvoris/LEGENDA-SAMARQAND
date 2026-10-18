"""Offline checks for premium rendering and a safe Telegram rejection path."""
import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as N

for key in ('TELEGRAM_BOT_TOKEN', 'DRIVER_GROUP_1', 'DRIVER_GROUP_2', 'BRAND_GROUP'):
    os.environ.setdefault(key, '123:test' if key == 'TELEGRAM_BOT_TOKEN' else '-1001')

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import ExtBot

from bot import presentation
from bot.premium_emoji import PremiumEmojiBot, premium_html, premium_keyboard, plain_keyboard


class PremiumEmojiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        presentation._PACK_EMOJIS.clear()
        presentation._PACK_EMOJIS.update({'✅': '12345', '📄': '23456'})
        self.addCleanup(presentation._PACK_EMOJIS.clear)
        presentation._PACK_ICONS.clear()
        self.addCleanup(presentation._PACK_ICONS.clear)
        presentation._PACK_GLYPHS.clear()
        self.addCleanup(presentation._PACK_GLYPHS.clear)

    def bot(self):
        with patch.dict(os.environ, {'ALL_PROXY': '', 'HTTPS_PROXY': '',
                                     'HTTP_PROXY': '', 'all_proxy': '', 'https_proxy': ''}):
            return PremiumEmojiBot('123:test')

    def test_html_keeps_markup_and_premium_emoji(self):
        result = premium_html('✅ <b>Tayyor</b> 📄 <code>📄</code>', 'HTML')
        self.assertIn('<tg-emoji emoji-id="23456">📄</tg-emoji>', result)
        self.assertNotIn('emoji-id="12345"', result)
        self.assertIn('✅', result)
        self.assertIn('<b>Tayyor</b>', result)
        self.assertIn('<code>📄</code>', result)

    def test_markdown_keeps_bold_and_unmapped_emoji_if_sets_fail(self):
        result = premium_html('✅ *Tayyor* 📄 📝', 'Markdown')
        self.assertIn('<b>Tayyor</b>', result)
        self.assertIn('📝', result)
        self.assertIn('emoji-id="23456"', result)

    def test_unrelated_logo_never_replaces_location_or_document_text(self):
        presentation._PACK_ICONS.update({'DRIVER': '34567', 'CONTACT': '45678'})
        self.assertNotIn('tg-emoji', premium_html('📍 Toshkent · 📝 Ulanish', 'HTML'))
        self.assertEqual(premium_html('1️⃣ Old • 2️⃣ Orqa'), '1. Old • 2. Orqa')
        from bot.handlers.start import MENU_DRIVER, main_keyboard
        keyboard = main_keyboard('tashkent')
        self.assertEqual(keyboard.keyboard[0][0].text, MENU_DRIVER)
        self.assertEqual(keyboard.keyboard[0][0].api_kwargs['icon_custom_emoji_id'], '34567')

    def test_missing_location_icon_remains_visible_in_keyboard(self):
        from bot.handlers.start import main_keyboard
        button = main_keyboard('tashkent').keyboard[4][0]
        self.assertEqual(button.text, '📍 Ofis manzili')
        self.assertNotIn('icon_custom_emoji_id', button.api_kwargs)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton('📍 Xaritada ochish', url='https://example.org')]])
        self.assertIsNone(premium_keyboard(markup))

    async def test_location_is_exact_and_driver_uses_only_logo_pack(self):
        packs = {
            'NewsEmoji': [N(emoji='🅱️', custom_emoji_id='111'),
                          N(emoji='📞', custom_emoji_id='666')],
            'IslomjonAnimeEmoji': [N(emoji='📍', custom_emoji_id='222'),
                                   N(emoji='📱', custom_emoji_id='777')],
            'logowbtg': [N(emoji='🚕', custom_emoji_id='333')],
            'FinanceEmoji': [N(emoji='🚗', custom_emoji_id='444'),
                             N(emoji='🔋', custom_emoji_id='888'),
                             N(emoji='⚡', custom_emoji_id='555')],
        }
        async def get_set(name):
            return N(stickers=packs.get(name, []))
        app = N(bot=N(get_sticker_set=get_set),
                bot_data={'premium_pack_icons': {'LOCATION': '111'}})
        await presentation.load_emoji_packs(app)
        self.assertEqual(presentation.premium_id('📍'), '222')
        self.assertEqual(presentation._PACK_ICONS['LOCATION'], '222')
        self.assertEqual(presentation._PACK_ICONS['DRIVER'], '333')
        self.assertIn('emoji-id="222"', premium_html('📍 Toshkent shahri', 'HTML'))
        self.assertEqual(presentation.premium_id('📝'), None)
        self.assertEqual(presentation.premium_id('📞'), presentation.CONTACT_EMOJI_ID)
        self.assertEqual(presentation._PACK_ICONS['CONTACT'], presentation.CONTACT_EMOJI_ID)
        self.assertEqual(presentation._PACK_ICONS['SPECTRE'], '555')
        from bot.handlers.start import main_keyboard
        menu = main_keyboard('tashkent').keyboard
        self.assertEqual(menu[1][0].api_kwargs['icon_custom_emoji_id'], '444')
        self.assertEqual(menu[4][0].api_kwargs['icon_custom_emoji_id'], '222')
        self.assertEqual(menu[3][0].api_kwargs['icon_custom_emoji_id'], presentation.CONTACT_EMOJI_ID)
        self.assertIn('emoji-id="333"', presentation.premium_markup('DRIVER', '🚕'))
        self.assertIn('emoji-id="444"', presentation.premium_markup('BRAND', '🎨'))
        self.assertEqual(premium_html(presentation.premium_markup('BRAND', '🎨') + ' 📍', 'HTML').count('<tg-emoji'), 2)

    async def test_actual_phone_asset_keeps_contact_request(self):
        async def get_set(name):
            return N(stickers=[N(emoji='📱', custom_emoji_id='777')]
                     if name == 'IslomjonAnimeEmoji' else [])
        await presentation.load_emoji_packs(N(bot=N(get_sticker_set=get_set), bot_data={}))
        self.assertEqual(presentation.premium_id('📞'), presentation.CONTACT_EMOJI_ID)
        contact = presentation.contact_button()
        self.assertTrue(contact.request_contact)
        self.assertEqual(contact.api_kwargs['icon_custom_emoji_id'], presentation.CONTACT_EMOJI_ID)

    async def test_coin_and_battery_cannot_replace_phone_and_energy(self):
        async def get_set(name):
            return N(stickers=[N(emoji='📞', custom_emoji_id='666'),
                               N(emoji='🔋', custom_emoji_id='888')]
                     if name == 'FinanceEmoji' else [])
        await presentation.load_emoji_packs(N(bot=N(get_sticker_set=get_set), bot_data={}))
        self.assertEqual(presentation._PACK_ICONS['CONTACT'], presentation.CONTACT_EMOJI_ID)
        self.assertNotIn('SPECTRE', presentation._PACK_ICONS)
        from bot.handlers.start import main_keyboard
        menu = main_keyboard('tashkent').keyboard
        self.assertTrue(any('Spectre' in b.text for row in menu for b in row))
        self.assertEqual(menu[3][0].api_kwargs['icon_custom_emoji_id'], presentation.CONTACT_EMOJI_ID)

    def test_owner_phone_icon_is_used_in_menu_and_contact_text(self):
        from bot.handlers.start import main_keyboard
        with patch.dict(os.environ, {'PREMIUM_ICON_CONTACT': '777'}):
            menu = main_keyboard('tashkent').keyboard
            self.assertEqual(presentation.premium_id('📞'), presentation.CONTACT_EMOJI_ID)
        self.assertEqual(menu[3][0].api_kwargs['icon_custom_emoji_id'], presentation.CONTACT_EMOJI_ID)
        markup = presentation.premium_markup('CONTACT', '📞')
        self.assertIn(f'emoji-id="{presentation.CONTACT_EMOJI_ID}"', markup)
        self.assertIn(f'emoji-id="{presentation.CONTACT_EMOJI_ID}"',
                      premium_html('📞 Aloqa', 'HTML'))

    async def test_operator_icons_follow_the_actual_status(self):
        assets = [N(emoji=glyph, custom_emoji_id=icon) for glyph, icon in
                  [('🟡', '111'), ('🔴', '222'), ('📷', '333'),
                   ('🚕', '444'), ('🗑', '555')]]
        async def get_set(name):
            return N(stickers=assets if name == 'statuswb' else [])
        await presentation.load_emoji_packs(N(bot=N(get_sticker_set=get_set), bot_data={}))
        from bot.handlers.operator import build_operator_keyboard
        inactive = build_operator_keyboard(42)
        active = build_operator_keyboard(42, in_progress=True)
        self.assertEqual(inactive.inline_keyboard[0][1].api_kwargs['icon_custom_emoji_id'], '222')
        self.assertEqual(active.inline_keyboard[0][1].api_kwargs['icon_custom_emoji_id'], '111')
        self.assertEqual(inactive.inline_keyboard[1][0].api_kwargs['icon_custom_emoji_id'], '333')
        self.assertEqual(inactive.inline_keyboard[1][1].api_kwargs['icon_custom_emoji_id'], '444')
        self.assertEqual(inactive.inline_keyboard[2][0].api_kwargs['icon_custom_emoji_id'], '555')
        self.assertIn('✅', inactive.inline_keyboard[0][0].text)

    def test_button_click_data_survives_premium_render(self):
        markup = InlineKeyboardMarkup([[InlineKeyboardButton('📄 Hujjatlar', callback_data='op:ready:5')]])
        changed = premium_keyboard(markup)
        self.assertEqual(changed['inline_keyboard'][0][0]['text'], 'Hujjatlar')
        self.assertEqual(changed['inline_keyboard'][0][0]['icon_custom_emoji_id'], '23456')
        self.assertEqual(changed['inline_keyboard'][0][0]['callback_data'], 'op:ready:5')

    def test_success_checks_stay_native_when_news_set_is_unavailable(self):
        self.assertIsNone(presentation.premium_id('✅'))
        self.assertEqual(premium_html('✅ *4 ta rasm qabul qilindi!*', 'Markdown'),
                         '✅ <b>4 ta rasm qabul qilindi!</b>')
        self.assertIsNone(premium_keyboard(InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Tayyor', callback_data='op:ready:5')]])))

    async def test_success_check_uses_only_the_selected_news_artwork(self):
        async def get_set(name):
            assets = ([N(emoji='✔️', custom_emoji_id='777')]
                      if name == 'NewsEmoji' else
                      [N(emoji='✅', custom_emoji_id='999')]
                      if name == 'statuswb' else [])
            return N(stickers=assets)
        await presentation.load_emoji_packs(N(bot=N(get_sticker_set=get_set), bot_data={}))
        self.assertEqual(presentation.premium_id('✅'), '777')
        self.assertIn('emoji-id="777"', premium_html('✅ 4 ta rasm qabul qilindi', 'HTML'))
        from bot.handlers.operator import build_operator_keyboard
        ready = build_operator_keyboard(42).inline_keyboard[0][0]
        self.assertEqual(ready.api_kwargs['icon_custom_emoji_id'], '777')
        self.assertNotEqual(ready.api_kwargs['icon_custom_emoji_id'], '999')

    async def test_rejected_icon_retries_original_message(self):
        markup = InlineKeyboardMarkup([[InlineKeyboardButton('📄 Hujjatlar', callback_data='x')]])
        sender = AsyncMock(side_effect=[BadRequest('custom emoji not allowed'), {'ok': True}])
        bot = self.bot()
        with patch.object(ExtBot, '_post', sender):
            await bot._post('sendMessage', {'chat_id': 5, 'text': '📄 *Hujjatlar*',
                                            'parse_mode': 'Markdown', 'reply_markup': markup})
        self.assertEqual(sender.await_count, 2)
        self.assertEqual(sender.await_args_list[0].args[1]['parse_mode'], 'HTML')
        self.assertEqual(sender.await_args_list[1].args[1]['parse_mode'], 'Markdown')
        self.assertIs(sender.await_args_list[1].args[1]['reply_markup'], markup)

    async def test_rejected_original_icon_retries_plain_contact_button(self):
        presentation._PACK_ICONS['CONTACT'] = '777'
        markup = presentation.contact_button()
        from telegram import ReplyKeyboardMarkup
        keyboard = ReplyKeyboardMarkup([[markup]], resize_keyboard=True)
        plain = plain_keyboard(keyboard)
        self.assertTrue(plain['keyboard'][0][0]['request_contact'])
        self.assertNotIn('icon_custom_emoji_id', plain['keyboard'][0][0])
        sender = AsyncMock(side_effect=[BadRequest('icon rejected'), {'ok': True}])
        with patch.object(ExtBot, '_post', sender):
            await self.bot()._post('sendMessage', {'chat_id': 5, 'text': 'Phone',
                                                   'reply_markup': keyboard})
        self.assertEqual(sender.await_count, 2)
        self.assertTrue(sender.await_args.args[1]['reply_markup']['keyboard'][0][0]['request_contact'])

    async def test_invalid_explicit_custom_emoji_falls_back_to_glyph(self):
        sender = AsyncMock(side_effect=[BadRequest('invalid custom emoji'), {'ok': True}])
        with patch.object(ExtBot, '_post', sender):
            await self.bot()._post('sendMessage', {'chat_id': 5,
                'text': '<tg-emoji emoji-id="123">📱</tg-emoji> Telefon', 'parse_mode': 'HTML'})
        self.assertEqual(sender.await_args.args[1]['text'], '📱 Telefon')

    async def test_album_captions_keep_photo_and_style(self):
        sender = AsyncMock(return_value=[])
        bot = self.bot()
        album = [InputMediaPhoto('file-id', caption='📄 *Hujjatlar*', parse_mode='Markdown')]
        with patch.object(ExtBot, '_post', sender):
            await bot._post('sendMediaGroup', {'chat_id': 5, 'media': album})
        media = sender.await_args.args[1]['media'][0]
        self.assertEqual(media.media, 'file-id')
        self.assertEqual(media.parse_mode, 'HTML')
        self.assertIn('<tg-emoji', media.caption)

    async def test_real_send_path_preserves_button_action(self):
        bot = self.bot()
        markup = InlineKeyboardMarkup([[InlineKeyboardButton('📄 Hujjatlar', callback_data='op:ready:5')]])
        response = {'message_id': 1, 'date': 0, 'chat': {'id': 5, 'type': 'private'}, 'text': 'Tayyor'}
        with patch.object(PremiumEmojiBot, '_do_post', AsyncMock(return_value=response)) as sent:
            await bot.send_message(5, '📄 *Hujjatlar*', parse_mode='Markdown', reply_markup=markup)
        payload = sent.await_args.kwargs['data']
        self.assertIn('<tg-emoji emoji-id="23456">📄</tg-emoji>', payload['text'])
        self.assertIn('<b>Hujjatlar</b>', payload['text'])
        self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'op:ready:5')
