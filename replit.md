# Telegram taxi bot — LEGENDA-SAMARQAND

The bot offers Toshkent and Andijon regional menus. Namangan has been replaced
by Andijon. Driver connection applications rotate through up to four configured
operator groups independently per city: 1 → 2 → 3 → 4 → 1. Each application goes
to one group with its documents and operator controls. Brand applications keep
their separate destination. Spectre applications and menu entries are removed.
Historical Spectre records retain cleanup safeguards only.

## Installation and startup

```bash
python -m pip install -r requirements.txt
python -m bot.main
```

Set `TELEGRAM_BOT_TOKEN` in the server environment. Do not commit credentials.
`PERSIST_DIR` controls the location of `bot_persistence.pkl` and `bot_routes.json`.

## Routing

Set all four IDs per city:

- `TASHKENT_DRIVER_GROUP_1` through `TASHKENT_DRIVER_GROUP_4`
- `ANDIJON_DRIVER_GROUP_1` through `ANDIJON_DRIVER_GROUP_4`
- `TASHKENT_BRAND_GROUP`, `ANDIJON_BRAND_GROUP`
- `TASHKENT_ARCHIVE_GROUP`, `ANDIJON_ARCHIVE_GROUP`

Legacy `DRIVER_GROUP_1` through `DRIVER_GROUP_4`, `BRAND_GROUP`, and
`ARCHIVE_GROUP` can still supply Andijon values. Explicit Andijon values take
precedence. A missing city route never falls back to the other city. The runtime
uses only configured driver groups; the CLI requires all four for complete setup.

Configure interactively on the server:

```bash
python -m bot.configure tashkent
python -m bot.configure andijon
python -m bot.configure show
python -m bot.configure check --scope all
```

Each setup asks for driver groups 1–4, brand, and archive. Saved local overrides
take precedence over environment variables. Enter keeps the existing value.
The old Spectre settings are ignored. The bot needs administrator permissions
in operator groups, including deleting messages when archiving applications.

## Offices and contacts

Toshkent: the supplied LEGENDA office photograph (`office_tashkent.png`),
Chilonzor 8-kvartal, 1-dom, Qatortol bekati. No map link is shown until the
client supplies the Toshkent link.

Andijon: text and Google Maps link, landmark Zalatoy Dolina hotel. No office
photograph is shown. Contact: +998781505050 and @wblegendaandijonadmin.

Toshkent contact: +998781505050, +998931354484, @WBLEGENDATAXI.
Both cities show @legendapulbot, @WBLEGENDA_KANAL and @WB_LEGENDA_TAXI.
These client-supplied details are part of the code; older placeholder contact
and office environment settings no longer override them.

There is no mandatory subscription in either region. City confirmation opens
the menu immediately; driver and brand forms make no membership requests.

## Deploying changes

A GitHub update does not restart a separately hosted process. Pull the latest
code in the deployed checkout, configure the new group IDs, and restart that
server's bot process. Use the client's own bot token and persistence directory.

## Offline verification

```bash
python -m compileall -q bot
python -m unittest discover -s tests -q
```
