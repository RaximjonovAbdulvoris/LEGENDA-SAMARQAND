"""Generate matching instruction graphics, never replicas of personal documents."""
import os
from pathlib import Path

CARDS = {
    'passport_front': ('01', 'Pasport / ID-karta', 'Old tomoni', 'Hujjatning old tomonini to‘liq kadrga oling.'),
    'license_front': ('02', 'Haydovchilik guvohnomasi', 'Old tomoni', 'Seriya, raqam va amal qilish muddati ko‘rinsin.'),
    'license_back': ('03', 'Haydovchilik guvohnomasi', 'Orqa tomoni', 'Hujjatning to‘rtala chetini ham kadrga kiriting.'),
    'tech_front': ('04', 'Texpasport', 'Old tomoni', 'Guvohnoma seriyasi va raqamini kesib yubormang.'),
    'tech_back': ('05', 'Texpasport', 'Orqa tomoni', 'Seriya va raqam joyi tiniq, yaltirashsiz ko‘rinsin.'),
    'selfie': ('06', 'Selfie', 'Yuz old tomondan', 'Yorug‘ joyda, ko‘zoynaksiz va filtrsiz suratga oling.'),
    'litsenziya': ('07', 'Litsenziya', 'Hujjat to‘liq kadrda', 'Hujjatning asl nusxasini tiniq suratga oling.'),
    'car_front': ('08', 'Avtomobil', 'Old tomoni', 'Avtomobil va davlat raqami to‘liq ko‘rinsin.'),
    'car_back': ('09', 'Avtomobil', 'Orqa tomoni', 'Avtomobil va davlat raqami to‘liq ko‘rinsin.'),
    'car_left': ('10', 'Avtomobil', 'Chap tomoni', 'Avtomobilni butun uzunligi bo‘yicha suratga oling.'),
    'car_right': ('11', 'Avtomobil', 'O‘ng tomoni', 'Avtomobilni butun uzunligi bo‘yicha suratga oling.'),
}


def generated_directory():
    root = Path(os.environ.get('PERSIST_DIR') or Path(__file__).resolve().parent.parent)
    return root / 'visual_templates'


def create_instruction_cards():
    from PIL import Image, ImageDraw, ImageFont
    directory = generated_directory()
    directory.mkdir(parents=True, exist_ok=True)
    def font(size):
        try:
            return ImageFont.truetype('DejaVuSans.ttf', size)
        except OSError:
            return ImageFont.load_default(size=size)
    for name, (step, title, side, tip) in CARDS.items():
        image = Image.new('RGB', (1280, 800), '#091a2b')
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((48, 48, 1232, 752), radius=30, outline='#294153', width=2)
        draw.text((85, 85), 'WB LEGENDA', font=font(28), fill='#e4cc8c')
        draw.text((1060, 80), f'{step}/11', font=font(28), fill='#8ba6b8')
        draw.text((85, 158), title, font=font(42), fill='#f5f5f0')
        draw.text((85, 220), side, font=font(26), fill='#64d5bd')
        # Abstract framing guides: no portrait, real document, signature or ID data.
        draw.rounded_rectangle((330, 300, 950, 530), radius=26, fill='#142e40', outline='#64d5bd', width=3)
        for x,y,dx,dy in ((300,280,1,1),(980,280,-1,1),(300,550,1,-1),(980,550,-1,-1)):
            draw.line((x,y,x+45*dx,y), fill='#e4cc8c',width=5)
            draw.line((x,y,x,y+40*dy), fill='#e4cc8c',width=5)
        label = 'TO‘LIQ KADR' if not name.startswith('car_') else 'AVTOMOBIL TO‘LIQ KADRDA'
        draw.text((390 if not name.startswith('car_') else 375,385),label,font=font(25),fill='#8ba6b8')
        draw.text((85, 615), tip, font=font(25), fill='#f5f5f0')
        draw.text((85, 674), 'Tiniq tasvir  /  Filtrsiz  /  Yaltirashsiz', font=font(21), fill='#8ba6b8')
        image.save(directory / f'{name}.png', optimize=True)
    return directory
