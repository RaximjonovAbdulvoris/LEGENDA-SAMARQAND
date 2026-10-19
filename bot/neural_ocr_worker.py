"""Isolated local OCR worker. Images arrive in RAM; no network or document files."""
import base64
from io import BytesIO
import json
import re
import sys

from PIL import Image, ImageOps, ImageFilter
from bot.document_ocr import FIELDS, normalize, guard_identifiers
from bot.fast_ocr import Word, DATE, SERIAL

# OCR may omit punctuation between printed date digits. The document label
# and calendar validation still determine what the eight digits mean.
PRINTED_DATE = r'([0-9]{2})[ ./-]?([0-9]{2})[ ./-]?([0-9]{4})'


def document_kind(rows):
    """Identify a document only from several independent printed field labels."""
    texts = [text.strip().upper() for _, text, confidence in rows or [] if confidence >= .70]
    has_4d = any(re.match(r'^4\s*D[.)]?\s*[0-9]{14}\b', text) for text in texts)
    has_5 = any(re.match(r'^5[.)]?\s*[A-Z]{1,3}[0-9]{6,}', text) for text in texts)
    has_3 = any(re.match(r'^3[.)]?\s*.*[0-9]{4}$', text) for text in texts)
    has_4a = any(re.match(r'^4\s*A[.)]?\s*[0-9]{2}', text) for text in texts)
    if (has_4d and (has_5 or has_3 or has_4a)) or (has_3 and has_5 and has_4a):
        return 'license_front'
    numbered = {int(match[1]) for text in texts
                if (match := re.match(r'^(9|10|11|12|13|14)[.)]?\s*', text))}
    if 10 in numbered and len(numbered) >= 3 and (11 in numbered or 12 in numbered):
        return 'tech_back'
    return None


def fields_from_rows(rows, source, width, height):
    values = {field: set() for field in FIELDS}
    def add(field, text):
        value = normalize({field: text})[field]
        if value:
            values[field].add(value)
    for word in rows:
        text = word.text.strip()
        if word.confidence < 90:
            continue
        if source.startswith('passport_'):
            for match in re.finditer(r'(?<![0-9])([0-9]{14})(?![0-9])', text):
                add('pinfl', match[1])
        if source == 'license_front':
            # A licence may contain other 14-digit identifiers. Only field 4d
            # is the applicant's PINFL; an unlabelled number is not enough.
            pinfl = re.fullmatch(r'4\s*[dд][.)]?\s*([0-9]{14})', text, re.I)
            if pinfl:
                add('pinfl', pinfl[1])
            # Strip the printed field number before applying SERIAL. Its
            # left-boundary assertion otherwise rejects an intact "5AF..."
            # reading because the series immediately follows the digit 5.
            field_five = re.fullmatch(r'5[.)]?\s*(.+)', text)
            serial = re.fullmatch(SERIAL, field_five[1].upper()) if field_five else None
            if serial:
                add('license_number', serial[1])
            birth = re.fullmatch(r'3[.)]?\s*(.+?)\s*' + PRINTED_DATE, text)
            if birth:
                add('birth_date', '.'.join(birth.groups()[1:]))
                add('birth_place', clean_place(birth[1]) or '')
            expiry = re.fullmatch(r'4\s*[bвб][.)]?\s*' + PRINTED_DATE, text, re.I)
            if expiry:
                add('license_expiry', '.'.join(expiry.groups()))
        if source.startswith('tech_') and word.left >= .60 * width and word.top < .75 * height:
            serial = re.fullmatch(SERIAL, text.upper())
            if serial:
                add('vehicle_registration', serial[1])
    if source == 'license_front':
        # The field 3 label and its place/date are often separate boxes.
        for anchor in (w for w in rows if w.confidence >= 85 and
                       re.fullmatch(r'3[.)]?', w.text.strip())):
            matches = []
            for word in rows:
                if (word.confidence < 90 or word.left <= anchor.left or
                        not -anchor.width * .5 <= word.left - anchor.left - anchor.width < anchor.height * 3 or
                        abs(word.top + word.height / 2 - anchor.top - anchor.height / 2) > max(anchor.height, word.height) * .55):
                    continue
                match = re.fullmatch(r'(.+?)\s*' + PRINTED_DATE, word.text.strip())
                if match and clean_place(match[1]):
                    matches.append(match)
            if len(matches) == 1:
                add('birth_place', clean_place(matches[0][1]))
                add('birth_date', '.'.join(matches[0].groups()[1:]))
        # Some layouts put field 5 and its series in separate OCR boxes.
        for anchor in (w for w in rows if w.confidence >= 90 and
                       re.fullmatch(r'5[.)]?', w.text)):
            for word in rows:
                if (word.confidence < 90 or
                        not 0 <= word.left - anchor.left - anchor.width < anchor.height * 4 or
                        abs(word.top + word.height / 2 - anchor.top - anchor.height / 2)
                        > max(anchor.height, word.height) * .5):
                    continue
                serial = re.fullmatch(SERIAL, word.text.strip().upper())
                if serial:
                    add('license_number', serial[1])
        # Some OCR layouts split the printed 4d label and number into two boxes.
        anchors = [w for w in rows if w.confidence >= 90 and
                   re.fullmatch(r'4\s*[dд][.)]?', w.text, re.I)]
        for anchor in anchors:
            for word in rows:
                if (word.confidence >= 90 and re.fullmatch(r'[0-9]{14}', word.text)
                        and 0 <= word.left - anchor.left - anchor.width < anchor.height * 5
                        and abs(word.top + word.height / 2 - anchor.top - anchor.height / 2)
                        <= max(anchor.height, word.height) * .5):
                    add('pinfl', word.text)
        # On the numbered licence, 4b (expiry) is directly right of 4a.
        # Require the 4a anchor and one intact date on the same text baseline.
        anchors = [w for w in rows if w.confidence >= 90 and re.fullmatch(r'4a[.)]?\s*' + DATE, w.text, re.I)]
        for anchor in anchors:
            for word in rows:
                if word.left <= anchor.left + anchor.width or word.confidence < 90:
                    continue
                if abs(word.top + word.height / 2 - anchor.top - anchor.height / 2) > max(anchor.height, word.height) * .5:
                    continue
                dates = list(re.finditer(DATE, word.text))
                if len(dates) == 1:
                    add('license_expiry', '.'.join(dates[0].groups()))
    return {key: next(iter(items)) if len(items) == 1 else None for key, items in values.items()}


