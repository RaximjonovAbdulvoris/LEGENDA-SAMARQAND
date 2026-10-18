import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bot.application_ids import application_code
from scripts.reset_application_numbers import reset


class DriverOnlyNumbers(unittest.TestCase):
    def test_counter_reset_and_draft_retry(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PERSIST_DIR': tmp}):
            draft = {}
            self.assertEqual(application_code(draft), 'LG-000001')
            self.assertEqual(application_code(draft), 'LG-000001')
            self.assertEqual(application_code({}), 'LG-000002')
            backup = reset(tmp)
            self.assertTrue(backup.is_file())
            self.assertEqual(application_code({}), 'LG-000001')

    def test_brand_does_not_allocate_or_display_driver_numbers(self):
        from bot.handlers import brand
        source = Path(brand.__file__).read_text()
        self.assertNotIn('application_code', source)
        self.assertNotIn('Ariza kodi:', source)
