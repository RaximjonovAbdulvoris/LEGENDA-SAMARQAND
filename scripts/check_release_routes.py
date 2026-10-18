"""Read-only, offline check for distinct LEGENDA regional destinations."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.regions import ANDIJON, TASHKENT, driver_groups, application_group, archive_group
from bot.route_settings import destination


def problems():
    errors, owners = [], {}
    for city in (TASHKENT, ANDIJON):
        groups = driver_groups(city)
        if not groups:
            errors.append(f'{city}: ariza guruhi sozlanmagan')
        routes = {f'ariza {i}': group for i, group in enumerate(groups, 1)}
        routes.update({
            'brend': application_group(city, 'brand'),
            'arxiv': archive_group(city),
            'chala': destination(f'{city.upper()}_INCOMPLETE_GROUP'),
            'boshqa park': destination(f'{city.upper()}_OTHER_PARK_GROUP'),
        })
        spectre = application_group(city, "spectre")
        if spectre:
            routes["spectre"] = spectre
        for kind, value in routes.items():
            label = f'{city} / {kind}'
            if not re.fullmatch(r'-[1-9][0-9]*', str(value)):
                errors.append(f'{label}: guruh ID sozlanmagan')
                continue
            if str(value) in owners:
                errors.append(f'{label}: {owners[str(value)]} bilan bir xil guruh')
            else:
                owners[str(value)] = label
    return errors


if __name__ == '__main__':
    try:
        errors = problems()
    except (ValueError, OSError):
        print('Guruh sozlamalari o‘qilmadi; bot_routes.json faylini tekshiring.')
        raise SystemExit(1)
    for error in errors:
        print(error)
    if not errors:
        print('Toshkent va Andijon yo‘nalishlari to‘liq va alohida. Telegram ruxsatlarini ham tekshiring.')
    raise SystemExit(bool(errors))