def clean_name(text):
    text = ' '.join(text.split()).strip(" .")
    if not 2 <= len(text) <= 140 or not re.fullmatch(r"[^\W\d_]+(?:[ '‘’ʼ`-][^\W\d_]+)*", text, re.UNICODE):
        return None
    return text


def clean_place(text):
    value = clean_name(text)
    if value:
        # Restore only a word boundary before a printed administrative suffix.
        # Do not complete, substitute, or spell-correct geographic names.
        value = re.sub(r'(?<=[A-Z])\s*(TUMANI|SHAHRI|VILOYATI|RESPUBLIKASI)$', r' \1', value)
    return value


def licence_identity(words):
    # Only the printed field 2 belongs to the given name. The line below it
    # often contains the patronymic and must not be shown to operators.
    def numbered(rank):
        result = []
        for word in words:
            if word.confidence < 85:
                continue
            match = re.fullmatch(rf'{rank}[.)]?\s*(.*)', word.text.strip())
            if match and (not match[1] or clean_name(match[1])):
                result.append((word, clean_name(match[1]) if match[1] else None))
        return result

    ones, twos = numbered(1), numbered(2)
    if not ones and len(twos) == 1:
        # A tiny printed '1.' can disappear while the surname remains clear.
        # Recover only inside the numbered identity block, aligned above 2.
        second, attached = twos[0]
        given_rows = [(w, clean_name(w.text)) for w in words
                      if w.confidence >= 90 and w.left > second.left
                      and abs(w.top + w.height / 2 - second.top - second.height / 2)
                      < max(w.height, second.height) * .55
                      and -second.width * .5 <= w.left - second.left - second.width
                      < max(w.height, second.height) * 2.5
                      and clean_name(w.text)]
        if attached:
            given_rows = [(second, attached)]
        thirds = [w for w in words if w.confidence >= 90
                  and re.fullmatch(r'3[.)]?\s*.+?\s*' + PRINTED_DATE, w.text.strip())
                  and 0 < w.top - second.top < max(w.height, second.height) * 4
                  and abs(w.left - second.left) < max(w.height, second.height) * 1.4]
        if len(given_rows) != 1 or len(thirds) != 1:
            return None
        given_box, given = given_rows[0]
        candidates = [(w, clean_name(w.text)) for w in words
                      if w.confidence >= 95 and clean_name(w.text)
                      and .6 * max(w.height, given_box.height) < given_box.top - w.top
                      < 1.8 * max(w.height, given_box.height)
                      and abs(w.left - given_box.left) < max(w.height, given_box.height) * .5]
        if len(candidates) != 1:
            return None
        return candidates[0][1], given
    if len(ones) != 1 or len(twos) != 1:
        return None
    first, first_attached = ones[0]
    second, second_attached = twos[0]
    if not 0 < second.top - first.top < max(first.height, second.height) * 2.4:
        return None
    if abs(first.left - second.left) > max(first.height, second.height) * 1.4:
        return None

    def row_value(anchor, attached):
        if attached:
            return anchor, attached
        choices = [(word, clean_name(word.text)) for word in words
                   if word.confidence >= 90 and word is not anchor
                   and word.left > anchor.left
                   and -anchor.width * .5 <= word.left - anchor.left - anchor.width < anchor.height * 2.5
                   and abs(word.top + word.height / 2 - anchor.top - anchor.height / 2)
                   < max(word.height, anchor.height) * .55]
        choices = [(word, value) for word, value in choices if value]
        return choices[0] if len(choices) == 1 else (None, None)

    _, surname = row_value(first, first_attached)
    _, given = row_value(second, second_attached)
    if not surname or not given:
        return None
    return (surname, given) if clean_name(surname + ' ' + given) else None


