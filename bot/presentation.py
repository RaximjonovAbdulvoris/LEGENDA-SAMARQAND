"""Telegram-native visual styling; premium assets are opt-in and never required."""
import logging
import os
import re
from telegram import InlineKeyboardButton, KeyboardButton

logger = logging.getLogger(__name__)


def button(label, *, action=None, url=None, style='primary', icon=None):
    extra = {'style': style}
    emoji_id = contact_emoji_id() if icon == 'CONTACT' else (os.environ.get(f'PREMIUM_ICON_{icon or ""}', '') or _PACK_ICONS.get(icon, ''))
    if re.fullmatch(r'[0-9]+', emoji_id):
        extra['icon_custom_emoji_id'] = emoji_id
    return InlineKeyboardButton(label, callback_data=action, url=url, api_kwargs=extra)


def menu_button(label, style='primary', icon=None, emoji=None):
    extra = {'style': style}
    emoji_id = ((contact_emoji_id() if icon == 'CONTACT' else
                 os.environ.get(f'PREMIUM_ICON_{icon}', '') or _PACK_ICONS.get(icon, ''))
                if icon else '') or (premium_id(emoji) if emoji else '') or ''
    if re.fullmatch(r'[0-9]+', emoji_id):
        extra['icon_custom_emoji_id'] = emoji_id
    elif emoji:
        label = f'{emoji} {label}'
    return KeyboardButton(label, api_kwargs=extra)


def contact_button(label="Raqamni jo'natish"):
    chosen = contact_emoji_id()
    extra = {'icon_custom_emoji_id': chosen} if re.fullmatch(r'[0-9]+', chosen) else {}
    return KeyboardButton(label if extra else f'📞 {label}',
                          request_contact=True, api_kwargs=extra)


async def send_brand_sticker(message, event):
    sticker = os.environ.get(f'{event.upper()}_STICKER_ID', '').strip()
    if not sticker:
        return
    try:
        await message.reply_sticker(sticker=sticker)
    except Exception:
        # Branding must never block an application. Do not log asset credentials/URLs.
        logger.warning('Optional %s sticker could not be sent', event)


async def send_warning_voice(message):
    """Send the owner's recorded document instructions after the written warning."""
    voice_id = os.environ.get('DRIVER_WARNING_VOICE_FILE_ID', '').strip()
    if not voice_id:
        return
    try:
        await message.reply_voice(voice=voice_id)
    except Exception:
        # An optional recording must not prevent a driver from continuing.
        logger.warning('Optional driver warning voice could not be sent')


async def with_keyboard_fallback(call, **kwargs):
    """A rejected premium icon must not prevent sending operator controls."""
    from telegram import InlineKeyboardMarkup
    from telegram.error import BadRequest
    try:
        return await call(**kwargs)
    except BadRequest:
        markup = kwargs.get('reply_markup')
        if not isinstance(markup, InlineKeyboardMarkup) or not any(
            b.api_kwargs.get('icon_custom_emoji_id')
            for row in markup.inline_keyboard for b in row
        ):
            raise
        kwargs['reply_markup'] = InlineKeyboardMarkup([
            [InlineKeyboardButton(b.text, callback_data=b.callback_data, url=b.url)
             for b in row] for row in markup.inline_keyboard
        ])
        return await call(**kwargs)


PACK_NAMES = ('ApplicationEmoji', 'FinanceEmoji', 'TONEmoji', 'NewsEmoji',
              'IslomjonAnimeEmoji', 'statuswb', 'logowbtg')
_PACK_ICONS = {}
_PACK_EMOJIS = {}
_PACK_GLYPHS = {}
CONTACT_EMOJI_ID = '5319164022245832659'  # Owner-selected phone artwork.


def contact_emoji_id():
    # Ignore stale deployments' CONTACT overrides, which previously rendered
    # unrelated artwork in the user's contact menu.
    return CONTACT_EMOJI_ID


def premium_id(emoji):
    """A glyph may only be replaced with the same glyph, never a random logo."""
    # The owner specifically chose the ✔️ artwork from NewsEmoji for every
    # confirmation. Other packs' ✅ entries can render as a blue badge.
    if emoji.replace('\ufe0f', '') == '✅':
        return _PACK_ICONS.get('READY')
    # One supplied pack labels a coin as 📞. Select a real phone from the
    # phone action's sticker set instead of using that unrelated sticker.
    if emoji.replace('\ufe0f', '') == '📞':
        return contact_emoji_id()
    return _PACK_EMOJIS.get(emoji.replace('\ufe0f', ''))


def premium_markup(action, fallback):
    """Render one specifically chosen action icon in an HTML message."""
    emoji_id = contact_emoji_id() if action == 'CONTACT' else (os.environ.get(f'PREMIUM_ICON_{action}', '') or _PACK_ICONS.get(action, ''))
    if emoji_id and re.fullmatch(r'[0-9]+', emoji_id):
        glyph = _PACK_GLYPHS.get(action, fallback)
        return f'<tg-emoji emoji-id="{emoji_id}">{glyph}</tg-emoji>'
    return fallback


