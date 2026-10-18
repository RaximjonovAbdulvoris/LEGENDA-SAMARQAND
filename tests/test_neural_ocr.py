import asyncio
import os
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from bot import neural_ocr as neural
from bot import document_ocr as ocr
from bot.fast_ocr import Word
from bot.neural_ocr_worker import fields_from_rows


def row(text, x=300, y=300, confidence=99, width=200, height=30):
    return Word(text, confidence, x, y, width, height)


class NeuralOCRTests(unittest.IsolatedAsyncioTestCase):
    def test_split_birth_label_and_dropped_date_punctuation(self):
        values = fields_from_rows([
            row('3.', x=430, y=265, width=30, height=23, confidence=89),
            row('TESTCITY27.121985', x=459, y=255, width=260, height=37),
            row('4b.0203.2032', x=645, y=283)], 'license_front', 1280, 720)
        self.assertEqual(values['birth_place'], 'TESTCITY')
        self.assertEqual(values['birth_date'], '27.12.1985')
        self.assertEqual(values['license_expiry'], '02.03.2032')
        values = fields_from_rows([row('4b.3102.2032')], 'license_front', 1280, 720)
        self.assertIsNone(values['license_expiry'])

    def test_name_label_box_can_overlap_text_without_including_patronymic(self):
        from bot.neural_ocr_worker import licence_identity
        words = [row('1', x=429, y=176, width=40, height=26),
                 row('EXAMPLE', x=460, y=174, height=28),
                 row('2', x=430, y=205, width=26, height=26),
                 row('PERSON', x=460, y=203, height=28),
                 row('PATRONYMIC', x=465, y=230, height=30)]
        self.assertEqual(licence_identity(words), ('EXAMPLE', 'PERSON'))

    def test_field_five_attached_or_separate_from_series(self):
        attached = fields_from_rows([row('5AF1834658')], 'license_front', 960, 1280)
        self.assertEqual(attached['license_number'], 'AF1834658')
        separate = fields_from_rows([row('5', x=300, width=18),
                                     row('AF1834658', x=325)], 'license_front', 960, 1280)
        self.assertEqual(separate['license_number'], 'AF1834658')
        off_line = fields_from_rows([row('5', x=300, y=100, width=18),
                                     row('AF1834658', x=325, y=300)],
                                    'license_front', 960, 1280)
        self.assertIsNone(off_line['license_number'])

    def test_numbered_lines_without_spaces_and_expiry_anchor(self):
        values = fields_from_rows([row('4d12345678901234'), row('5.AB1234567'),
            row('4a.01.02.2020', y=200, width=150), row('4401.02.2030', x=500, y=200)],
            'license_front', 960, 1280)
        self.assertEqual(values['pinfl'], '12345678901234')
        self.assertEqual(values['license_number'], 'AB1234567')
        self.assertEqual(values['license_expiry'], '01.02.2030')
        self.assertIsNone(values['birth_date'])

    def test_address_and_wrong_baseline_are_not_birth_or_expiry(self):
        values = fields_from_rows([row('8.OTHER CITY'), row('4a.01.02.2020', y=200),
            row('01.02.2030', x=600, y=500)], 'license_front', 960, 1280)
        self.assertIsNone(values['birth_place'])
        self.assertIsNone(values['license_expiry'])

    def test_serial_excludes_control_number_outside_right_edge(self):
        values = fields_from_rows([row('VL1234567', x=550, y=600),
            row('ABC1234567', x=700, y=630), row('ABC7654321', x=400, y=1040)],
            'tech_back', 960, 1280)
        self.assertEqual(values['vehicle_registration'], 'ABC1234567')

    def test_registration_hologram_at_sixty_two_percent_width(self):
        values = fields_from_rows([row('ABC1234567', x=600, y=430)],
                                  'tech_back', 960, 1280)
        self.assertEqual(values['vehicle_registration'], 'ABC1234567')

    def test_conflicts_low_confidence_and_uncertain_digits_remain_blank(self):
        for rows in [[row('5.AB1234567'), row('5.AB1234568')],
                     [row('5.AB1234567', confidence=70)], [row('5.AB12345O7')]]:
            self.assertIsNone(fields_from_rows(rows, 'license_front', 960, 1280)['license_number'])

    def test_licence_pinfl_requires_printed_4d_field(self):
        candidate = '12345678901234'
        self.assertIsNone(fields_from_rows([row(candidate)], 'license_front', 960, 1280)['pinfl'])
        self.assertEqual(fields_from_rows([row('4d', x=260),
            row(candidate, x=465)], 'license_front', 960, 1280)['pinfl'], candidate)
        self.assertIsNone(fields_from_rows([row('4d', x=260, y=200),
            row(candidate, x=465, y=300)], 'license_front', 960, 1280)['pinfl'])

    async def test_dispatch_never_contacts_external_or_ollama(self):
        with patch.dict(os.environ, {'DOCUMENT_AI_ENABLED': '1', 'DOCUMENT_OCR_BACKEND': 'rapid'}), \
             patch.object(neural, 'extract_documents', AsyncMock(return_value={'backend': 'rapid'})), \
             patch.object(ocr, 'local_client') as client:
            self.assertEqual((await ocr.extract_documents(N(), {}))['backend'], 'rapid')
        client.assert_not_called()

    async def test_partial_worker_output_survives_and_other_images_are_marked_failed(self):
        file = N(file_size=4, download_as_bytearray=AsyncMock(return_value=b'data'))
        bot = N(get_file=AsyncMock(return_value=file))
        with patch.object(neural, 'worker', AsyncMock(return_value=[{
            'source': 'license_front', 'values': {'pinfl': '12345678901234'}}])):
            result = await neural.extract_documents(bot, {'license_front': 'one', 'tech_back': 'two', 'selfie': 'excluded'})
        self.assertEqual(result['values']['pinfl'], '12345678901234')
        self.assertEqual(result['failed_sources'], ['tech_back'])
        self.assertEqual(bot.get_file.await_count, 2)

    async def test_worker_timeout_kills_child_and_keeps_emitted_result(self):
        stdout = N(readline=AsyncMock(side_effect=[b'{"source":"license_front","values":{}}\n']))
        process = N(returncode=None, stdin=N(write=lambda _: None, drain=AsyncMock(), close=lambda: None),
            stdout=stdout, wait=AsyncMock(), kill=lambda: setattr(process, 'returncode', -9))
        async def timeout_after_one(awaitable, timeout):
            task = asyncio.create_task(awaitable)
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise asyncio.TimeoutError
        with patch.object(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)) as spawn, \
             patch.object(asyncio, 'wait_for', side_effect=timeout_after_one), \
             patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'do-not-inherit'}):
            result = await neural._worker([])
        self.assertEqual(len(result), 1)
        self.assertEqual(process.returncode, -9)
        self.assertNotIn('TELEGRAM_BOT_TOKEN', spawn.call_args.kwargs['env'])

    async def test_worker_budget_allows_registration_crosscheck(self):
        process = N(returncode=0, stdin=N(write=lambda _: None, drain=AsyncMock(), close=lambda: None),
            stdout=N(readline=AsyncMock(return_value=b'')), wait=AsyncMock(), kill=lambda: None)
        with patch.object(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)), \
             patch.object(asyncio, 'wait_for', side_effect=lambda awaitable, timeout: asyncio.ensure_future(awaitable)) as wait:
            await neural._worker([])
        self.assertEqual(wait.call_args.kwargs['timeout'], 40)

