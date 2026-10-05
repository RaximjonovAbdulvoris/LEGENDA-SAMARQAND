import os
import unittest
from itertools import product
from types import SimpleNamespace

for _key, _value in {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "DRIVER_GROUP_1": "11",
    "DRIVER_GROUP_2": "12",
    "BRAND_GROUP": "21",
}.items():
    os.environ.setdefault(_key, _value)

from bot.handlers import operator


class Message:
    def __init__(self, message_id=1, media_group_id=None):
        self.message_id = message_id
        self.media_group_id = media_group_id
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return Message(self.message_id + 100)


class Query:
    def __init__(self, action, message):
        self.data = action
        self.message = message
        self.markup_removed = False
        self.deleted = False

    async def answer(self, *args, **kwargs):
        pass

    async def edit_message_reply_markup(self, **kwargs):
        self.markup_removed = kwargs.get("reply_markup", "missing") is None

    async def delete_message(self):
        self.deleted = True


class Bot:
    def __init__(self):
        self.groups = []
        self.photos = []
        self.copies = []
        self.deletes = []
        self.sent = []
        self.fail_keyboard = 0
        self.malformed_group = None

    async def send_media_group(self, **kwargs):
        self.groups.append(kwargs)
        index = len(self.groups)
        count = len(kwargs["media"])
        if self.malformed_group == index:
            return [Message(1000, "wrong"), Message(1001, "different")][:count]
        return [Message(1000 + i, f"album-{index}") for i in range(count)]

    async def send_photo(self, **kwargs):
        self.photos.append(kwargs)
        return Message(2000)

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)
        if self.fail_keyboard:
            self.fail_keyboard -= 1
            raise RuntimeError("keyboard failed")
        return Message(3000)

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return Message(4000)

    async def delete_message(self, **kwargs):
        self.deletes.append(kwargs)


def albums(main=10, selfie=2):
    return [
        {
            "name": "main",
            "source_message_ids": list(range(21, 21 + main)),
            "photos": [
                {
                    "file_id": f"main-{i}",
                    "caption": "main caption" if i == 0 else None,
                    "parse_mode": "HTML" if i == 0 else None,
                }
                for i in range(main)
            ],
        },
        {
            "name": "selfie",
            "source_message_ids": list(range(50, 50 + selfie)),
            "photos": [
                {
                    "file_id": f"selfie-{i}",
                    "caption": "selfie caption" if i == 0 else None,
                }
                for i in range(selfie)
            ],
        },
    ]


class ArchiveAlbumTests(unittest.IsolatedAsyncioTestCase):
    def context(self, bot=None):
        return SimpleNamespace(bot=bot or Bot(), bot_data={})

    def register(self, context, *, kind="driver", media_albums=None):
        return operator.register_application(
            context,
            applicant_id=9,
            region="andijon",
            kind=kind,
            group_chat_id=100,
            photo_msg_ids=(
                [mid for album in media_albums for mid in album["source_message_ids"]]
                if media_albums is not None else list(range(21, 33))
            ),
            kb_msg_id=20,
            media_albums=media_albums,
        )

    async def test_reconstructs_main_and_selfie_albums(self):
        context = self.context()
        key = self.register(context, media_albums=albums())
        record = context.bot_data["applications"][key]

        self.assertTrue(await operator._archive_application(context.bot, record, "-300"))
        self.assertEqual([len(call["media"]) for call in context.bot.groups], [10, 2])
        self.assertEqual(context.bot.groups[0]["media"][0].caption, "main caption")
        self.assertEqual(context.bot.groups[1]["media"][0].caption, "selfie caption")
        self.assertEqual(len(context.bot.copies), 1)

    async def test_avoids_singleton_when_splitting_large_album(self):
        context = self.context()
        key = self.register(context, media_albums=albums(main=11, selfie=2))
        record = context.bot_data["applications"][key]

        self.assertTrue(await operator._archive_application(context.bot, record, "-300"))
        self.assertEqual([len(call["media"]) for call in context.bot.groups], [9, 2, 2])

    async def test_malformed_group_confirmation_blocks_source_deletion(self):
        bot = Bot()
        bot.malformed_group = 1
        context = self.context(bot)
        key = self.register(context, media_albums=albums())
        message = Message(20)
        query = Query("op:ready:9", message)
        update = SimpleNamespace(
            callback_query=query,
            effective_chat=SimpleNamespace(id=100),
            effective_user=SimpleNamespace(id=77, full_name="Operator"),
        )

        original_archive_group = operator.archive_group
        operator.archive_group = lambda region: "-300"
        try:
            await operator.on_operator_button(update, context)
        finally:
            operator.archive_group = original_archive_group

        self.assertEqual(bot.deletes, [])
        self.assertFalse(query.deleted)
        self.assertEqual(
            context.bot_data["applications"][key]["archive_state"]["albums"]["0:0"][
                "status"
            ],
            "indeterminate",
        )

    async def test_keyboard_retry_does_not_duplicate_confirmed_albums(self):
        bot = Bot()
        bot.fail_keyboard = 1
        context = self.context(bot)
        key = self.register(context, media_albums=albums())
        record = context.bot_data["applications"][key]

        self.assertFalse(await operator._archive_application(bot, record, "-300"))
        self.assertTrue(await operator._archive_application(bot, record, "-300"))
        self.assertEqual(len(bot.groups), 2)
        self.assertEqual(len(bot.copies), 2)

    async def test_incomplete_metadata_never_sends_or_allows_deletion(self):
        context = self.context()
        key = self.register(context, media_albums=albums())
        record = context.bot_data["applications"][key]
        record["photo_msg_ids"].append(999)
        self.assertFalse(await operator._archive_application(context.bot, record, "-300"))
        self.assertEqual(context.bot.groups, [])
        self.assertEqual(context.bot.copies, [])

    async def test_brand_and_spectre_legacy_callbacks_are_harmless(self):
        for kind, action in product(("brand", "spectre"), ("ready", "progress", "comment")):
            with self.subTest(kind=kind, action=action):
                bot = Bot()
                context = self.context(bot)
                context.bot_data["app_messages"] = {
                    9: {
                        "applicant_id": 9,
                        "kind": kind,
                        "region": "andijon",
                        "group_chat_id": 100,
                        "photo_msg_ids": [21],
                        "kb_msg_id": 20,
                    }
                }
                message = Message(20)
                query = Query(f"op:{action}:9", message)
                update = SimpleNamespace(
                    callback_query=query,
                    effective_chat=SimpleNamespace(id=100),
                    effective_user=SimpleNamespace(id=77, full_name="Operator"),
                )

                await operator.on_operator_button(update, context)

                self.assertTrue(query.markup_removed)
                self.assertFalse(query.deleted)
                self.assertEqual(bot.groups, [])
                self.assertEqual(bot.copies, [])
                self.assertEqual(bot.deletes, [])
                self.assertEqual(bot.sent, [])
                self.assertTrue(message.replies)

    async def test_archive_guard_rejects_brand_and_spectre(self):
        for kind in ("brand", "spectre"):
            context = self.context()
            key = self.register(context, kind=kind, media_albums=albums())
            record = context.bot_data["applications"][key]
            self.assertFalse(await operator._archive_application(context.bot, record, "-300"))
            self.assertEqual(context.bot.groups, [])


if __name__ == "__main__":
    unittest.main()