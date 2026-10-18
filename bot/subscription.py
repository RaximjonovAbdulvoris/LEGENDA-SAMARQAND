"""Compatibility hooks: this deployment has no subscription gate."""
from telegram import InlineKeyboardMarkup


def subscription_keyboard(callback_data, required_chats=()):
    return InlineKeyboardMarkup([])


async def require_subscription(update, context, **kwargs):
    return True