class LicenceIdentityTests(unittest.TestCase):
    def test_registration_disagreement_on_hologram_is_not_exported(self):
        from bot.neural_ocr_worker import refine_registration
        from PIL import Image
        from unittest.mock import Mock
        box = [[10, 10], [180, 10], [180, 45], [10, 45]]
        engine = Mock(side_effect=[([(box, 'AB1234567', .99)], None)] * 3 +
                                  [([(box, 'AB1234568', .99)], None)])
        image = Image.new('RGB', (960, 1280))
        candidate = row('AB1234567', x=650, y=820, width=140, height=30)
        self.assertIsNone(refine_registration(engine, image, [candidate]))
        engine = Mock(return_value=([(box, 'AB1234567', .99)], None))
        self.assertEqual(refine_registration(engine, image, [candidate]), 'AB1234567')
        self.assertLess(engine.call_args.args[0].shape[0], 300)
        self.assertIsNone(refine_registration(engine, image, []))

    def test_enhanced_pass_fills_missing_field_without_overriding_conflict(self):
        from bot.neural_ocr_worker import read_document
        from PIL import Image
        from io import BytesIO
        image = Image.new('RGB', (960, 1280), 'white')
        buffer = BytesIO()
        image.save(buffer, 'PNG')
        def detected(text, y):
            return ([[300, y], [650, y], [650, y + 30], [300, y + 30]], text, .99)
        first = ([detected('5.AB1234567', 300)], None)
        second = ([detected('5.AB1234568', 300), detected('4d12345678901234', 350)], None)
        engine = unittest.mock.Mock(side_effect=[first, first, second])
        values = read_document(engine, buffer.getvalue(), 'license_front')
        self.assertEqual(engine.call_count, 3)
        self.assertEqual(values['pinfl'], '12345678901234')
        self.assertIsNone(values['license_number'])

    def test_complete_field_three_does_not_need_a_fragile_crop(self):
        from bot.neural_ocr_worker import read_document
        from PIL import Image
        from io import BytesIO
        image = Image.new('RGB', (960, 1280), 'white')
        buffer = BytesIO()
        image.save(buffer, 'PNG')
        box = [[300, 500], [670, 500], [670, 535], [300, 535]]
        detected = ([(box, '3. TEST CITY 01.02.1990', .99)], None)
        with patch('bot.neural_ocr_worker.refine_birth', side_effect=AssertionError('unneeded crop')):
            values = read_document(lambda *args, **kwargs: detected,
                                   buffer.getvalue(), 'license_front')
        self.assertEqual(values['birth_place'], 'TEST CITY')
        self.assertEqual(values['birth_date'], '01.02.1990')

    def test_name_requires_field_two_and_aligned_surname(self):
        from bot.neural_ocr_worker import licence_name
        words = [row('1', x=290, y=100, width=20, height=28),
                 row('EXAMPLE', x=320, y=100, height=28),
                 row('2', x=290, y=130, width=20, height=28),
                 row('PERSON', x=320, y=130, height=28),
                 row('OTHER REGION', x=780, y=100)]
        self.assertEqual(licence_name(words), 'EXAMPLE PERSON')
        self.assertIsNone(licence_name([w for w in words if w.text != '2']))
        self.assertIsNone(licence_name(words + [row('CONFLICT', x=322, y=101, height=28)]))

    def test_licence_name_excludes_patronymic_below_field_two(self):
        from bot.neural_ocr_worker import licence_identity
        words = [row('1', x=290, y=100, width=20, height=28, confidence=92),
                 row('EXAMPLE', x=320, y=100, height=28),
                 row('2', x=290, y=135, width=20, height=28),
                 row('PERSON', x=320, y=135, height=28),
                 row("SECONDO'G'LI", x=321, y=172, height=28, confidence=89),
                 row('3.TEST CITY 01.02.1990', x=290, y=207, height=28)]
        self.assertEqual(licence_identity(words), ('EXAMPLE', 'PERSON'))
        self.assertEqual(licence_identity(words + [row('CONFLICT', x=321, y=176, height=28)]), ('EXAMPLE', 'PERSON'))

    def test_repeated_ocr_disagreement_stays_unknown(self):
        from bot.neural_ocr_worker import unanimous
        self.assertEqual(unanimous(['TEST CITY'] * 3), 'TEST CITY')
        self.assertIsNone(unanimous(['TEST CITY', 'TEST C1TY', 'TEST CITY']))
        self.assertIsNone(unanimous(['TEST CITY', None, 'TEST CITY']))

    def test_names_are_escaped_and_do_not_replace_applicant_name(self):
        from bot.document_ocr import format_summary
        text = format_summary({'backend': 'rapid', 'values': {'full_name': 'EXAMPLE PERSON'}})
        self.assertIn('Pravadagi ism-familiya', text)
        self.assertIn('<code>EXAMPLE PERSON</code>', text)
        from bot.neural_ocr_worker import clean_name
        self.assertIsNone(clean_name('<script>'))
        self.assertIsNone(clean_name('PERSON123'))

