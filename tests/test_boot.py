"""Old operator posts must not delay Telegram polling at startup."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123:test')
os.environ.setdefault('DRIVER_GROUP_1', '-1001')
os.environ.setdefault('DRIVER_GROUP_2', '-1002')
os.environ.setdefault('BRAND_GROUP', '-1003')

from bot import main


class BootTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_refresh_is_scheduled_after_start(self):
        application = SimpleNamespace(job_queue=MagicMock())
        with patch('bot.template_cards.create_instruction_cards'), \
             patch('bot.presentation.load_emoji_packs', new_callable=AsyncMock), \
             patch.object(main, 'warmup_templates', new_callable=AsyncMock), \
             patch.object(main, 'remove_legacy_brand_keyboards', new_callable=AsyncMock) as brand, \
             patch.object(main, 'refresh_driver_keyboards', new_callable=AsyncMock) as driver:
            await main.post_init(application)
            brand.assert_not_awaited()
            driver.assert_not_awaited()
            application.job_queue.run_once.assert_called_once_with(main.refresh_operator_controls, when=2)
            await main.refresh_operator_controls(SimpleNamespace(application=application))
            brand.assert_awaited_once_with(application)
            driver.assert_not_awaited()
