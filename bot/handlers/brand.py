import logging
from html import escape as h

from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.start import (
    MENU_BRAND,
    cancel,
    main_keyboard,
    require_region,
)
from bot.regions import (
    application_group,
    clear_application,
    get_region,
    region_name,
)

logger = logging.getLogger(__name__)

BRAND_WARN, BRAND_NAME, BRAND_PHONE, BRAND_MODEL, BRAND_YEAR, BRAND_COLOR, BRAND_PLATE = range(100, 107)
CONTINUE_BTN = "✅ Davom etish"
CONTINUE_KB = ReplyKeyboardMarkup([[CONTINUE_BTN]], resize_keyboard=True, one_time_keyboard=True)

WARN_TEXT = (
    "⚠️ *DIQQAT! BRENDLASH SHARTLARI:*\n\n"
    "❌ SPARK — brendlanmaydi\n"
    "❌ NEXIA 3 — brendlanmaydi\n"
    "❌ Yili 2015 va undan past mashinalar — brendlanmaydi\n\n"
    "✅ Boshqa mashinalar (yili 2016 va undan yuqori) — brendlanadi\n\n"
    "_SPARK va NEXIA 3 yili nechi bo‘lishidan qat’i nazar BREND qilinmaydi._\n"
    "_2016 dan past mashinalar ham brend qilinmaydi._\n\n"
    "Davom etish uchun pastdagi knopkani bosing."
)


def _regional_keyboard(context):
    return main_keyboard(get_region(context))


async def _require_region(update, context, allowed_regions=None) -> bool:
    return await require_region(update, context, allowed_regions)


def _kind(context) -> str:
    return "brand"


def _kind_title(kind: str) -> str:
    return "Brend"


def _branch_title(context) -> str:
    return f"{region_name(get_region(context))} — {_kind_title(_kind(context))}"


async def _send_name_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"📍 *{_branch_title(context)} arizasi*\n\n"
        "Iltimos, *ism va familiyangizni* yozing:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )


async def _destination_error(update, context, kind: str) -> None:
    region = get_region(context)
    label = _kind_title(kind)
    await update.effective_message.reply_text(
        f"⚠️ {region_name(region)} uchun {label} arizasi hozircha sozlanmagan.\n"
        "Iltimos, birozdan keyin qayta urinib ko‘ring.",
        reply_markup=_regional_keyboard(context),
    )


async def _start_application(
    update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str
) -> int:
    if not await _require_region(update, context):
        return ConversationHandler.END
    region = get_region(context)
    # Preserve the selected branch, but never carry partial fields into a
    # newly selected application.
    clear_application(context)
    context.user_data["_application_kind"] = kind
    if not application_group(region, kind):
        await _destination_error(update, context, kind)
        clear_application(context)
        return ConversationHandler.END
    return await begin_form(update, context)


async def start_brand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _start_application(update, context, "brand")


async def begin_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kind = _kind(context)
    # Validate the selected city and destination before collecting data.
    if not await _require_region(update, context):
        return ConversationHandler.END
    region = get_region(context)
    if not application_group(region, kind):
        if update.callback_query:
            await update.callback_query.answer()
        await _destination_error(update, context, kind)
        clear_application(context)
        return ConversationHandler.END
    clear_application(context)
    context.user_data["_application_kind"] = kind
    context.user_data["_application_region"] = region
    await update.effective_message.reply_text(
        f"📍 *{region_name(region)} — Brend arizasi*\n\n"
        f"{WARN_TEXT}",
        parse_mode="Markdown",
        reply_markup=CONTINUE_KB,
    )
    return BRAND_WARN


async def brand_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _send_name_prompt(update, context)
    return BRAND_NAME


async def brand_warn_wrong(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        f"❗ Iltimos, *{CONTINUE_BTN}* knopkasini bosing:",
        parse_mode="Markdown", reply_markup=CONTINUE_KB,
    )
    return BRAND_WARN


async def brand_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if len(name) < 3:
        await update.message.reply_text("❗ Iltimos, to‘liq ism familiyangizni yozing:")
        return BRAND_NAME
    context.user_data["b_name"] = name
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📞 Raqamni jo‘natish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await update.message.reply_text(
        f"📍 *{_branch_title(context)}*\n"
        "📞 Telefon raqamingizni jo‘nating:",
        parse_mode="Markdown", reply_markup=keyboard,
    )
    return BRAND_PHONE


async def brand_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.contact.phone_number if update.message.contact else (update.message.text or "").strip()
    if len(phone) < 7 or not any(char.isdigit() for char in phone):
        await update.message.reply_text("❗ To‘g‘ri telefon raqamini kiriting (masalan: +998901234567):")
        return BRAND_PHONE
    context.user_data["b_phone"] = phone
    await update.message.reply_text(
        f"📍 *{_branch_title(context)}*\n"
        "🚗 Mashinangizning *rusumini (modelini)* yozing:",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove(),
    )
    return BRAND_MODEL


async def brand_get_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    model = (update.message.text or "").strip()
    if len(model) < 2:
        await update.message.reply_text("❗ Iltimos, mashina rusumini yozing:")
        return BRAND_MODEL
    context.user_data["b_model"] = model
    await update.message.reply_text(
        f"📍 *{_branch_title(context)}*\n"
        "📅 Mashinangizning *yilini* yozing:",
        parse_mode="Markdown",
    )
    return BRAND_YEAR