@unittest.skipUnless(__import__('importlib.util', fromlist=['find_spec']).find_spec('numpy'), 'Optional neural dependencies required')
class LicenceBirthRefinementTests(unittest.TestCase):
    def test_three_reads_required_and_ambiguous_characters_not_repaired(self):
        from bot.neural_ocr_worker import refine_birth
        from PIL import Image
        from unittest.mock import Mock
        image = Image.new('RGB', (960, 1280), 'white')
        field = row('3.TEST CITY 01.02.1990', x=280, y=640, width=360, height=36)
        good = Mock(side_effect=[([('TEST CITY', .96)], None)] * 3 + [([('01021990', .96)], None)] * 3)
        result = refine_birth(good, image, field)
        self.assertEqual(result['birth_place'], 'TEST CITY')
        self.assertEqual(result['birth_date'], '01.02.1990')
        uncertain = Mock(side_effect=[([('TEST CITY', .96)], None)] * 3 + [([('O1021990', .96)], None)] * 3)
        self.assertIsNone(refine_birth(uncertain, image, field)['birth_date'])

class LicenceOrientationTests(unittest.TestCase):
    def test_vertical_lines_request_quarter_turn(self):
        from bot.neural_ocr_worker import orientation_hint
        rows = [([[x, 100], [x + 20, 100], [x + 20, 300], [x, 300]], '4d12345678901234', .99)
                for x in (100, 150, 200)]
        self.assertEqual(orientation_hint(rows), 90)

    def test_upside_down_numbered_rows_and_upright_rows(self):
        from bot.neural_ocr_worker import orientation_hint
        def detected(text, y):
            return ([[100, y], [300, y], [300, y + 25], [100, y + 25]], text, .99)
        upright = [detected('1SURNAME', 100), detected('2GIVEN', 140), detected('3. TEST CITY 01.02.1990', 180)]
        self.assertEqual(orientation_hint(upright), 0)
        upside_down = [detected('1SURNAME', 300), detected('2GIVEN', 250), detected('3. TEST CITY 01.02.1990', 200)]
        self.assertEqual(orientation_hint(upside_down), 180)

    def test_joined_name_labels_are_supported(self):
        from bot.neural_ocr_worker import licence_name
        self.assertEqual(licence_name([row('1EXAMPLE', x=320, y=100, height=28),
            row('2PERSON', x=320, y=130, height=28)]), 'EXAMPLE PERSON')

