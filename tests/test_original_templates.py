import tempfile
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, patch

from bot import config
from bot.handlers import driver


class OriginalTemplateTests(unittest.IsolatedAsyncioTestCase):
    def test_supplied_template_beats_generated_card(self):
        with tempfile.TemporaryDirectory() as root:
            old, new = Path(root) / 'old', Path(root) / 'generated'
            old.mkdir()
            new.mkdir()
            (old / 'license_front.jpg').write_bytes(b'old')
            (new / 'license_front.png').write_bytes(b'new')
            with patch.object(config, 'TEMPLATES_DIR', str(old)), \
                 patch('bot.template_cards.generated_directory', return_value=new):
                self.assertEqual(config.template_path('license_front'), str(old / 'license_front.jpg'))

    async def test_original_combined_car_template_is_sent_once(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / 'car_sides.jpg'
            original.write_bytes(b'original')
            msg = N(reply_photo=AsyncMock(), reply_media_group=AsyncMock(), reply_text=AsyncMock())
            with patch('bot.handlers.driver.template_path', return_value=str(original)):
                await driver._send_car_photo_prompt(N(message=msg), N(bot_data={}))
            msg.reply_photo.assert_awaited_once()
            msg.reply_media_group.assert_not_awaited()