def licence_name(words):
    identity = licence_identity(words)
    return ' '.join(identity) if identity else None


def unanimous(readings):
    # Different confident readings remain unknown, even if a majority agrees.
    items = set(value for value in readings if value)
    return next(iter(items)) if len(items) == 1 and sum(bool(x) for x in readings) >= 3 else None


def refine_registration(engine, image, words):
    """Confirm the certificate series around its detected text box.

    Holograms can make a final digit look plausible but different. Any
    disagreement is left empty instead of quietly choosing a majority.
    """
    import numpy as np
    candidates = [word for word in words if word.confidence >= 90
                  and re.fullmatch(SERIAL, word.text.strip().upper())
                  and word.left >= .60 * image.width and word.top < .75 * image.height]
    if len(candidates) != 1:
        return None
    word = candidates[0]
    margin = max(12, int(word.height * 2))
    crop = image.crop((max(0, int(word.left) - margin),
                       max(0, int(word.top) - margin),
                       min(image.width, int(word.left + word.width) + margin * 2),
                       min(image.height, int(word.top + word.height) + margin * 2)))
    contrast = ImageOps.autocontrast(crop)
    views = (ImageOps.invert(crop), crop, contrast,
             contrast.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2)))
    readings = []
    for view in views:
        rows, _ = engine(np.array(view)[:, :, ::-1].copy())
        matches = {normalize({'vehicle_registration': match[1]})['vehicle_registration']
                   for _, text, confidence in rows or [] if confidence >= .90
                   for match in [re.fullmatch(SERIAL, text.strip().upper())] if match}
        readings.append(next(iter(matches)) if len(matches) == 1 else None)
    return unanimous(readings)


def certificate_serial_candidate(words, width, height):
    """The printed certificate series sits below/right of the control code.

    If that layout is not clear, leave the serial for an operator to check.
    """
    candidates = [w for w in words if w.confidence >= 90 and
                  re.fullmatch(SERIAL, w.text.strip().upper()) and
                  w.left >= .60 * width and w.top < .75 * height]
    distinct = {normalize({'vehicle_registration': w.text})['vehicle_registration']
                for w in candidates}
    if len(distinct) == 1:
        return candidates[0]
    # Duplicate OCR passes may yield the same two printed codes. One can be
    # selected only when every competing code is clearly above and to its left.
    chosen = [w for w in candidates if all(
        normalize({'vehicle_registration': other.text})['vehicle_registration'] ==
        normalize({'vehicle_registration': w.text})['vehicle_registration'] or
        (w.left - other.left > max(w.height, other.height) * 2 and
         w.top - other.top > max(w.height, other.height) * .5)
        for other in candidates)]
    return chosen[0] if len({w.text for w in chosen}) == 1 and chosen else None


