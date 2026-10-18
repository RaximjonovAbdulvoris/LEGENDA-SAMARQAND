"""Move driver applications without deleting an unconfirmed destination copy."""
from copy import deepcopy
from html import escape
import logging
import re

from bot.route_settings import destination

logger = logging.getLogger(__name__)


async def checkpoint(context):
    application = getattr(context, 'application', None)
    if application is None or application.persistence is None:
        return True
    try:
        await application.update_persistence()
        await application.persistence.flush()
        return True
    except Exception:
        logger.error('Application state could not be saved; source cleanup is blocked.')
        return False


def destination_photo_ids(record, state):
    from bot.handlers.operator import _album_chunks
    albums = record.get('media_albums')
    if albums is None:
        ids = state.get('destination_photo_ids', [])
        return list(ids) if len(ids) == len(record.get('photo_msg_ids', [])) else []
    result = []
    for i, album in enumerate(albums):
        for j, chunk in enumerate(_album_chunks(album['photos'])):
            checkpoint = state.get('albums', {}).get(f'{i}:{j}', {})
            ids = checkpoint.get('message_ids', [])
            if len(ids) != len(chunk):
                return []
            result.extend(ids)
    return result


async def finish_source_cleanup(context, destination_record):
    from bot.handlers.operator import _record_for_key, _delete_operator_application
    source_key = destination_record.get('transfer_source_key')
    if source_key is None:
        return True
    if not await checkpoint(context):
        return False
    source = _record_for_key(context, source_key)
    if source is not None and not await _delete_operator_application(context, source_key, source):
        return False
    destination_record.pop('transfer_source_key', None)
    await checkpoint(context)
    await notify_applicant(context, destination_record)
    return True


async def notify_applicant(context, record):
    from bot.handlers.operator import INCOMPLETE_TEXT, OTHER_PARK_TEXT, _branch
    action = record['queue_kind']
    notices = record.setdefault('sent_notices', {})
    if notices.get(action):
        return
    text = INCOMPLETE_TEXT if action == 'incomplete' else OTHER_PARK_TEXT
    try:
        await context.bot.send_message(
            chat_id=record['applicant_id'],
            text=text + f"\n\n📍 {escape(_branch(record))}\nAriza: <code>{escape(record.get('code') or 'Eski ariza')}</code>",
            parse_mode='HTML',
        )
        notices[action] = True
        record['notification_status'] = 'sent'
    except Exception:
        record['notification_status'] = 'failed'
        try:
            await context.bot.send_message(
                chat_id=record['group_chat_id'],
                text=f"⚠️ {record.get('code') or 'Ariza'} guruhga ko‘chirildi, ammo arizachiga xabar yuborilgani tasdiqlanmadi.",
                reply_to_message_id=record['kb_msg_id'],
            )
        except Exception:
            logger.warning('Moved application notice could not be delivered.')
    await checkpoint(context)


async def move_to_review(context, key, record, action):
    from bot.handlers import operator
    from bot.application_ids import application_code
    label = 'Chala rasm' if action == 'incomplete' else 'Boshqa park'
    existing = record.get('_transfer')
    if existing:
        child = operator._record_for_key(context, existing['destination_key'])
        if not child:
            return False, 'Ko‘chirilgan ariza yozuvi topilmadi. Asl ariza o‘chirilmadi.'
        success = await finish_source_cleanup(context, child)
        return success, 'Asl xabarlarni o‘chirish tugamadi. Bot huquqlarini tekshirib, yana bosing.'

    route_key = 'INCOMPLETE_GROUP' if action == 'incomplete' else 'OTHER_PARK_GROUP'
    # Once separate region routes are enabled, a missing city destination
    # must not silently send a driver's documents to the shared queue.
    if record.get('region') not in ('andijon', 'tashkent'):
        return False, 'Ariza hududi aniqlanmadi; ko‘chirish bekor qilindi.'
    regional_queue = destination(f"{record.get('region', '').upper()}_{route_key}")
    saved_queue = record.get('queue_destinations', {}).get(action)
    if saved_queue and str(saved_queue) != str(regional_queue):
        return False, 'Eski ariza yo‘nalishi yangi hudud guruhiga mos emas. Administrator tekshirsin.'
    queue = regional_queue
    if not re.fullmatch(r'-[1-9][0-9]*', str(queue)):
        return False, f'«{label}» guruhi sozlanmagan. {route_key} ga guruh ID sini kiriting.'
    if str(queue) == str(record['group_chat_id']):
        return False, 'Qabul qiluvchi guruh operator guruhidan alohida bo‘lishi kerak.'

    queue = record.setdefault('queue_destinations', {}).setdefault(action, str(queue))
    state = record.setdefault('queue_states', {}).setdefault(action, {'photos': {}, 'kb': False})
    markup = operator.build_operator_keyboard(record['applicant_id'], review=True)
    shadow = dict(record, archive_state=state, _copy_markup=markup)
    if not await operator._archive_application(context.bot, shadow, queue):
        return False, f'«{label}» guruhiga barcha rasmlar to‘liq yuborilgani tasdiqlanmadi. Asl ariza saqlandi.'

    ids = destination_photo_ids(record, state)
    control_id = state.get('destination_kb_id')
    if (not ids or any(not isinstance(mid, int) or mid <= 0 for mid in ids)
            or len(ids) != len(set(ids)) or not isinstance(control_id, int) or control_id <= 0
            or control_id in ids):
        return False, 'Yangi guruh xabar IDlari to‘liq tasdiqlanmadi. Asl ariza o‘chirilmadi; administrator tekshirishi kerak.'

    albums = deepcopy(record.get('media_albums'))
    if albums is not None:
        index = 0
        for album in albums:
            size = len(album['photos'])
            album['source_message_ids'] = ids[index:index + size]
            index += size
    code = record.get('code') or application_code(record)
    child_key = operator.register_application(
        context, applicant_id=record['applicant_id'], region=record['region'], kind='driver',
        group_chat_id=int(queue), photo_msg_ids=ids, kb_msg_id=control_id,
        applicant_name=record.get('applicant_name', ''), username=record.get('username', ''),
        media_albums=albums, code=code,
    )
    child = operator._record_for_key(context, child_key)
    for field in ('ocr_status', 'ocr_prefix', 'ocr_suffix', 'ocr_card_data', 'ocr_input', 'ocr_result',
                  'ocr_attempts', 'ocr_retry_at', 'ocr_delivery_pending', 'ocr_delivery_attempts'):
        if field in record:
            child[field] = deepcopy(record[field])
    child['queue_kind'] = action
    child['sent_notices'] = dict(record.get('sent_notices', {}))
    child['transfer_source_key'] = key
    record['_transfer'] = {'action': action, 'destination_key': child_key}
    success = await finish_source_cleanup(context, child)
    return success, 'Ariza yangi guruhda saqlandi, lekin asl xabarlarni o‘chirib bo‘lmadi. Bot huquqlarini tekshirib yana bosing.'
