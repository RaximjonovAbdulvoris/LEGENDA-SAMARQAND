"""Help the owner supply real sticker and premium emoji IDs without guessing."""
from html import escape


async def asset_id(update, context):
    message = update.effective_message
    target = message.reply_to_message
    if not target:
        await message.reply_text('Voice, sticker yoki premium emoji xabariga Reply qilib /asset_id yuboring.')
        return
    if target.voice:
        await message.reply_text(
            f'Voice ID:\n<code>{escape(target.voice.file_id)}</code>', parse_mode='HTML'
        )
        return
    if target.sticker:
        await message.reply_text(
            f'Sticker ID:\n<code>{escape(target.sticker.file_id)}</code>', parse_mode='HTML'
        )
        return
    ids = [e.custom_emoji_id for e in (target.entities or ()) if e.custom_emoji_id]
    if ids:
        await message.reply_text('Emoji ID:\n' + '\n'.join(f'<code>{escape(i)}</code>' for i in ids), parse_mode='HTML')
    else:
        await message.reply_text('Bu xabarda voice, sticker yoki premium emoji topilmadi.')
