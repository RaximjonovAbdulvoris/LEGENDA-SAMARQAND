import asyncio
from io import BytesIO
import os
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from PIL import Image
from bot import fast_ocr as fast
from bot import document_ocr as ocr


def sample_words(lines):
    result = []
    for index, text in enumerate(lines):
        x = 30
        for token in text.split():
            result.append(fast.Word(token, 96, x, 40 + index * 60, len(token) * 12, 25))
            x += len(token) * 12 + 15
    return result


class FastOCRTests(unittest.IsolatedAsyncioTestCase):
    def test_numbered_licence_fields_and_address_are_distinct(self):
        values = fast.parse_fields(sample_words([
            '3. TEST CITY 01.02.1990', '4a. 01.01.2020 4b. 01.02.2030',
            '4d. 12345678901234', '5. AB1234567', '8. OTHER ADDRESS']), 'license_front', 1600, 900)
        self.assertEqual(values['birth_place'], 'TEST CITY')
        self.assertEqual(values['birth_date'], '01.02.1990')
        self.assertEqual(values['pinfl'], '12345678901234')
        self.assertEqual(values['license_number'], 'AB1234567')
        self.assertEqual(values['license_expiry'], '01.02.2030')

    def test_invalid_dates_are_not_filled(self):
        values = fast.parse_fields(sample_words(['3. TEST CITY 31.02.1990', '4a. 01.01.2020']),
            'license_front', 1600, 900)
        self.assertIsNone(values['birth_date'])
        self.assertIsNone(values['license_expiry'])

    def test_uncertain_digits_never_repaired(self):
        words = sample_words(['4d. 12345678901234'])
        words[-1].confidence = 40
        values = fast.parse_fields(words, 'license_front', 1600, 900)
        self.assertIsNone(values['pinfl'])
        values = fast.parse_fields(sample_words(['4d. 123456789O1234']), 'license_front', 1600, 900)
        self.assertIsNone(values['pinfl'])

    def test_conflicting_values_stay_empty(self):
        values = fast.parse_fields(sample_words(['4d. 12345678901234', '4d. 12345678901235']),
            'license_front', 1600, 900)
        self.assertIsNone(values['pinfl'])

    def test_certificate_requires_position_and_is_not_plate_or_vin(self):
        for text, left, top, expected in [('ABC0012345', 800, 100, 'ABC0012345'),
                ('ABC0012345', 20, 100, None), ('ABC0012345', 800, 800, None),
                ('01A123BC', 800, 100, None), ('VIN123456789012', 800, 100, None)]:
            values = fast.parse_fields([fast.Word(text, 96, left, top, 150, 25)], 'tech_back', 1200, 900)
            self.assertEqual(values['vehicle_registration'], expected)

    def test_engine_number_label_is_not_certificate(self):
        words = [fast.Word('14.', 96, 650, 100, 50, 25),
                 fast.Word('AB1234567', 96, 800, 100, 160, 25)]
        self.assertIsNone(fast.parse_fields(words, 'tech_back', 1200, 900)['vehicle_registration'])

    def test_image_processing_stays_in_memory(self):
        stream = BytesIO()
        Image.new('RGB', (400, 700), 'white').save(stream, 'PNG')
        png, width, height = fast.image_view(stream.getvalue(), 90)
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertGreater(width, height)
        self.assertLessEqual(width, 2600)

    async def test_timed_out_child_is_killed(self):
        process = N(returncode=None, kill=lambda: setattr(process, 'returncode', -9),
                    wait=AsyncMock(), communicate=AsyncMock(side_effect=asyncio.TimeoutError))
        with patch.object(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.TimeoutError):
                await fast.recognize('/usr/bin/tesseract', 'eng', b'synthetic-image')
        self.assertEqual(process.returncode, -9)
        process.wait.assert_awaited_once()

    async def test_only_selected_documents_are_processed_and_one_failure_is_partial(self):
        with patch.object(fast, 'engine_info', AsyncMock(return_value=('tesseract', 'eng'))), \
             patch.object(fast, 'extract_one', AsyncMock(side_effect=[RuntimeError(),
                 {'pinfl': '12345678901234', 'birth_date': '01.02.1990'}])) as extract:
            result = await fast.extract_documents(N(), {'passport_front': 'a', 'license_front': 'b', 'selfie': 'private'})
        self.assertEqual(extract.await_count, 2)
        self.assertEqual(result['backend'], 'fast')
        self.assertEqual(result['failed_sources'], ['passport_front'])
        self.assertEqual(result['values']['pinfl'], '12345678901234')

    async def test_fast_mode_never_contacts_ollama(self):
        with patch.dict(os.environ, {'DOCUMENT_OCR_BACKEND': 'fast', 'DOCUMENT_AI_ENABLED': '1'}), \
             patch.object(fast, 'extract_documents', AsyncMock(return_value={'backend': 'fast'})) as extract, \
             patch.object(ocr, 'local_client') as client:
            self.assertEqual((await ocr.extract_documents(N(), {}))['backend'], 'fast')
        extract.assert_awaited_once()
        client.assert_not_called()

    async def test_global_budget_cancels_remaining_images_and_keeps_completed_fields(self):
        original_wait = asyncio.wait
        async def short_wait(tasks, timeout):
            return await original_wait(tasks, timeout=.01)
        async def extract(bot, source, *args):
            if source == 'passport_front': return {'pinfl': '12345678901234'}
            await asyncio.sleep(10)
        with patch.object(fast, 'engine_info', AsyncMock(return_value=('tesseract', 'eng'))), \
             patch.object(fast, 'extract_one', side_effect=extract), \
             patch.object(asyncio, 'wait', side_effect=short_wait):
            result = await fast.extract_documents(N(), {'passport_front': 'a', 'license_front': 'b'})
        self.assertEqual(result['values']['pinfl'], '12345678901234')
        self.assertEqual(result['failed_sources'], ['license_front'])

    def test_subprocess_does_not_inherit_bot_credentials(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test-only', 'BRAND_SHEETS_SECRET': 'test-only'}):
            environment = fast.environment()
        self.assertNotIn('TELEGRAM_BOT_TOKEN', environment)
        self.assertNotIn('BRAND_SHEETS_SECRET', environment)
        self.assertEqual(environment['OMP_THREAD_LIMIT'], '1')


class RotationTimeoutRegression(unittest.IsolatedAsyncioTestCase):
    async def test_later_timeout_preserves_first_rotation_fields(self):
        file = N(file_size=10, download_as_bytearray=AsyncMock(return_value=b"image"))
        bot = N(get_file=AsyncMock(return_value=file))
        words = sample_words(["4d. 12345678901234"])
        with patch.object(fast, 'image_view', return_value=(b"png", 1600, 900)), \
             patch.object(fast, 'recognize', AsyncMock(side_effect=[words, asyncio.TimeoutError(), asyncio.TimeoutError()])):
            result = await fast.extract_one(bot, "license_front", "file", "tesseract", "eng")
        self.assertEqual(result["pinfl"], "12345678901234")
        self.assertIsNone(result["license_number"])