async def brand_get_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    year = (update.message.text or "").strip()
    if not year.isdigit() or not 1990 <= int(year) <= 2030:
        await update.message.reply_text("❗ To‘g‘ri yil kiriting (masalan: 2018):")
        return BRAND_YEAR
    context.user_data["b_year"] = year
    await update.message.reply_text(
        f"📍 *{_branch_title(context)}*\n"
        "🎨 Mashinangizning *rangini* yozing:",
        parse_mode="Markdown",
    )
    return BRAND_COLOR


async def brand_get_color(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    color = (update.message.text or "").strip()
    if len(color) < 2:
        await update.message.reply_text("❗ Iltimos, mashina rangini yozing:")
        return BRAND_COLOR
    context.user_data["b_color"] = color
    await update.message.reply_text(
        f"📍 *{_branch_title(context)}*\n"
        "🔢 Mashinangizning *davlat raqamini* yozing:",
        parse_mode="Markdown",
    )
    return BRAND_PLATE


async def brand_get_plate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    plate = (update.message.text or "").strip().upper()
    if len(plate) < 5 or not any(char.isalnum() for char in plate):
        await update.message.reply_text("❗ To‘g‘ri davlat raqamini kiriting (masalan: 01A123BC):")
        return BRAND_PLATE

    data = context.user_data
    data["b_plate"] = plate
    user = update.effective_user
    display = f"@{user.username}" if user.username else user.full_name
    user_link = f'<a href="tg://user?id={user.id}">{h(display)}</a>'
    kind = _kind(context)
    if not await _require_region(update, context):
        return ConversationHandler.END
    region = get_region(context)
    if data.get("_application_region") != region:
        clear_application(context)
        await update.message.reply_text(
            "⚠️ Hudud almashtirilgani uchun eski ariza bekor qilindi. "
            "Yangi hudud uchun arizani qayta boshlang.",
            reply_markup=_regional_keyboard(context),
        )
        return ConversationHandler.END
    # Recheck immediately before sending; an unset destination must never
    # silently route this application to Andijon.
    group_id = application_group(region, kind)
    if not group_id:
        await _destination_error(update, context, kind)
        clear_application(context)
        return ConversationHandler.END

    user = update.effective_user
    display = f"@{user.username}" if user.username else user.full_name
    user_link = f'<a href="tg://user?id={user.id}">{h(display)}</a>'
    branch_title = _kind_title(kind)
    branch_icon = "🎨"
    text = (
        f"{branch_icon} <b>{h(region_name(region))} — YANGI {h(branch_title.upper())} ARIZA</b>\n\n"
        f"👤 Foydalanuvchi: {user_link}\n"
        f"🪪 FIO: {h(data['b_name'])}\n"
        f"📞 Tel: {h(data['b_phone'])}\n"
        f"🚗 Model: {h(data['b_model'])}\n"
        f"📅 Yili: {h(data['b_year'])}\n"
        f"🎨 Rangi: {h(data['b_color'])}\n"
        f"🔢 Davlat raqami: {h(data['b_plate'])}"
    )
    try:
        await context.bot.send_message(
            chat_id=group_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        # Brand applications stay in their destination groups as plain messages.
    except Exception as error:
        logger.exception("brand: arizani guruhga yuborib bo‘lmadi: %s", error)
        clear_application(context)
        await update.message.reply_text(
            "⚠️ Arizani yuborishda texnik xatolik yuz berdi. "
            "Iltimos, birozdan keyin qayta urinib ko‘ring.",
            reply_markup=_regional_keyboard(context),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🎉 *Tabriklaymiz!*\n\n"
        f"{region_name(region)} — {_kind_title(kind)} arizangiz qabul qilindi. "
        "Tez orada operatorlarimiz bog‘lanadi.",
        parse_mode="Markdown", reply_markup=_regional_keyboard(context),
    )
    clear_application(context)
    return ConversationHandler.END


async def wrong_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❗ Iltimos, javobni matn ko‘rinishida yozing:")
    return context.user_data.get("_brand_state", BRAND_NAME)


def _text_state(handler, state, accepted=None):
    async def tracked(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["_brand_state"] = state
        return await handler(update, context)
    async def wrong(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["_brand_state"] = state
        return await wrong_text(update, context)
    return [
        MessageHandler(accepted if accepted is not None else filters.TEXT & ~filters.COMMAND, tracked),
        MessageHandler(~filters.COMMAND, wrong),
    ]


def build_brand_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.ChatType.PRIVATE & filters.Regex(f"^{MENU_BRAND}$"),
                start_brand,
            ),
        ],
        states={
            BRAND_WARN: [
                MessageHandler(filters.Regex(f"^{CONTINUE_BTN}$"), brand_warn),
                MessageHandler(~filters.COMMAND, brand_warn_wrong),
            ],
            BRAND_NAME: _text_state(brand_get_name, BRAND_NAME),
            BRAND_PHONE: _text_state(
                brand_get_phone, BRAND_PHONE,
                filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
            ),
            BRAND_MODEL: _text_state(brand_get_model, BRAND_MODEL),
            BRAND_YEAR: _text_state(brand_get_year, BRAND_YEAR),
            BRAND_COLOR: _text_state(brand_get_color, BRAND_COLOR),
            BRAND_PLATE: _text_state(brand_get_plate, BRAND_PLATE),
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel)],
        allow_reentry=True,
    )