def refine_birth(engine, image, word):
    import numpy as np
    boundary = int(word.left + .7 * word.width)
    top, bottom = max(0, int(word.top) + 1), min(image.height, int(word.top + word.height) - 2)
    regions = {
        'birth_place': (max(0, round(word.left + .1 * word.width)), top, boundary - 1, bottom),
        'birth_date': (max(0, boundary), top, min(image.width, int(word.left + word.width) + 7), bottom),
    }
    result = {}
    for key, box in regions.items():
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        readings = []
        for scale in (2, 3, 4):
            region = image.crop(box)
            region = region.resize((region.width * scale, region.height * scale), Image.Resampling.BICUBIC)
            region = ImageOps.autocontrast(region.convert('L')).convert('RGB')
            rows, _ = engine(np.array(region)[:, :, ::-1].copy(), use_det=False, use_cls=False)
            if not rows or len(rows) != 1:
                readings.append(None)
                continue
            text, confidence = rows[0]
            if key == 'birth_date':
                # Formatting eight printed digits is permitted; never substitute
                # ambiguous O/I/S characters or infer a date from PINFL.
                digits = re.sub(r'[ ./-]', '', text)
                value = normalize({'birth_date': digits[:2]+'.'+digits[2:4]+'.'+digits[4:]})['birth_date'] if re.fullmatch(r'[0-9]{8}', digits) else None
                readings.append(value if confidence >= .90 else None)
            else:
                value = clean_place(text)
                readings.append(value if confidence >= .85 else None)
        result[key] = unanimous(readings)
    return result


def orientation_hint(result, technical=False):
    """Infer page rotation from detected text geometry, not phone EXIF alone."""
    from statistics import median
    tall = wide = 0
    ranks = {}
    for box, text, confidence in result or []:
        x = [point[0] for point in box]
        y = [point[1] for point in box]
        width, height = max(x) - min(x), max(y) - min(y)
        if len(text) >= 6 and confidence >= .6:
            tall += height > width * 1.5
            wide += width > height * 1.5
        match = re.match(r'^([0-9]{1,2})[.)]', text) if technical else re.match(r'^([1-5])(?:[a-d])?(?:[.)]|\s|(?=[A-Z]))', text, re.I)
        if match and confidence >= .85:
            ranks.setdefault(int(match[1]), []).append(sum(y) / len(y))
    if tall >= 3 and tall > wide * 1.5:
        return 90
    ordered = [(rank, median(centers)) for rank, centers in sorted(ranks.items())]
    inversions = sum(a[1] > b[1] for a, b in zip(ordered, ordered[1:]))
    if len(ordered) >= 3 and inversions >= 2 and inversions == len(ordered) - 1:
        return 180
    return 0


