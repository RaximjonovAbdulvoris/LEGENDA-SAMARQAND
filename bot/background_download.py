"""Bounded ZIP jobs with durable, recoverable Telegram delivery state."""
import asyncio
import logging
import re
from weakref import WeakKeyDictionary

from telegram.error import BadRequest, Forbidden
from telegram.ext import ApplicationHandlerStop, CommandHandler, filters

logger = logging.getLogger(__name__)
_JOBS = WeakKeyDictionary()
_SLOTS = WeakKeyDictionary()
UNRESOLVED = ('sending', 'unknown')


def _key(context, key):
    return (id(getattr(context, 'application', context)), *key)


def active(context, key):
    task = _JOBS.get(asyncio.get_running_loop(), {}).get(_key(context, key))
    return task is not None and not task.done()


def schedule(context, key, record):
    jobs = _JOBS.setdefault(asyncio.get_running_loop(), {})
    identity = _key(context, key)
    if active(context, key):
        return False
    # Queue at most a few jobs; no documents are downloaded while waiting.
    if sum(not task.done() for task in jobs.values()) >= 8:
        return False
    application = getattr(context, 'application', None)
    coroutine = _run(context, key, record)
    task = (application.create_task(coroutine, name='application-zip')
            if application is not None else asyncio.create_task(coroutine))
    jobs[identity] = task
    def done(completed):
        if jobs.get(identity) is completed:
            jobs.pop(identity, None)
        if not completed.cancelled() and completed.exception():
            logger.warning('ZIP job failed (%s)', type(completed.exception()).__name__)
    task.add_done_callback(done)
    return True


async def _notice(context, record, text):
    try:
        await context.bot.send_message(chat_id=record['group_chat_id'], text=text,
                                       reply_to_message_id=record['kb_msg_id'])
    except Exception:
        logger.warning('ZIP status notice could not be delivered')


async def _run(context, key, record):
    from bot.application_download import prepare_archive
    from bot.background_ocr import control_lock
    from bot.handlers.operator import _record_for_key
    from bot.queue_moves import checkpoint
    slots = _SLOTS.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(2))
    async with slots, control_lock(*key):
        # A queued task must never send a ZIP for an application already moved.
        if _record_for_key(context, key) is not record or record.get('zip_delivery_state') in UNRESOLVED:
            return
        phase = 'preparing'
        try:
            document, code = await asyncio.wait_for(prepare_archive(context.bot, record), timeout=90)
            record['zip_delivery_state'] = 'sending'
            if not await checkpoint(context):
                record.pop('zip_delivery_state', None)
                await _notice(context, record, 'ZIP yuborilmadi: holatni saqlab bo‘lmadi. Keyinroq qayta bosing.')
                return
            phase = 'sending'
            sent = await context.bot.send_document(chat_id=record['group_chat_id'], document=document,
                reply_to_message_id=record['kb_msg_id'], caption=f'{code} · Hujjatlar ZIP arxivi')
            if not isinstance(getattr(sent, 'message_id', None), int):
                raise RuntimeError('Unconfirmed ZIP response')
            record.setdefault('zip_message_ids', []).append(sent.message_id)
            record.pop('zip_delivery_state', None)
            phase = 'saved'
            await checkpoint(context)
        except asyncio.CancelledError:
            # Persisted "sending" remains unresolved after an interrupted send.
            raise
        except Exception as error:
            if phase == 'sending' and not isinstance(error, (BadRequest, Forbidden)):
                record['zip_delivery_state'] = 'unknown'
                await checkpoint(context)
                await _notice(context, record, recovery_text())
            else:
                record.pop('zip_delivery_state', None)
                await checkpoint(context)
                message = str(error) if isinstance(error, ValueError) else 'ZIPni tayyorlab yoki yuborib bo‘lmadi. Keyinroq qayta bosing.'
                await _notice(context, record, message)
            logger.warning('ZIP job %s failed (%s)', phase, type(error).__name__)


def recovery_text():
    return ('ZIP yetib borgani tasdiqlanmadi; qayta yuborish to‘xtatildi. '
            'Fayl kelgan bo‘lsa, shu ZIPga Reply qilib /zip_boglash yozing. '
            'Fayl kelmaganini tekshirgan bo‘lsangiz, ariza xabariga Reply qilib /zip_qayta yozing.')


async def recover_zip(update, context):
    """Only a group admin can reconcile an ambiguous ZIP delivery."""
    from bot.handlers.operator import _record_for_key
    from bot.background_ocr import control_lock
    from bot.queue_moves import checkpoint
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or getattr(message, 'sender_chat', None):
        return
    target = message.reply_to_message
    if target is None:
        await message.reply_text('ZIP faylga yoki ariza xabariga Reply qilib buyruq yozing.')
        return
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except Exception:
        await message.reply_text('Administrator huquqini tekshirib bo‘lmadi.')
        return
    if member.status not in ('administrator', 'creator'):
        await message.reply_text('Bu amal faqat guruh administratorlari uchun.')
        return
    command = (message.text or '').split()[0].split('@')[0]
    if command == '/zip_boglash':
        document = getattr(target, 'document', None)
        match = re.fullmatch(r'(LG-[0-9]{6,})_hujjatlar\.zip', getattr(document, 'file_name', '') or '')
        if not match or getattr(getattr(target, 'from_user', None), 'id', None) != context.bot.id:
            await message.reply_text('Shu bot yuborgan ariza ZIP fayliga Reply qiling.')
            return
        matches = [(key, value) for key, value in context.bot_data.get('applications', {}).items()
                   if value.get('group_chat_id') == chat.id and value.get('code') == match[1]]
        if len(matches) != 1:
            await message.reply_text('ZIPning arizasi bu guruhda topilmadi.')
            return
        key, record = matches[0]
    else:
        key = (chat.id, target.message_id)
        record = _record_for_key(context, key)
        if record is None:
            await message.reply_text('Arizaning operator tugmalari bor xabariga Reply qiling.')
            return
    if active(context, key):
        await message.reply_text('ZIP tayyorlanmoqda; tugashini kuting.')
        return
    async with control_lock(*key):
        if _record_for_key(context, key) is not record:
            return
        if command == '/zip_boglash':
            ids = record.setdefault('zip_message_ids', [])
            if target.message_id not in ids:
                ids.append(target.message_id)
        elif record.get('zip_delivery_state') not in UNRESOLVED:
            await message.reply_text('Bu arizada aniqlanmagan ZIP yuborilishi yo‘q.')
            return
        record.pop('zip_delivery_state', None)
        saved = await checkpoint(context)
    await message.reply_text('ZIP holati saqlandi. Operator amallari ochildi.' if saved
                             else 'Holatni saqlab bo‘lmadi; buyruqni qayta bajaring.')


def register_handlers(application):
    async def command(update, context):
        try:
            await recover_zip(update, context)
        finally:
            raise ApplicationHandlerStop
    application.add_handler(CommandHandler(['zip_boglash', 'zip_qayta'], command,
                                           filters=filters.ChatType.GROUPS))
