import asyncio
from io import BytesIO
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace as N
from zipfile import ZipFile

from bot.application_download import send_archive, folder_name
from bot.driver_card import details
from bot.neural_ocr_worker import licence_identity
from bot.fast_ocr import Word


class CardTests(unittest.TestCase):
    def test_folder_name_cannot_escape_zip(self):
        self.assertEqual(folder_name({'applicant_name': '../../Example/Person', 'code': 'LG-123456'}), 'ExamplePerson')

    def test_operator_fields_are_ordered_and_escaped(self):
        text = details({'values': {'surname': 'EXAMPLE', 'given_name': 'PERSON',
                    'birth_date': '01.02.1990', 'pinfl': '12345678901234',
                    'birth_place': 'TEST CITY', 'license_number': 'AB1234567',
                    'license_expiry': '01.02.2030', 'vehicle_registration': 'AC1234567'}},
                    '+998901234567', '01A123BC')
        labels = ('Familiya:', 'Ism:', 'Tug‘ilgan sana:', 'Telefon raqami:',
                  'PINFL:', 'Tug‘ilgan joy:', 'Prava raqami:', 'Amal qilish muddati:',
                  'Avtomobil raqami:', 'Texpasport seriya raqami:')
        self.assertEqual([text.index(label) for label in labels], sorted(text.index(label) for label in labels))
        self.assertEqual(text.count('Tug‘ilgan sana:'), 1)
        self.assertNotIn('EXAMPLE PERSON', text)
        self.assertIn('<code>998901234567</code>', details({'values': {}}, '+998 90 123 45 67'))

    def test_numbered_licence_fields_separate_identity(self):
        def word(text, x, y):
            return Word(text, 99, x, y, 80 if x == 320 else 20, 28)
        self.assertEqual(licence_identity([word('1', 290, 100), word('EXAMPLE', 320, 100),
            word('2', 290, 130), word('PERSON', 320, 130)]), ('EXAMPLE', 'PERSON'))


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_selected_application_photos_are_in_zip(self):
        bot = N(get_file=AsyncMock(side_effect=lambda fid: N(file_size=3,
            download_as_bytearray=AsyncMock(return_value=fid.encode()))),
            send_document=AsyncMock())
        record = {'code': 'LG-123456', 'ocr_result': {'values': {'surname': 'EXAMPLE', 'given_name': 'PERSON'}}, 'media_albums': [
            {'name': 'main', 'photos': [{'file_id': 'one'}, {'file_id': 'two'}]},
            {'name': 'selfie', 'photos': [{'file_id': 'three'}]}]}
        await send_archive(bot, record, -100, 42)
        self.assertEqual(bot.get_file.await_count, 3)
        document = bot.send_document.await_args.kwargs['document']
        with ZipFile(BytesIO(document.input_file_content)) as archive:
            self.assertEqual(archive.namelist(), ['EXAMPLE PERSON/main_01.jpg', 'EXAMPLE PERSON/main_02.jpg', 'EXAMPLE PERSON/selfie_01.jpg'])
            self.assertEqual(archive.read('EXAMPLE PERSON/main_02.jpg'), b'two')
        self.assertEqual(bot.send_document.await_args.kwargs['chat_id'], -100)

    async def test_missing_photo_prevents_partial_zip(self):
        bot = N(get_file=AsyncMock(), send_document=AsyncMock())
        with self.assertRaises(ValueError):
            await send_archive(bot, {'media_albums': [{'name': 'main', 'photos': [{}]}]}, -100, 42)
        bot.send_document.assert_not_awaited()
