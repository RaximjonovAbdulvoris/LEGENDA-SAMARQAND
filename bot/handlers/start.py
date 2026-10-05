from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from bot.regions import (
    ANDIJON, TASHKENT, REGION_NAMES, clear_application, get_region, region_name,
)

MENU_DRIVER = "📝 Ulanish uchun Ariza"
MENU_BRAND = "🎨 Brend Ariza"
MENU_CONTACT = "📞 Bog'lanish uchun"
MENU_OFFICE = "📍 Ofis manzili"
MENU_REGION = "🔄 Hududni almashtirish"
OFFICE_PHOTO = Path(__file__).resolve().parents[1] / "templates" / "office.png"
TASHKENT_OFFICE_PHOTO = OFFICE_PHOTO.with_name("office_tashkent.png")
OFFICE_CAPTION = (
    "📍 <b>WB LEGENDA (ANDIJON OFISI)</b>\n\n"
    "Mo‘ljal: ZALATOY DOLINA MEXMONXONASI\n\n"
    "👇 Manzilni ko‘rish uchun «Xaritada ochish» tugmasini bosing."
)
OFFICE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📍 Xaritada ochish", url="https://maps.app.goo.gl/EnvW29BtaEwMbT5r8")],
])
TASHKENT_OFFICE_TEXT = (
    "📍 <b>WB LEGENDA (TOSHKENT OFISI)</b>\n\n"
    "Manzil: CHILONZOR 8-kvartal, 1-dom\n"
    "Mo‘ljal: QATORTOL BEKATI"
)
# No Toshkent map link was supplied; do not show the previous office's map.
TASHKENT_OFFICE_KEYBOARD = None

def main_keyboard(region: str) -> ReplyKeyboardMarkup:
    rows = [[MENU_DRIVER], [MENU_BRAND],
            [MENU_CONTACT], [MENU_OFFICE], [MENU_REGION]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# Compatibility for imports outside the regional flows.
MAIN_KEYBOARD = main_keyboard(ANDIJON)

SHARED_CONTACT_LINKS = (
    '💰 <b>PUL YECHISH BOTI:</b> <a href="https://t.me/legendapulbot">@legendapulbot</a>\n\n'
    '📣 <b>TELEGRAM KANAL:</b> <a href="https://t.me/WBLEGENDA_KANAL">@WBLEGENDA_KANAL</a>\n\n'
    '📸 <b>INSTAGRAM:</b> <a href="https://www.instagram.com/wb_legenda_taxi/">@WB_LEGENDA_TAXI</a>'
)
CONTACT_TEXT = (
    "<b>WB LEGENDA (ANDIJON) — bog‘lanish uchun</b>\n\n"
    "☎️ <b>ALOQA:</b> +998781505050\n"
    '📨 <b>TELEGRAM:</b> <a href="https://t.me/wblegendaandijonadmin">@wblegendaandijonadmin</a>\n'
    + SHARED_CONTACT_LINKS
)
TASHKENT_CONTACT_TEXT = (
    "<b>WB LEGENDA (TOSHKENT) — bog‘lanish uchun</b>\n\n"
    "☎️ <b>ALOQA:</b> +998781505050\n"
    "📱 <b>ALOQA:</b> +998931354484\n"
    '📨 <b>TELEGRAM:</b> <a href="https://t.me/WBLEGENDATAXI">@WBLEGENDATAXI</a>\n'
    + SHARED_CONTACT_LINKS
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != "private":
        return ConversationHandler.END
    context.user_data.clear()
    if update.effective_user:
        context.bot_data.get("pending_user_replies", {}).pop(update.effective_user.id, None)
    nonce = uuid4().hex[:10]
    context.user_data["region_choice_nonce"] = nonce
    await update.effective_message.reply_text(
        "• WB LEGENDA TAXI • xush kelibsiz\n\n"
        "“WB LEGENDA TAXI” ga ulanish va avtomobilni brendlash uchun shu botga ariza qoldiring!\n\n"
        "Avval ishlamoqchi bo’lgan shahringizni tanlang!",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.effective_message.reply_text(
        "Qaysi hududda ishlamoqchisiz?",
        reply_markup=_region_choices(nonce),
    )
    return ConversationHandler.END


def _region_choices(nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(name, callback_data=f"region:pick:{region}:{nonce}")]
        for region, name in REGION_NAMES.items()
    ])


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    region = get_region(context)
    if not region:
        return await start(update, context)
    details = (
        "📝 <b>Ulanish uchun ariza</b> — haydovchi sifatida ro‘yxatdan o‘tish.\n"
        "🎨 <b>Brend ariza</b> — avtomobilni brendlash uchun murojaat.\n"
        "📞 Aloqa ma’lumotlari va 📍 ofis manzili ham quyidagi menyuda."
    )
    await update.effective_message.reply_text(
        f"📍 <b>{region_name(region)}</b>\n\n"
        "Arizangiz tanlangan shahardagi operatorlarga yuboriladi.\n\n"
        f"{details}\n\nKerakli bo‘limni tanlang:",
        parse_mode="HTML", reply_markup=main_keyboard(region),
    )
    return ConversationHandler.END


async def on_region_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or update.effective_chat.type != "private":
        return ConversationHandler.END
    parts = (query.data or "").split(":")
    if (len(parts) != 4 or parts[2] not in REGION_NAMES
            or parts[3] != context.user_data.get("region_choice_nonce")):
        await query.answer("Bu tanlov eskirgan. Hududni almashtirish uchun /start bosing.", show_alert=True)
        return ConversationHandler.END
    _, action, region, nonce = parts
    if action == "pick":
        context.user_data["pending_region"] = region
        await query.answer()
        await query.edit_message_text(
            f"📍 <b>{region_name(region)}</b>\n\n"
            f"Siz <b>{region_name(region)}</b> uchun ariza yubormoqchisiz.\n"
            "Arizangiz faqat shu shahardagi operatorlarga boradi.\n\n"
            "Tanlovingizni tasdiqlaysizmi?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"region:confirm:{region}:{nonce}")],
                [InlineKeyboardButton("⬅️ Ortga", callback_data=f"region:back:{region}:{nonce}")],
            ]),
        )
    elif action == "back":
        context.user_data.pop("pending_region", None)
        await query.answer()
        await query.edit_message_text(
            "Qaysi hududda ishlamoqchisiz?", reply_markup=_region_choices(nonce),
        )
    elif action == "confirm" and context.user_data.get("pending_region") == region:
        context.user_data.clear()
        context.user_data["region"] = region
        await query.answer()
        await query.edit_message_text(f"✅ Tanlandi: {region_name(region)}")
        await show_menu(update, context)
    else:
        await query.answer("Avval hududni qayta tanlang.", show_alert=True)
    return ConversationHandler.END


