import asyncio
import json
import os
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
import httpx
from bot import document_ocr as ocr


class DocumentOCRTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        backend = patch.dict(os.environ, {'DOCUMENT_OCR_BACKEND': 'ollama'})
        backend.start()
        self.addCleanup(backend.stop)

    @staticmethod
    def image_bytes():
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new('RGB', (300, 200), 'white').save(buf, format='JPEG')
        return buf.getvalue()

    @staticmethod
    def client(handler):
        return httpx.AsyncClient(base_url=ocr.LOCAL_ORIGIN,
            transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False)

    @staticmethod
    def response(values):
        return httpx.Response(200, json={'done': True, 'done_reason': 'stop',
            'message': {'content': json.dumps(values)}})

    def test_validates_digits_dates_and_unknown_values(self):
        values = ocr.normalize({'pinfl': '1234 5678901234', 'birth_date': '31.02.2000',
            'license_expiry': '01.12.2030', 'birth_place': None, 'license_number': 12345})
        self.assertEqual(values['pinfl'], '12345678901234')
        self.assertIsNone(values['birth_date'])
        self.assertIsNone(values['license_number'])
        self.assertEqual(values['license_expiry'], '01.12.2030')

    def test_html_escaping_and_copyable_values(self):
        text = ocr.format_summary({'status': 'partial', 'values': {'birth_place': 'A < B & C'}})
        self.assertIn('<code>A &lt; B &amp; C</code>', text)
        self.assertIn('Aniqlanmadi', text)
        self.assertIn('asl hujjat', text)

    async def test_disabled_sends_nothing_even_with_old_api_key(self):
        bot = N(get_file=AsyncMock())
        with patch.dict(os.environ, {'CODECRAFT_API_KEY': 'test-only', 'DOCUMENT_AI_ENABLED': '0'}), \
             patch.object(ocr, 'local_client') as factory:
            result = await ocr.extract_documents(bot, {'passport_front': 'file'})
        bot.get_file.assert_not_awaited()
        factory.assert_not_called()
        self.assertEqual(result['status'], 'unavailable')

    async def test_engine_failure_never_downloads_or_falls_back(self):
        bot = N(get_file=AsyncMock())
        client = self.client(lambda request: httpx.Response(503))
        with patch.object(ocr, 'local_client', return_value=client), \
             patch.dict(os.environ, {'DOCUMENT_AI_ENABLED': '1', 'CODECRAFT_API_KEY': 'test-only'}):
            result = await ocr.extract_documents(bot, {'passport_front': 'file'})
        bot.get_file.assert_not_awaited()
        self.assertEqual(result['status'], 'unavailable')

    async def test_model_must_be_installed_local_vision(self):
        for metadata in ({'capabilities': ['completion']},
                         {'capabilities': ['vision'], 'remote_model': 'some-cloud-model'}):
            async with self.client(lambda request: httpx.Response(200, json=metadata)) as client:
                with self.assertRaises(ValueError):
                    await ocr.check_local_engine(client, 'qwen2.5vl:3b')
        with patch.dict(os.environ, {'LOCAL_OCR_MODEL': 'model:cloud'}):
            with self.assertRaises(ValueError):
                ocr.local_model()

    async def test_request_contains_image_bytes_only_to_fixed_localhost(self):
        def respond(request):
            self.assertEqual(str(request.url), 'http://127.0.0.1:11434/api/chat')
            self.assertNotIn('authorization', request.headers)
            body = json.loads(request.content)
            self.assertEqual(body['format']['required'], list(ocr.SOURCES['passport_front']))
            self.assertFalse(body['format']['additionalProperties'])
            self.assertEqual(body['options']['num_predict'], 384)
            self.assertIs(body['stream'], False)
            self.assertEqual(len(body['messages'][1]['images']), 1)
            self.assertNotIn('api.telegram.org', request.content.decode())
            return self.response({'pinfl': '12345678901234', 'vehicle_registration': 'WRONGSOURCE'})
        bot = N(get_file=AsyncMock(return_value=N(file_size=100,
            download_as_bytearray=AsyncMock(return_value=self.image_bytes()))))
        async with self.client(respond) as client:
            result = await ocr.extract_one(client, bot, 'passport_front', 'file',
                asyncio.Semaphore(1), 'qwen2.5vl:3b')
        self.assertEqual(result['pinfl'], '12345678901234')
        self.assertNotIn('vehicle_registration', result)

    async def test_overlarge_document_rejected_before_download(self):
        file = N(file_size=5_000_001, download_as_bytearray=AsyncMock())
        with self.assertRaises(ValueError):
            await ocr.download_view(N(get_file=AsyncMock(return_value=file)), 'file')
        file.download_as_bytearray.assert_not_awaited()

    async def test_front_back_read_together_without_selfie_or_car(self):
        seen = []
        def respond(request):
            if request.url.path == '/api/show':
                return httpx.Response(200, json={'capabilities': ['vision']})
            body = json.loads(request.content)
            seen.append(body)
            return self.response({'pinfl': '12345678901234'})
        bot = N(get_file=AsyncMock(return_value=N(file_size=100,
            download_as_bytearray=AsyncMock(return_value=self.image_bytes()))))
        with patch.object(ocr, 'local_client', return_value=self.client(respond)), \
             patch.dict(os.environ, {'DOCUMENT_AI_ENABLED': '1'}):
            result = await ocr.extract_documents(bot, {'passport_front': 'front',
                'passport_back': 'back', 'selfie': 'private-selfie', 'car_front': 'private-car'})
        self.assertEqual(bot.get_file.await_count, 2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]['messages'][1]['images']), 2)
        self.assertEqual(result['backend'], 'local')

    async def test_conflicting_document_readings_are_not_guessed(self):
        with patch.object(ocr, 'local_client', return_value=self.client(
                lambda request: httpx.Response(200, json={'capabilities': ['vision']}))), \
             patch.dict(os.environ, {'DOCUMENT_AI_ENABLED': '1'}), \
             patch.object(ocr, 'extract_group', AsyncMock(side_effect=[
                {'pinfl': '12345678901234'}, {'pinfl': '12345678901235'}])):
            result = await ocr.extract_documents(N(), {'passport_front': 'a', 'license_front': 'b'})
        self.assertIsNone(result['values']['pinfl'])

    async def test_one_failed_group_preserves_other_fields(self):
        with patch.object(ocr, 'local_client', return_value=self.client(
                lambda request: httpx.Response(200, json={'capabilities': ['vision']}))), \
             patch.dict(os.environ, {'DOCUMENT_AI_ENABLED': '1'}), \
             patch.object(ocr, 'extract_group', AsyncMock(side_effect=[RuntimeError('sensitive'),
                {'vehicle_registration': 'ABC1234567'}])):
            with self.assertLogs(ocr.logger, 'WARNING') as logs:
                result = await ocr.extract_documents(N(), {'passport_front': 'a', 'tech_front': 'b'})
        self.assertNotIn('sensitive', '\n'.join(logs.output))
        self.assertEqual(result['values']['vehicle_registration'], 'ABC1234567')
        self.assertEqual(result['failed_sources'], ['passport_front'])

    async def test_incomplete_response_rejected(self):
        async with self.client(lambda request: httpx.Response(200, json={
                'done': True, 'done_reason': 'length', 'message': {'content': '{}'}})) as client:
            with self.assertRaises(ValueError):
                await ocr.read_local(client, 'qwen2.5vl:3b', ['bytes'], ('pinfl',), ('test',))

    async def test_redirect_not_followed(self):
        seen = []
        def respond(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={'Location': 'https://example.com'})
        async with self.client(respond) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await ocr.check_local_engine(client, 'qwen2.5vl:3b')
        self.assertEqual(len(seen), 1)

    async def test_result_cached_but_old_cloud_cache_replaced(self):
        ctx = N(bot=N(), user_data={'_document_ocr': {'status': 'complete', 'values': {}}})
        extractor = AsyncMock(return_value={'status': 'partial', 'values': {}, 'backend': 'local'})
        with patch.object(ocr, 'extract_documents', extractor):
            await ocr.prepare_document_summary(ctx)
            await ocr.prepare_document_summary(ctx)
        extractor.assert_awaited_once()

    async def test_group_inference_serialized(self):
        active = 0
        peak = 0
        async def read(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.01)
            active -= 1
            return {}
        with patch.object(ocr, 'download_view', AsyncMock(return_value='image')), \
             patch.object(ocr, 'read_local', side_effect=read):
            await asyncio.gather(*[ocr.extract_group(N(), N(), ('passport_front',),
                {'passport_front': 'file'}, 'qwen2.5vl:3b') for _ in range(3)])
        self.assertEqual(peak, 1)

    def test_submission_does_not_block_other_users(self):
        from telegram.ext import ConversationHandler
        from bot.handlers import driver
        conversation = driver.build_driver_conversation()
        self.assertFalse(conversation.states[driver.CAR_PLATE][0].block)
        self.assertEqual(conversation.states[ConversationHandler.WAITING][0].callback,
                         driver.submission_waiting)

    async def test_pending_submission_keeps_documents(self):
        from bot.handlers import driver
        data = {'passport_front': 'keep-file'}
        context = N(user_data=data)
        update = N(message=N(reply_text=AsyncMock()))
        await driver.submission_waiting(update, context)
        self.assertEqual(data, {'passport_front': 'keep-file'})

    async def test_timeout_keeps_completed_document_groups(self):
        original_wait = asyncio.wait
        async def short_wait(tasks, timeout):
            return await original_wait(tasks, timeout=.02)
        async def extract(client, bot, sources, data, model):
            if 'passport_front' in sources:
                return {'pinfl': '12345678901234'}
            await asyncio.sleep(10)
        with patch.object(ocr, 'local_client', return_value=self.client(
                lambda request: httpx.Response(200, json={'capabilities': ['vision']}))), \
             patch.dict(os.environ, {'DOCUMENT_AI_ENABLED': '1'}), \
             patch.object(ocr, 'extract_group', side_effect=extract), \
             patch.object(ocr.asyncio, 'wait', side_effect=short_wait):
            result = await ocr.extract_documents(N(), {'passport_front': 'a', 'tech_front': 'b'})
        self.assertEqual(result['values']['pinfl'], '12345678901234')
        self.assertEqual(result['failed_sources'], ['tech_front'])

    def test_preprocessing_trims_white_margins_without_clipping_ink(self):
        import base64
        from io import BytesIO
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (1800, 1000), 'white')
        ImageDraw.Draw(image).rectangle((500, 400, 1200, 600), fill='black')
        buffer = BytesIO()
        image.save(buffer, 'PNG')
        processed = Image.open(BytesIO(base64.b64decode(ocr.prepare_images(buffer.getvalue())[0])))
        self.assertLess(processed.width, 1800)
        self.assertLess(processed.height, 1000)
        self.assertGreater(processed.width, 700)
        self.assertGreater(processed.height, 200)
        self.assertLess(processed.convert('L').getpixel((processed.width // 2, processed.height // 2)), 10)

    def test_dark_background_keeps_frame_and_limits_size(self):
        import base64
        from io import BytesIO
        from PIL import Image
        image = Image.new('RGB', (3000, 2000), (100, 120, 140))
        buffer = BytesIO()
        image.save(buffer, 'JPEG')
        processed = Image.open(BytesIO(base64.b64decode(ocr.prepare_images(buffer.getvalue())[0])))
        self.assertEqual(processed.size, (1280, 853))
        self.assertFalse(processed.getexif())

    def test_short_prompt_preserves_document_specific_rules(self):
        prompt = ocr.field_prompt(('license_expiry',))
        self.assertIn('4b', prompt)
        self.assertIn('not issue/category', prompt)
        self.assertNotIn('vehicle_registration:', prompt)

    def test_vehicle_plate_never_becomes_certificate_number(self):
        for value in ('01A123BC', '01 123 ABC', 'AA-TEST'):
            values, rejected = ocr.guard_identifiers({'vehicle_registration': value}, value)
            self.assertIsNone(values['vehicle_registration'])
            self.assertIn('vehicle_registration', rejected)
        values, rejected = ocr.guard_identifiers({'vehicle_registration': 'ABC0012345'})
        self.assertEqual(values['vehicle_registration'], 'ABC0012345')
        self.assertEqual(rejected, [])

    def test_rejected_number_explains_manual_review(self):
        text = ocr.format_summary({'status': 'partial', 'values': {},
            'rejected_fields': ['vehicle_registration']})
        self.assertIn('Davlat raqami bilan chalkashdi', text)

    def test_plate_pattern_rejected_without_form_plate(self):
        for field in ('vehicle_registration', 'license_number'):
            values, _ = ocr.guard_identifiers({field: '01A123BC'})
            self.assertIsNone(values[field])

    def test_document_field_locations_are_explicit(self):
        self.assertIn('4d', ocr.field_prompt(('pinfl',)))
        self.assertIn('field 3', ocr.field_prompt(('birth_place', 'birth_date')))
        self.assertIn('NEVER field 8', ocr.field_prompt(('birth_place',)))
        self.assertIn('field 5', ocr.field_prompt(('license_number',)))
        self.assertIn('UPPER RIGHT near hologram', ocr.field_prompt(('vehicle_registration',)))
        self.assertIn('pinfl', ocr.SOURCES['license_front'])

    async def test_pinfl_can_come_from_licence_when_passport_is_empty(self):
        with patch.object(ocr, 'local_client', return_value=self.client(
                lambda request: httpx.Response(200, json={'capabilities': ['vision']}))), \
             patch.dict(os.environ, {'DOCUMENT_AI_ENABLED': '1'}), \
             patch.object(ocr, 'extract_group', AsyncMock(side_effect=[
                {'pinfl': None}, {'pinfl': '12345678901234'}])):
            result = await ocr.extract_documents(N(), {'passport_front': 'a', 'license_front': 'b'})
        self.assertEqual(result['values']['pinfl'], '12345678901234')
