"""Render the selected Telegram custom emoji in outgoing bot text and buttons.

All substitutions are made just before sending. A rejected custom emoji is
retried with the original payload so applications cannot be blocked by styling.
"""
import html
import re

from telegram import InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import ExtBot

from bot.presentation import premium_id, _PACK_ICONS

_TAGS = re.compile(r'(<[^>]+>)')
_MARKDOWN = re.compile(r'\*([^*\n]+)\*|_([^_\n]+)_|`([^`\n]+)`')
_CUSTOM_EMOJI = re.compile(r'<tg-emoji\s+emoji-id="[0-9]+">(.*?)</tg-emoji>', re.S)
_TEXT_ENDPOINTS = {'sendMessage', 'editMessageText', 'sendPhoto',
                   'editMessageCaption', 'sendVideo', 'sendDocument', 'sendAnimation',
                   'editMessageReplyMarkup', 'sendMediaGroup'}


def _emoji_end(text, start):
    char = text[start]
    code = ord(char)
    if char in '0123456789#*' and start + 1 < len(text):
        pos = start + 1 + (text[start + 1] == '\ufe0f')
        if pos < len(text) and text[pos] == '\u20e3':
            return pos + 1
    if not (0x2600 <= code <= 0x27BF or 0x1F000 <= code <= 0x1FAFF
            or char in '©®™'):
        return start
    end = start + 1
    while end < len(text) and text[end] in ('\ufe0f', '\ufe0e', '\U0001f3fb',
                                             '\U0001f3fc', '\U0001f3fd',
                                             '\U0001f3fe', '\U0001f3ff'):
        end += 1
    while end + 1 < len(text) and text[end] == '\u200d':
        joined = _emoji_end(text, end + 1)
        if joined == end + 1:
            break
        end = joined
    return end


def _pieces(value):
    index = 0
    while index < len(value):
        end = _emoji_end(value, index)
        if end > index:
            yield value[index:end], True
            index = end
        else:
            yield value[index], False
            index += 1


def _markdown_html(text):
    """Translate the project's legacy bold/italic/code Markdown subset."""
    output, cursor = [], 0
    for match in _MARKDOWN.finditer(text):
        output.append(html.escape(text[cursor:match.start()]))
        tag = 'b' if match.group(1) else 'i' if match.group(2) else 'code'
        output.append(f'<{tag}>{html.escape(match.group(1) or match.group(2) or match.group(3))}</{tag}>')
        cursor = match.end()
    output.append(html.escape(text[cursor:]))
    return ''.join(output)


def premium_html(text, parse_mode=None):
    if parse_mode == 'HTML':
        source = text
    elif parse_mode in ('Markdown', 'MarkdownV2'):
        if parse_mode == 'MarkdownV2':
            return None  # Keep syntax we cannot safely translate.
        source = _markdown_html(text)
    elif parse_mode is None:
        source = html.escape(text)
    else:
        return None
    changed = False
    output = []
    in_code = False
    in_custom_emoji = False
    for chunk in _TAGS.split(source):
        if chunk.startswith('<') and chunk.endswith('>'):
            if chunk.lower().startswith(('<code', '<pre')):
                in_code = True
            if chunk.lower().startswith(('</code', '</pre')):
                in_code = False
            if chunk.lower().startswith('<tg-emoji '):
                in_custom_emoji = True
            if chunk.lower().startswith('</tg-emoji'):
                in_custom_emoji = False
            output.append(chunk)
            continue
        if in_code or in_custom_emoji:
            output.append(chunk)
            continue
        for part, is_emoji in _pieces(chunk):
            if not is_emoji:
                output.append(part)
                continue
            changed = True
            if '\u20e3' in part:
                output.append(part[0] + '.')
                continue
            found = premium_id(part)
            if found and found.isdecimal():
                output.append(f'<tg-emoji emoji-id="{found}">{part}</tg-emoji>')
            else:
                output.append(part)
            # Keep the meaning if Telegram has no usable premium icon at all.
    return ''.join(output) if changed else None


def _button_icon(label):
    for index, part in enumerate(label):
        end = _emoji_end(label, index)
        if end > index:
            emoji = label[index:end]
            return premium_id(emoji) or _PACK_ICONS.get(
                'READY' if emoji == '✅' else
                'PROGRESS' if emoji in ('🟡', '🔴', '⏳', '🔄') else
                'INCOMPLETE' if emoji in ('📄', '📷', '📝', '🪪') else
                'OTHERPARK' if emoji in ('🚕', '🚗', '🚘') else
                'DELETE' if emoji in ('🗑', '❌') else
                'LOCATION' if emoji in ('📍', '🗺') else ''
            )
    return None


