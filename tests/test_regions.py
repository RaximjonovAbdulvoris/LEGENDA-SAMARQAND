"""Offline coverage: no Telegram requests and no real application data."""
import os
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

for key in ("TELEGRAM_BOT_TOKEN", "DRIVER_GROUP_1", "DRIVER_GROUP_2",
            "DRIVER_GROUP_3", "DRIVER_GROUP_4", "BRAND_GROUP"):
    os.environ.setdefault(key, "123:test" if key == "TELEGRAM_BOT_TOKEN" else "-1001")

from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from bot import regions, subscription
from bot.handlers import brand, driver, start
from bot.main import build_application_conversation, intercept_pending_reply


def context(region=None):
    return N(
        user_data={"region": region} if region else {},
        bot_data={},
        bot=N(
            get_chat_member=AsyncMock(return_value=N(status="member")),
            send_message=AsyncMock(return_value=N(message_id=90)),
            send_media_group=AsyncMock(return_value=[N(message_id=80), N(message_id=81)]),
        ),
    )


def update(text=""):
    message = N(
        text=text, message_id=1, reply_text=AsyncMock(), reply_photo=AsyncMock(),
        contact=None,
    )
    return N(
        message=message, effective_message=message, callback_query=None,
        effective_chat=N(id=123, type="private"),
        effective_user=N(id=123, full_name="Test Applicant", username="test_applicant"),
    )


def callback(data):
    result = update()
    result.callback_query = N(
        data=data, message=result.message, answer=AsyncMock(),
        edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock(),
    )
    return result


class RegionalTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_welcome_text_and_region_buttons(self):
        ctx = context()
        msg = update("/start")
        await start.start(msg, ctx)
        calls = msg.effective_message.reply_text.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0],
            "• WB HUMO TAXI • xush kelibsiz\n\n"
            "“WB HUMO TAXI” ga ulanish va avtomobilni brendlash uchun shu botga ariza qoldiring!\n\n"
            "Avval ishlamoqchi bo’lgan shahringizni tanlang!")
        self.assertTrue(calls[0].kwargs["reply_markup"].remove_keyboard)
        self.assertEqual(calls[1].args[0], "Qaysi hududda ishlamoqchisiz?")
        buttons = calls[1].kwargs["reply_markup"].inline_keyboard
        self.assertEqual(
            [button.text for row in buttons for button in row],
            ["Toshkent shahri", "Andijon shahri"],
        )
        nonce = ctx.user_data["region_choice_nonce"]
        self.assertEqual(
            [button.callback_data for row in buttons for button in row],
            [f"region:pick:{region}:{nonce}" for region in regions.REGION_NAMES],
        )

    async def test_region_must_be_confirmed_and_old_buttons_cannot_change_it(self):
        ctx = context()
        await start.start(update(), ctx)
        nonce = ctx.user_data["region_choice_nonce"]
        await start.on_region_choice(callback(f"region:pick:tashkent:{nonce}"), ctx)
        self.assertIsNone(regions.get_region(ctx))
        await start.on_region_choice(callback(f"region:confirm:tashkent:{nonce}"), ctx)
        self.assertEqual(regions.get_region(ctx), "tashkent")
        await start.on_region_choice(callback(f"region:confirm:andijon:{nonce}"), ctx)
        self.assertEqual(regions.get_region(ctx), "tashkent")

    @patch.object(subscription, "ANDIJON_REQUIRED_CHATS", (("@WB_HUMO_TAXI", "https://t.me/WB_HUMO_TAXI", "Kanal"), ("@test_andijon", "https://t.me/test_andijon", "Guruh")))
    @patch.object(start, "ANDIJON_REQUIRED_CHATS", (("@WB_HUMO_TAXI", "https://t.me/WB_HUMO_TAXI", "Kanal"), ("@test_andijon", "https://t.me/test_andijon", "Guruh")))
    async def test_andijon_requires_channel_and_group_before_menu(self):
        ctx = context()
        await start.start(update(), ctx)
        nonce = ctx.user_data["region_choice_nonce"]
        await start.on_region_choice(
            callback(f"region:pick:andijon:{nonce}"), ctx
        )
        await start.on_region_choice(
            callback(f"region:confirm:andijon:{nonce}"), ctx
        )

        self.assertEqual(ctx.user_data["andijon_subscription_nonce"], nonce)
        ctx.bot.get_chat_member.assert_not_awaited()

        ctx.bot.get_chat_member.reset_mock()
        ctx.bot.get_chat_member.side_effect = [
            N(status="member"),
            N(status="left"),
        ]
        check = callback(f"region:andijon:subscription:{nonce}")
        await start.on_region_choice(check, ctx)
        self.assertEqual(
            [call.kwargs["chat_id"] for call in ctx.bot.get_chat_member.call_args_list],
            [subscription.REQUIRED_CHANNEL, "@test_andijon"],
        )
        check.callback_query.answer.assert_awaited()
        self.assertIn("obuna", check.callback_query.answer.await_args.args[0])

    @patch.object(start, "ANDIJON_REQUIRED_CHATS", (("@WB_HUMO_TAXI", "https://t.me/WB_HUMO_TAXI", "Kanal"), ("@test_andijon", "https://t.me/test_andijon", "Guruh")))
    async def test_andijon_subscription_check_opens_menu_after_both_joined(self):
        ctx = context()
        await start.start(update(), ctx)
        nonce = ctx.user_data["region_choice_nonce"]
        await start.on_region_choice(
            callback(f"region:pick:andijon:{nonce}"), ctx
        )
        await start.on_region_choice(
            callback(f"region:confirm:andijon:{nonce}"), ctx
        )

        check = callback(f"region:andijon:subscription:{nonce}")
        await start.on_region_choice(check, ctx)

        self.assertEqual(
            [call.kwargs["chat_id"] for call in ctx.bot.get_chat_member.call_args_list],
            [subscription.REQUIRED_CHANNEL, "@test_andijon"],
        )
        check.callback_query.edit_message_reply_markup.assert_awaited_once_with(
            reply_markup=None
        )
        self.assertIn(
            "Andijon shahri",
            check.message.reply_text.await_args.args[0],
        )

    async def test_back_and_switch_discard_unconfirmed_or_partial_form(self):
        ctx = context("andijon")
        ctx.user_data["name"] = "Old form"
        await start.start(update(), ctx)
        self.assertNotIn("name", ctx.user_data)
        self.assertIsNone(regions.get_region(ctx))
        nonce = ctx.user_data["region_choice_nonce"]
        await start.on_region_choice(callback(f"region:pick:tashkent:{nonce}"), ctx)
        await start.on_region_choice(callback(f"region:back:tashkent:{nonce}"), ctx)
        await start.on_region_choice(callback(f"region:confirm:tashkent:{nonce}"), ctx)
        self.assertIsNone(regions.get_region(ctx))

    async def test_both_city_menus_have_all_sections(self):
        def buttons(region):
            return [button.text for row in start.main_keyboard(region).keyboard for button in row]
        for city in ("andijon", "tashkent"):
            self.assertEqual(buttons(city), [
                start.MENU_DRIVER, start.MENU_BRAND,
                start.MENU_CONTACT, start.MENU_OFFICE, start.MENU_REGION,
            ])
            msg = update()
            await start.show_menu(msg, context(city))
            text = msg.message.reply_text.call_args.args[0]
            self.assertNotIn("filial", text)
            self.assertIn(regions.region_name(city), text)
            self.assertNotIn("Spectre", text)

    async def test_tashkent_office_and_contact_are_city_specific(self):
        msg = update()
        msg.message.reply_photo.return_value = N(photo=[N(file_id="tashkent-office")])
        await start.show_office(msg, context("tashkent"))
        msg.message.reply_text.assert_not_awaited()
        office = msg.message.reply_photo.call_args
        self.assertEqual(office.kwargs["photo"].name, str(start.TASHKENT_OFFICE_PHOTO))
        self.assertIn("Toshkent shahri", office.kwargs["caption"])
        self.assertIn("Mirzo Ulug‘bek tumani", office.kwargs["caption"])
        self.assertIn("Traktorsozlar shaharchasi massivi, 1-mavze, 39-uy", office.kwargs["caption"])
        self.assertIn("TTZ diadora", office.kwargs["caption"])
        self.assertEqual(office.kwargs["reply_markup"].inline_keyboard[0][0].url,
                         "https://yandex.uz/maps/-/CTxxiJ5~")
        await start.show_contact(msg, context("tashkent"))
        contact = msg.message.reply_text.call_args.args[0]
        self.assertIn("+998 78 113-80-81", contact)
        self.assertIn("Toshkent shahri", contact)
        self.assertNotIn("@humo_Andijon", contact)

    async def test_city_confirmation_has_no_branch_wording(self):
        for city in ("andijon", "tashkent"):
            ctx = context()
            await start.start(update(), ctx)
            msg = callback(f"region:pick:{city}:{ctx.user_data['region_choice_nonce']}")
            await start.on_region_choice(msg, ctx)
            text = msg.callback_query.edit_message_text.call_args.args[0]
            self.assertIn(regions.region_name(city), text)
            self.assertNotIn("filial", text)

    async def test_all_forms_need_confirmed_region(self):
        for handler in (driver.start_driver, brand.start_brand):
            ctx = context()
            result = await handler(update(), ctx)
            self.assertEqual(result, ConversationHandler.END)
            ctx.bot.get_chat_member.assert_not_awaited()
            ctx.bot.send_message.assert_not_awaited()

    async def test_missing_tashkent_routes_do_not_collect_or_send(self):
        with patch.dict(os.environ, {
            "TASHKENT_DRIVER_GROUP_1": "", "TASHKENT_DRIVER_GROUP_2": "",
            "TASHKENT_BRAND_GROUP": "", "TASHKENT_SPECTRE_GROUP": "",
        }):
            for handler in (driver.start_driver, brand.start_brand):
                ctx = context("tashkent")
                self.assertEqual(await handler(update(), ctx), ConversationHandler.END)
                ctx.bot.send_message.assert_not_awaited()
                ctx.bot.get_chat_member.assert_not_awaited()
                self.assertEqual(regions.get_region(ctx), "tashkent")

    async def test_shared_membership_gate_fails_closed(self):
        ctx = context("tashkent")
        ctx.bot.get_chat_member.return_value = N(status="left")
        self.assertFalse(await subscription.require_subscription(update(), ctx, callback_data="test"))
        ctx.bot.get_chat_member.side_effect = TelegramError("unavailable")
        self.assertFalse(await subscription.require_subscription(update(), ctx, callback_data="test"))
        self.assertEqual(ctx.bot.get_chat_member.call_args.kwargs["chat_id"], "@WB_HUMO_TAXI")

    async def test_brand_full_question_flow_routes_by_region(self):
        destinations = {("andijon", "brand"): "-201", ("tashkent", "brand"): "-202"}
        with patch("bot.regions.BRAND_GROUP", "-201"), patch.dict(os.environ, {
            "ANDIJON_BRAND_GROUP": "-201", "TASHKENT_BRAND_GROUP": "-202",
            "ANDIJON_SPECTRE_GROUP": "-204",
        }):
            for (region, kind), destination in destinations.items():
                ctx = context(region)
                result = await brand.start_brand(update(), ctx)
                if kind == "brand":
                    self.assertEqual(result, brand.BRAND_WARN)
                    await brand.brand_warn(update(), ctx)
                else:
                    self.assertEqual(result, brand.BRAND_NAME)
                for handler, answer in (
                    (brand.brand_get_name, "Test Applicant"),
                    (brand.brand_get_phone, "+998901234567"),
                    (brand.brand_get_model, "Cobalt"),
                    (brand.brand_get_year, "2020"),
                    (brand.brand_get_color, "Oq"),
                    (brand.brand_get_plate, "01A123BC"),
                ):
                    await handler(update(answer), ctx)
                self.assertEqual(str(ctx.bot.send_message.call_args.kwargs["chat_id"]), destination)
                self.assertIsNone(ctx.bot.send_message.call_args.kwargs["reply_markup"])
                self.assertFalse(ctx.bot_data.get("applications"))
                self.assertFalse(ctx.bot_data.get("app_messages"))
                self.assertEqual(ctx.user_data, {"region": region})

    async def test_driver_routes_and_registers_separate_branch(self):
        with patch("bot.regions.DRIVER_GROUPS", ["-301"]), patch.dict(os.environ, {
            "ANDIJON_DRIVER_GROUP_1": "-301", "TASHKENT_DRIVER_GROUP_1": "-302", "TASHKENT_DRIVER_GROUP_2": "",
        }), patch("bot.handlers.driver.next_index", return_value=0):
            for region, destination in (("andijon", "-301"), ("tashkent", "-302")):
                ctx = context(region)
                self.assertEqual(await driver.start_driver(update(), ctx), driver.NAME)
                ctx.user_data.update({
                    "name": "Test", "phone": "+998901234567", "car_plate": "01A123BC",
                    "user_id": 123, "passport_front": "photo1", "passport_back": "photo2",
                    "selfie": "photo3", "litsenziya": "photo4",
                })
                await driver._send_to_driver_group(ctx)
                self.assertEqual(str(ctx.bot.send_message.call_args.kwargs["chat_id"]), destination)
                self.assertTrue(all(str(call.kwargs["chat_id"]) == destination
                                    for call in ctx.bot.send_media_group.call_args_list))
                record = next(iter(ctx.bot_data["applications"].values()))
                self.assertEqual(record["region"], region)

    async def test_cancel_keeps_region_but_clears_form(self):
        ctx = context("tashkent")
        ctx.user_data["b_name"] = "partial"
        await start.cancel(update(), ctx)
        self.assertEqual(ctx.user_data, {"region": "tashkent"})

    async def test_pending_reply_does_not_also_become_form_answer(self):
        ctx = context("tashkent")
        ctx.bot_data["pending_user_replies"] = {123: "test"}
        with patch("bot.main.on_user_reply_message", new_callable=AsyncMock) as reply:
            with self.assertRaises(ApplicationHandlerStop):
                await intercept_pending_reply(update("My reply"), ctx)
            reply.assert_awaited_once()
        await intercept_pending_reply(update(start.MENU_REGION), ctx)
        self.assertNotIn(123, ctx.bot_data["pending_user_replies"])

    def test_single_conversation_has_both_nonoverlapping_state_sets(self):
        conversation = build_application_conversation()
        self.assertIn(driver.NAME, conversation.states)
        self.assertIn(brand.BRAND_NAME, conversation.states)
        self.assertTrue(conversation.allow_reentry)


