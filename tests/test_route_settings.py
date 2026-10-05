import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from bot.configure import main
from bot.route_settings import ANDIJON_KEYS, TASHKENT_KEYS, destination, read_settings, settings_path


class RouteSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"PERSIST_DIR": self.directory.name}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.directory.cleanup)

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return main(list(args))

    def configure(self, city="tashkent"):
        return self.run_cli(city, "--driver1=-101", "--driver2=-102", "--driver3=-103",
                            "--driver4=-104", "--brand=-105", "--archive=-106")

    def test_configure_both_cities_without_token(self):
        for city, keys in (("tashkent", TASHKENT_KEYS), ("andijon", ANDIJON_KEYS)):
            self.assertEqual(self.configure(city), 0)
            for i in range(1, 5):
                self.assertEqual(destination(keys[f"driver{i}"]), str(-100-i))
        self.assertEqual(len(read_settings()), 12)
        self.assertEqual(self.run_cli("show"), 0)

    def test_local_values_override_environment_and_keep_other_city(self):
        self.configure()
        self.configure("andijon")
        self.assertEqual(self.run_cli("andijon", "--driver4=-200"), 0)
        with patch.dict(os.environ, {"ANDIJON_DRIVER_GROUP_4": "-999"}):
            self.assertEqual(destination("ANDIJON_DRIVER_GROUP_4"), "-200")
        self.assertEqual(destination("TASHKENT_DRIVER_GROUP_4"), "-104")

    def test_all_four_driver_routes_required_by_configuration(self):
        self.assertEqual(self.run_cli("andijon", "--driver1=-101", "--driver2=-102",
                                     "--brand=-105", "--archive=-106"), 1)
        self.assertFalse(settings_path().exists())
        self.configure()
        before = settings_path().read_bytes()
        self.assertEqual(self.run_cli("tashkent", "--driver4=none"), 1)
        self.assertEqual(settings_path().read_bytes(), before)

    def test_interactive_setup_and_edit(self):
        with patch("builtins.input", side_effect=["-101", "-102", "-103", "-104", "-105", "-106"]):
            self.assertEqual(self.run_cli("andijon"), 0)
        with patch("builtins.input", side_effect=["", "", "", "-204", "", ""]):
            self.assertEqual(self.run_cli("andijon"), 0)
        self.assertEqual(destination("ANDIJON_DRIVER_GROUP_4"), "-204")

    def test_broken_file_and_unknown_keys_fail_explicitly(self):
        for content in ('{broken', '{"TELEGRAM_BOT_TOKEN":"not-a-token"}'):
            settings_path().write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                read_settings()

    def test_retired_spectre_routes_are_ignored(self):
        settings_path().write_text('{"NAMANGAN_SPECTRE_GROUP":"-100", "TASHKENT_SPECTRE_GROUP":"-200", "TASHKENT_DRIVER_GROUP_1":"-101"}', encoding="utf-8")
        self.assertEqual(read_settings(), {"TASHKENT_DRIVER_GROUP_1": "-101"})

    def test_checks_fail_cleanly_without_routes_or_token(self):
        self.assertEqual(self.run_cli("check", "--scope", "all"), 1)
        self.configure()
        self.configure("andijon")
        self.assertEqual(self.run_cli("check", "--scope", "all"), 1)

    def test_old_disabled_second_route_does_not_break_upgrade(self):
        settings_path().write_text('{"TASHKENT_DRIVER_GROUP_1":"-101", "TASHKENT_DRIVER_GROUP_2":""}', encoding="utf-8")
        self.assertEqual(destination("TASHKENT_DRIVER_GROUP_2"), "")
        self.assertEqual(self.run_cli("tashkent", "--driver2=-102", "--driver3=-103", "--driver4=-104", "--brand=-105", "--archive=-106"), 0)