def build_region_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(on_region_choice, pattern=r"^region:")


async def require_region(update: Update, context: ContextTypes.DEFAULT_TYPE, allowed_regions=None) -> bool:
    if not update.effective_chat or update.effective_chat.type != "private":
        return False
    region = get_region(context)
    if not region:
        if update.callback_query:
            await update.callback_query.answer()
        await start(update, context)
        return False
    if allowed_regions is not None and region not in allowed_regions:
        await update.effective_message.reply_text(
            "Bu bo‘lim tanlangan shaharda mavjud emas. Quyidagi menyudan tanlang.",
            reply_markup=main_keyboard(region),
        )
        return False
    return True
    return ConversationHandler.END


async def show_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_region(update, context):
        return ConversationHandler.END
    clear_application(context)
    region = get_region(context)
    text = TASHKENT_CONTACT_TEXT if region == TASHKENT else CONTACT_TEXT
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard(region),
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


def build_contact_handler() -> MessageHandler:
    return MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^(?:📞 )?Bog'lanish uchun$"), show_contact)


async def show_office(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_region(update, context):
        return ConversationHandler.END
    clear_application(context)
    if get_region(context) == TASHKENT:
        photo_path, caption, keyboard = (
            TASHKENT_OFFICE_PHOTO, TASHKENT_OFFICE_TEXT, TASHKENT_OFFICE_KEYBOARD,
        )
        cache_key = "tashkent_office_photo"
    else:
        await update.message.reply_text(
            OFFICE_CAPTION,
            parse_mode="HTML", reply_markup=OFFICE_KEYBOARD,
        )
        return ConversationHandler.END
    photo_hash = sha256(photo_path.read_bytes()).hexdigest()
    cached_photo = context.bot_data.get(f"{cache_key}_file_id")
    if cached_photo and context.bot_data.get(f"{cache_key}_hash") == photo_hash:
        await update.message.reply_photo(
            photo=cached_photo, caption=caption,
            parse_mode="HTML", reply_markup=keyboard,
        )
    else:
        with photo_path.open("rb") as photo:
            sent = await update.message.reply_photo(
                photo=photo, caption=caption,
                parse_mode="HTML", reply_markup=keyboard,
            )
        if sent.photo:
            context.bot_data[f"{cache_key}_file_id"] = sent.photo[-1].file_id
            context.bot_data[f"{cache_key}_hash"] = photo_hash
    return ConversationHandler.END


def build_office_handler() -> MessageHandler:
    return MessageHandler(filters.ChatType.PRIVATE & filters.Regex(f"^{MENU_OFFICE}$"), show_office)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_application(context)
    if update.effective_user:
        context.bot_data.get("pending_user_replies", {}).pop(update.effective_user.id, None)
    await update.effective_message.reply_text("Ariza to‘ldirish bekor qilindi.")
    return await show_menu(update, context)