async def load_emoji_packs(application):
    """Resolve actual Telegram IDs from the user's custom emoji sets."""
    import asyncio
    cached = application.bot_data.setdefault('premium_pack_icons', {})
    _PACK_ICONS.clear()
    _PACK_EMOJIS.clear()
    _PACK_GLYPHS.clear()
    packs = {}
    async def load(name):
        try:
            result = await application.bot.get_sticker_set(name)
            packs[name] = [s for s in result.stickers if s.custom_emoji_id]
        except Exception:
            logger.warning('Optional emoji set %s unavailable; fallback icons retained.', name)
    try:
        await asyncio.wait_for(asyncio.gather(*(load(name) for name in PACK_NAMES)), timeout=25)
    except asyncio.TimeoutError:
        logger.warning('Emoji-set lookup timed out; available/cache icons retained.')
    preferred = {
        'READY': ('✔️', '✔'),
        'PROGRESS_ACTIVE': ('🟡',), 'PROGRESS_IDLE': ('🔴',),
        'INCOMPLETE': ('📷',), 'OTHERPARK': ('🚕', '🚗'),
        'DELETE': ('🗑',),
        'BRAND': ('🎨', '🚗'),
        'CONTACT': ('📱', '☎'), 'LOCATION': ('📍', '🗺', '🏢'),
        'APPLICATION': ('📝', '📄'), 'DOCUMENT': ('📄', '🪪'),
        'SPECTRE': ('⚡',), 'REGION': ('🔄', '🔁', '🗺'),
    }
    # A sticker's own Unicode equivalent is the only safe match for message
    # text. New packs take precedence over the old packs for identical glyphs.
    for sticker in (s for name in reversed(PACK_NAMES) for s in packs.get(name, [])):
        for emoji in (sticker.emoji or '').split():
            _PACK_EMOJIS.setdefault(emoji.replace('\ufe0f', ''), sticker.custom_emoji_id)
    application.bot_data['premium_pack_emojis'] = dict(_PACK_EMOJIS)
    # The user's WB logo pack is reserved for the Ulanish button. Every other
    # action must have a genuinely matching glyph; no first-sticker fallback.
    logo = packs.get('logowbtg', [])
    driver = next((s for glyph in ('🚕', '🚗', '🚘', '🚖') for s in logo
                   if (s.emoji or '').replace('\ufe0f', '') == glyph), None)
    if driver is None and logo:
        driver = logo[0]  # Only this pack is explicitly designated as a logo.
    if driver:
        _PACK_ICONS['DRIVER'] = driver.custom_emoji_id
        _PACK_GLYPHS['DRIVER'] = '🚕'
    by_pack = {
        'READY': ('NewsEmoji',),
        'PROGRESS_ACTIVE': ('statuswb', 'ApplicationEmoji'),
        'PROGRESS_IDLE': ('statuswb', 'ApplicationEmoji'),
        'INCOMPLETE': ('statuswb', 'IslomjonAnimeEmoji', 'ApplicationEmoji', 'NewsEmoji'),
        'OTHERPARK': ('statuswb', 'FinanceEmoji', 'IslomjonAnimeEmoji'),
        'DELETE': ('statuswb', 'ApplicationEmoji'),
        'BRAND': ('FinanceEmoji', 'IslomjonAnimeEmoji'),
        'CONTACT': ('IslomjonAnimeEmoji', 'NewsEmoji'),
        'APPLICATION': ('ApplicationEmoji', 'IslomjonAnimeEmoji', 'NewsEmoji'),
        'DOCUMENT': ('ApplicationEmoji', 'IslomjonAnimeEmoji', 'NewsEmoji'),
        'LOCATION': ('IslomjonAnimeEmoji', 'NewsEmoji', 'ApplicationEmoji'),
        'SPECTRE': ('FinanceEmoji', 'TONEmoji'),
        'REGION': ('IslomjonAnimeEmoji', 'ApplicationEmoji', 'NewsEmoji'),
    }
    for action, options in preferred.items():
        selected = next((s for emoji in options for name in by_pack[action]
                         for s in packs.get(name, [])
                         if (s.emoji or '').replace('\ufe0f', '') == emoji), None)
        if selected:
            _PACK_ICONS[action] = selected.custom_emoji_id
            _PACK_GLYPHS[action] = selected.emoji or options[0]
    # Contact uses the exact icon supplied by the owner, not an unrelated
    # sticker carrying phone Unicode metadata in one of the public packs.
    _PACK_ICONS['CONTACT'] = contact_emoji_id()
    _PACK_GLYPHS['CONTACT'] = '📞'
    # Drop old cached mappings such as LOCATION -> a WB logo.
    cached.clear()
    cached.update(_PACK_ICONS)
