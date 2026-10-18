"""Smoke check with generated, fictional text only; never reads user documents."""
import asyncio
import base64
from io import BytesIO
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from bot.neural_ocr import engine_info, worker


async def main():
    await engine_info()
    image = Image.new('RGB', (1200, 600), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 48)
    for y, text in [(50, '4d. 12345678901234'), (150, '5. AB1234567'), (250, '4b. 01.02.2030')]:
        draw.text((50, y), text, font=font, fill='black')
    stream = BytesIO()
    image.save(stream, 'PNG')
    start = time.monotonic()
    rows = await worker([{'source': 'license_front', 'image': base64.b64encode(stream.getvalue()).decode()}])
    values = rows[0].get('values', {}) if rows else {}
    if values.get('pinfl') != '12345678901234' or values.get('license_number') != 'AB1234567':
        raise RuntimeError('Synthetic OCR verification failed')
    print(f'Local neural OCR smoke passed in {time.monotonic() - start:.2f}s. Real document accuracy is not guaranteed.')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print('Neural OCR check failed: ' + type(error).__name__)
        raise SystemExit(1)