if __name__ == "__main__":
    unittest.main()
class FourGroupRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_city_rotates_through_four_groups_independently(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "PERSIST_DIR": directory,
            **{f"ANDIJON_DRIVER_GROUP_{i}": str(-100-i) for i in range(1, 5)},
            **{f"TASHKENT_DRIVER_GROUP_{i}": str(-200-i) for i in range(1, 5)},
        }), patch("bot.counter._counters", {}):
            sent = {"andijon": [], "tashkent": []}
            for _ in range(8):
                for city in sent:
                    ctx = context(city)
                    ctx.user_data.update({"_application_region":city, "name":"Test", "phone":"+998901234567", "car_plate":"01A123BC", "user_id":123, "passport_front":"photo1", "selfie":"photo2"})
                    await driver._send_to_driver_group(ctx)
                    group = str(ctx.bot.send_message.await_args.kwargs["chat_id"])
                    sent[city].append(group)
                    for call in ctx.bot.send_media_group.await_args_list:
                        self.assertEqual(str(call.kwargs["chat_id"]), group)
            self.assertEqual(sent["andijon"], ["-101", "-102", "-103", "-104"] * 2)
            self.assertEqual(sent["tashkent"], ["-201", "-202", "-203", "-204"] * 2)

    def test_spectre_cannot_start_or_route_in_either_city(self):
        self.assertFalse(hasattr(brand, "start_spectre"))
        self.assertEqual(len(brand.build_brand_conversation().entry_points), 1)
        for city in regions.REGION_NAMES:
            self.assertEqual(regions.application_group(city, "spectre"), "")
            self.assertTrue(all("Spectre" not in button.text for row in start.main_keyboard(city).keyboard for button in row))

    async def test_andijon_does_not_display_old_office_or_contact(self):
        msg = update()
        await start.show_office(msg, context("andijon"))
        msg.message.reply_photo.assert_not_awaited()
        self.assertNotIn("Zarkan", msg.message.reply_text.await_args.args[0])
        await start.show_contact(msg, context("andijon"))
        self.assertNotIn("humo_Namangan", msg.message.reply_text.await_args.args[0])