def _button_text(label):
    result = ''.join(part if not emoji else part[0] + '.' if '\u20e3' in part else ''
                     for part, emoji in _pieces(label))
    return re.sub(r'\s{2,}', ' ', result).strip()


def premium_keyboard(markup):
    if not hasattr(markup, 'to_dict'):
        return None
    data = markup.to_dict()
    rows = data.get('keyboard') or data.get('inline_keyboard')
    if rows is None:
        return None
    changed = False
    for row in rows:
        for button in row:
            if not isinstance(button, dict) or not button.get('text'):
                continue
            label = button['text']
            cleaned = _button_text(label)
            icon = _button_icon(label)
            if cleaned != label and cleaned and (
                    (icon and icon.isdecimal()) or button.get('icon_custom_emoji_id')):
                button['text'] = cleaned
                if icon and icon.isdecimal() and not button.get('icon_custom_emoji_id'):
                    button['icon_custom_emoji_id'] = icon
                changed = True
    return data if changed else None


def plain_keyboard(markup):
    """Preserve button actions while removing optional visual Bot API fields."""
    data = markup.to_dict() if hasattr(markup, 'to_dict') else markup
    if not isinstance(data, dict):
        return None
    rows = data.get('keyboard') or data.get('inline_keyboard')
    if rows is None:
        return None
    cleaned = {key: value for key, value in data.items() if key not in ('style', 'icon_custom_emoji_id')}
    changed = False
    result_rows = []
    for row in rows:
        result_row = []
        for button in row:
            if not isinstance(button, dict):
                return None
            changed |= 'style' in button or 'icon_custom_emoji_id' in button
            result_row.append({key: value for key, value in button.items()
                               if key not in ('style', 'icon_custom_emoji_id')})
        result_rows.append(result_row)
    cleaned['keyboard' if 'keyboard' in data else 'inline_keyboard'] = result_rows
    return cleaned if changed else None


class PremiumEmojiBot(ExtBot):
    async def _post(self, endpoint, data=None, **kwargs):
        original = data
        if data and endpoint in _TEXT_ENDPOINTS:
            changed = dict(data)
            dirty = False
            key = 'text' if endpoint in ('sendMessage', 'editMessageText') else 'caption'
            if isinstance(changed.get(key), str) and not changed.get(
                    'entities' if key == 'text' else 'caption_entities'):
                rendered = premium_html(changed[key], changed.get('parse_mode'))
                if rendered is not None:
                    changed[key] = rendered
                    changed['parse_mode'] = 'HTML'
                    dirty = True
            if endpoint == 'sendMediaGroup' and isinstance(changed.get('media'), (list, tuple)):
                photos = []
                for photo in changed['media']:
                    if isinstance(photo, InputMediaPhoto) and photo.caption and not photo.caption_entities:
                        caption = premium_html(photo.caption, photo.parse_mode)
                        if caption is not None:
                            photo = InputMediaPhoto(
                                media=photo.media, caption=caption, parse_mode='HTML',
                                has_spoiler=photo.has_spoiler,
                                show_caption_above_media=photo.show_caption_above_media,
                                api_kwargs=photo.api_kwargs,
                            )
                            dirty = True
                    photos.append(photo)
                changed['media'] = photos
            markup = premium_keyboard(changed.get('reply_markup'))
            if markup is not None:
                changed['reply_markup'] = markup
                dirty = True
            if dirty:
                try:
                    return await super()._post(endpoint, changed, **kwargs)
                except BadRequest:
                    pass
        try:
            return await super()._post(endpoint, original, **kwargs)
        except BadRequest:
            fallback = plain_keyboard(original.get('reply_markup')) if original else None
            safe = dict(original)
            if fallback is not None:
                safe['reply_markup'] = fallback
            for field in ('text', 'caption'):
                if isinstance(safe.get(field), str):
                    safe[field] = _CUSTOM_EMOJI.sub(lambda match: match.group(1), safe[field])
            if fallback is None and safe == original:
                raise
            return await super()._post(endpoint, safe, **kwargs)
