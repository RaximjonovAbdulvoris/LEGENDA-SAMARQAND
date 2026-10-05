import os
import runpy
import unittest
from unittest.mock import patch

from bot.counter import next_index


class AndijonGroupConfigTests(unittest.TestCase):
    def test_uses_all_four_groups(self):
        values = {
            "TELEGRAM_BOT_TOKEN": "123:offline-test",
            "DRIVER_GROUP_1": "-101",
            "DRIVER_GROUP_2": "-102",
            "DRIVER_GROUP_3": "-103",
            "DRIVER_GROUP_4": "-104",
            "BRAND_GROUP": "-105",
        }
        with patch.dict(os.environ, values, clear=True):
            groups = runpy.run_module("bot.config")["DRIVER_GROUPS"]
        self.assertEqual(groups, ["-101", "-102", "-103", "-104"])
        with patch("bot.counter._counters", {}):
            destinations = [
                groups[next_index("driver_group_rr:andijon", len(groups))]
                for _ in range(8)
            ]
        self.assertEqual(destinations, ["-101", "-102", "-103", "-104"] * 2)

    def test_third_and_fourth_settings_are_not_required(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:offline-test",
            "DRIVER_GROUP_1": "-101",
            "DRIVER_GROUP_2": "-102",
            "BRAND_GROUP": "-105",
        }, clear=True):
            self.assertEqual(
                runpy.run_module("bot.config")["DRIVER_GROUPS"], ["-101", "-102", "", ""],
            )