class PlaceFormattingTests(unittest.TestCase):
    def test_only_printed_suffix_boundary_is_restored(self):
        from bot.neural_ocr_worker import clean_place
        self.assertEqual(clean_place('TESTTUMANI'), 'TEST TUMANI')
        self.assertEqual(clean_place('TEST TUMANI'), 'TEST TUMANI')
        self.assertEqual(clean_place('TEST TUMNI'), 'TEST TUMNI')

class NeuralQueueAndFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_queue_does_not_use_two_second_timeout(self):
        with patch.object(neural, '_worker', AsyncMock(return_value=[])), \
             patch.object(asyncio, 'wait_for') as timed:
            await neural.worker([])
        timed.assert_not_called()

    async def test_front_registration_is_used_when_back_read_is_empty(self):
        file = N(file_size=4, download_as_bytearray=AsyncMock(return_value=b'data'))
        bot = N(get_file=AsyncMock(return_value=file))
        with patch.object(neural, 'worker', AsyncMock(return_value=[
            {'source': 'tech_back', 'values': {}},
            {'source': 'tech_front', 'values': {'vehicle_registration': 'ABC1234567'}}])):
            result = await neural.extract_documents(bot, {'tech_back': 'back', 'tech_front': 'front'})
        self.assertEqual(result['values']['vehicle_registration'], 'ABC1234567')
        self.assertFalse(result['retryable'])

    async def test_incremental_result_is_preserved_when_source_never_finishes(self):
        file = N(file_size=4, download_as_bytearray=AsyncMock(return_value=b'data'))
        with patch.object(neural, 'worker', AsyncMock(return_value=[{
            'source': 'license_front', 'values': {'pinfl': '12345678901234'}, 'final': False}])):
            result = await neural.extract_documents(N(get_file=AsyncMock(return_value=file)), {'license_front': 'one'})
        self.assertEqual(result['values']['pinfl'], '12345678901234')
        self.assertTrue(result['retryable'])

    async def test_final_result_can_remove_an_unconfirmed_partial_value(self):
        file = N(file_size=4, download_as_bytearray=AsyncMock(return_value=b'data'))
        with patch.object(neural, 'worker', AsyncMock(return_value=[
            {'source': 'license_front', 'values': {'birth_place': 'TEST CITY'}, 'final': False},
            {'source': 'license_front', 'values': {'birth_place': None}, 'final': True}])):
            result = await neural.extract_documents(N(get_file=AsyncMock(return_value=file)), {'license_front': 'one'})
        self.assertIsNone(result['values']['birth_place'])

    def test_expired_and_future_dates_are_marked_for_review(self):
        from datetime import date
        warnings = ocr.review_warnings({'license_expiry': '01.01.2020', 'birth_date': '01.01.2040'}, date(2026, 1, 1))
        self.assertEqual(len(warnings), 2)

class TechnicalOrientationTests(unittest.TestCase):
    def test_upside_down_registration_numbered_layout(self):
        from bot.neural_ocr_worker import orientation_hint
        rows = [([[100,y],[300,y],[300,y+20],[100,y+20]], text, .99)
                for text,y in [('9. 2020',300),('10. SEDAN',250),('11. VIN',200)]]
        self.assertEqual(orientation_hint(rows, technical=True), 180)
