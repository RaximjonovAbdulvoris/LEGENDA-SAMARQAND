# LEGENDA lokal yangilanishi

Asos: `adapt-andijon-four-groups`, `0e1cba8` (serverdagi `4717ae5` ning davomi).
Ishchi branch: `local-legenda-upgrade`; GitHubdagi nishon branch: `adapt-andijon-four-groups`. Serverga deploy alohida bajariladi.

## Qo‘shilgan imkoniyatlar

- Prava old tomonidan familiya (1), ism (2), tug‘ilgan joy/sana (3), PINFL (4d), prava raqami va amal qilish muddati o‘qiladi. Ism tagidagi otasining ismi qo‘shilmaydi.
- Texpasport seriyasi old/orqa rasmlardan tekshiriladi. Zid o‘qishlar tayyor qiymat sifatida ko‘rsatilmaydi.
- OCR serverning o‘zida, tokenlar uzatilmaydigan va tarmoqqa chiqishi cheklangan alohida jarayonda ishlaydi. Tashqi AI API kerak emas.
- Ariza avval operatorga yuboriladi, keyin shu xabardagi ma’lumotlar OCR bilan yangilanadi. O‘qish xatosi arizani yo‘qotmaydi; navbat va qayta urinishlar mavjud.
- Har arizaga `LG-000001` shaklida raqam; operator maydonlari nusxalash uchun alohida ko‘rsatiladi. Telefon `998` dan boshlanadi.
- Menyu va operator tugmalariga yangi uslub va mos premium emoji qo‘llovi. Ruxsat bo‘lmasa oddiy ko‘rinishga qaytadi.
- ZIP ichida haydovchi nomli papka va shu arizaning rasmlari. Yuklash alohida Telegram fayli yuboradi; qurilmaning Downloads sozlamasini bot boshqarmaydi.
- Tayyor, qaytariladigan Jarayonda, O‘chirish, Chala rasm va Boshqa park amallari. Amallar guruh administratorlari bilan cheklangan.
- Chala/Boshqa parkka ko‘chirilgach haydovchiga tegishli xabar yuboriladi. Yangi guruhda faqat Tayyor, O‘chirish, Jarayonda qoladi. Tayyor shu hudud arxiviga o‘tkazadi.
- Nusxa tasdiqlanmasdan asl xabarlar va ZIP o‘chirilmaydi. Ko‘chirish muvaffaqiyatli bo‘lgach eski guruhdagi ariza ZIP xabarlari ham o‘chiriladi.
- Eski operator xabarlarini yangilash botning xabar qabul qilishini kutdirib qo‘ymaydi. Form holati va bot yozuvlari diskka saqlanadi, shifrlash kaliti serverda qoladi.

## Saqlangan LEGENDA ma’lumotlari

Toshkent/Andijon, har hudud uchun 4 tagacha ariza guruhi, alohida arxiv va Brend yo‘nalishi; ofislar, telefonlar, administratorlar, pul yechish va ijtimoiy tarmoq havolalari; mavjud asl shablon rasmlari. `office_tashkent.png` almashtirilmagan.

Brend menyusi va formasi qoladi. Ariza Brend guruhiga tugmasiz oddiy xabar sifatida tushadi. Tasdiqlash/rad etish tugmalari hamda Google Sheets integratsiyasi qo‘shilmagan. Spectre va majburiy obuna qo‘shilmagan.

## Keyingi sozlash

Yangi guruhlar uchun:

| Hudud | Sozlama |
|---|---|
| Toshkent | `TASHKENT_INCOMPLETE_GROUP`, `TASHKENT_OTHER_PARK_GROUP` |
| Andijon | `ANDIJON_INCOMPLETE_GROUP`, `ANDIJON_OTHER_PARK_GROUP` |

Qolgan ariza, Brend va arxiv IDlari serverdagi mavjud sozlamalardan olinadi; o‘zgarsa, yangilari kerak.

Guruhlar `bot_routes.json` yoki muhit sozlamalaridan olinadi. Harakatlar oldidan hudud tekshiriladi. Bir hudud guruhi yetishmasa, boshqasiga avtomatik yuborilmaydi. `scripts/check_release_routes.py` yetishmagan yoki takrorlangan yo‘nalishlarni ko‘rsatadi; `python -m bot.configure check --scope all` Telegram ruxsatlarini tekshiradi.

Deploydan oldin faqat LEGENDA servisini to‘xtatib, shu botning `.env`, `bot_routes.json` (bo‘lsa), `bot_persistence.pkl`, uning `.key` fayli (bo‘lsa) va `office_tashkent.png` nusxalari olinadi. Keyin shu botning `.venv` muhitiga `requirements.txt` va `requirements-ocr.txt` o‘rnatilib, `libseccomp.so.2` mavjudligi va `scripts/check_neural_ocr.py` tekshiriladi. HUMOBOTning tokeni, yozuvlari yoki ovoz file IDsi ko‘chirilmaydi.

## Tekshiruv va chegaralar

Natija: 230 ta avtomatik test o‘tdi. Lokal OCR worker sun’iy hujjatni 1,63 soniyada o‘qidi; bu serverdagi barcha haqiqiy rasmlar uchun tezlik va aniqlik kafolati emas.

Lokal avtomatik testlar OCR, hudud yo‘nalishlari, albom ko‘chirish, qayta urinish, eski yozuvlar, premium emoji, ZIP va Brendning oddiy xabar oqimini qamrab oladi. Sun’iy hujjat bilan haqiqiy OCR worker ham tekshirildi. Telegramdagi haqiqiy guruh huquqlari va server tezligi deploydan oldin tekshirilishi kerak.

OCRning barcha rasmda xatosiz ishlashi kafolatlanmaydi; noaniq qiymatlar operatorga tekshirish uchun belgilanadi. Eski yozuvda Telegram rasm file IDlari saqlanmagan bo‘lsa, shu eski ariza uchun ZIP tuzib bo‘lmaydi. Bu cheklov yangi arizalarga taalluqli emas.

Shifrlangan holatdan eski kodga qaytishda yangilanishdan oldingi yozuvlar nusxasini tiklash kerak; shifrlangan faylni eski PicklePersistence bilan ochib bo‘lmaydi.