def read_document(engine, raw, source, emit=None, return_kind=False):
    import numpy as np
    with Image.open(BytesIO(raw)) as opened:
        if opened.width * opened.height > 30_000_000:
            raise ValueError('Image too large')
        image = ImageOps.exif_transpose(opened).convert('RGB')
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    result, _ = engine(np.array(ImageOps.invert(image))[:, :, ::-1].copy())
    detected = document_kind(result)
    if not detected and source not in ('license_front', 'tech_back'):
        normal, _ = engine(np.array(image)[:, :, ::-1].copy())
        detected = document_kind(normal)
        if detected:
            result = normal
    if detected:
        source = detected
    if source == 'license_front' or source.startswith('tech_'):
        for _ in range(2):
            angle = orientation_hint(result, technical=source.startswith('tech_'))
            if not angle:
                break
            image = image.rotate(angle, expand=True)
            result, _ = engine(np.array(ImageOps.invert(image))[:, :, ::-1].copy())
    words = []
    for box, text, confidence in result or []:
        left, top = min(p[0] for p in box), min(p[1] for p in box)
        words.append(Word(text, confidence * 100, left, top,
            max(p[0] for p in box) - left, max(p[1] for p in box) - top))
    values = fields_from_rows(words, source, image.width, image.height)
    conflicts = set()
    normal_words = []
    if source == 'license_front':
        identity = licence_identity(words)
        values['surname'], values['given_name'] = identity or (None, None)
        values['full_name'] = ' '.join(identity) if identity else None
        if not identity or any(not values.get(key) for key in
                ('pinfl', 'birth_date', 'birth_place', 'license_number', 'license_expiry')):
            normal, _ = engine(np.array(image)[:, :, ::-1].copy())
            normal_words = []
            for box, text, confidence in normal or []:
                left, top = min(p[0] for p in box), min(p[1] for p in box)
                normal_words.append(Word(text, confidence * 100, left, top,
                    max(p[0] for p in box) - left, max(p[1] for p in box) - top))
            normal_fields = fields_from_rows(normal_words, source, image.width, image.height)
            for key, value in normal_fields.items():
                if not value:
                    continue
                if values.get(key) and values[key] != value:
                    values[key] = None
                    conflicts.add(key)
                elif key not in conflicts and not values.get(key):
                    values[key] = value
            normal_identity = licence_identity(normal_words)
            if identity and normal_identity and identity != normal_identity:
                values['surname'] = values['given_name'] = values['full_name'] = None
                conflicts.update(('surname', 'given_name', 'full_name'))
            elif not identity:
                identity = normal_identity
                values['surname'], values['given_name'] = identity or (None, None)
                values['full_name'] = ' '.join(identity) if identity else None
        if emit:
            emit(guard_identifiers(values)[0], source)
        anchors = [w for w in words if w.confidence >= 75 and re.match(r'^3[.)]', w.text)]
        if len(anchors) == 1 and (not values['birth_place'] or not values['birth_date']):
            refined = refine_birth(engine, image, anchors[0])
            for key, value in refined.items():
                # A narrow crop can read only part of a place name. The intact,
                # numbered field 3 has precedence when it already parsed cleanly.
                if value and not values[key]:
                    values[key] = value
    # Spend the extra time only when the first pass did not read the fields
    # required from this document. Local contrast restores dim text and the
    # mild sharpening can separate letters softened by camera focus.
    required = ('full_name', 'pinfl', 'birth_date', 'birth_place',
                'license_number', 'license_expiry') if source == 'license_front' else ('vehicle_registration',)
    other = []
    if any(not values.get(key) for key in required):
        grayscale = ImageOps.autocontrast(image.convert('L'), cutoff=1)
        enhanced = grayscale.filter(ImageFilter.UnsharpMask(radius=1, percent=130, threshold=2)).convert('RGB')
        second, _ = engine(np.array(ImageOps.invert(enhanced))[:, :, ::-1].copy())
        for box, text, confidence in second or []:
            left, top = min(p[0] for p in box), min(p[1] for p in box)
            other.append(Word(text, confidence * 100, left, top,
                max(p[0] for p in box) - left, max(p[1] for p in box) - top))
        alternate = fields_from_rows(other, source, image.width, image.height)
        if source == 'license_front':
            other_identity = licence_identity(other)
            alternate['surname'], alternate['given_name'] = other_identity or (None, None)
            alternate['full_name'] = ' '.join(other_identity) if other_identity else None
        required = (*required, 'surname', 'given_name') if source == 'license_front' else required
        for key in required:
            if alternate.get(key):
                if values.get(key) and values[key] != alternate[key]:
                    values[key] = None  # Never silently prefer conflicting readings.
                    conflicts.add(key)
                elif key not in conflicts and not values.get(key):
                    values[key] = alternate[key]
    if source.startswith('tech_'):
        # A full-frame OCR confidence score alone is insufficient on holograms.
        # This also excludes serial-looking text from the vehicle data rows.
        # The normal and contrast passes can detect the same serial in
        # different boxes. Deduplicate matching readings, but never resolve
        # conflicting readings by majority vote.
        candidate = certificate_serial_candidate(words + other, image.width, image.height)
        confirmed = refine_registration(engine, image, [candidate]) if candidate else None
        expected = normalize({'vehicle_registration': candidate.text})['vehicle_registration'] if candidate else None
        values['vehicle_registration'] = confirmed if confirmed == expected else None
    values = guard_identifiers(values)[0]
    return (values, source) if return_kind else values


def main():
    from bot.ocr_sandbox import restrict_worker
    restrict_worker()
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    if '--check' in sys.argv:
        print(json.dumps({'ready': True}), flush=True)
        return
    payload = json.loads(sys.stdin.buffer.read(45_000_000))
    for item in payload:
        source = item['source']
        try:
            raw = base64.b64decode(item['image'], validate=True)
            if len(raw) > 5_000_000:
                raise ValueError('Image too large')
            values, kind = read_document(engine, raw, source, return_kind=True,
                emit=lambda partial, detected: print(json.dumps({
                    'source': source, 'kind': detected, 'values': partial, 'final': False}), flush=True))
            print(json.dumps({'source': source, 'kind': kind, 'values': values, 'final': True}), flush=True)
        except Exception as error:
            print(json.dumps({'source': source, 'error': type(error).__name__}), flush=True)


if __name__ == '__main__':
    main()
