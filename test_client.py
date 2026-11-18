import json
import tempfile
import unittest
from pathlib import Path

from client import (
    conversation_display_name,
    conversation_key,
    list_wallpapers,
    load_wallpaper_preferences,
    nickname_settings_path,
    parse_server_address,
    save_wallpaper_preferences,
)


class ServerAddressTests(unittest.TestCase):
    def test_strips_host_and_parses_port(self) -> None:
        self.assertEqual(parse_server_address(" 192.168.1.10 ", "5050"), ("192.168.1.10", 5050))

    def test_rejects_empty_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "IP hoặc tên máy chủ"):
            parse_server_address(" ", "5050")

    def test_rejects_invalid_port(self) -> None:
        for port in ("abc", "0", "65536"):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "Cổng"):
                parse_server_address("127.0.0.1", port)


class WallpaperPreferenceTests(unittest.TestCase):
    def test_conversation_key_is_independent_of_participant_order(self) -> None:
        self.assertEqual(conversation_key(7, 12), conversation_key(12, 7))

    def test_nickname_replaces_contact_name_only_for_its_conversation(self) -> None:
        contact = {"id": 12, "display_name": "Tên thật"}

        self.assertEqual(
            conversation_display_name(contact, 7, {"7:12": "Biệt danh"}),
            "Biệt danh",
        )
        self.assertEqual(
            conversation_display_name(contact, 8, {"7:12": "Biệt danh"}),
            "Tên thật",
        )
        self.assertEqual(conversation_display_name(contact, 7, {}), "Tên thật")

    def test_nickname_settings_use_a_separate_persistent_file(self) -> None:
        self.assertEqual(nickname_settings_path().name, "chat_nicknames.json")

    def test_preferences_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings" / "chat_backgrounds.json"
            preferences = {"7:12": "hình nền.png"}

            save_wallpaper_preferences(path, preferences)

            self.assertEqual(load_wallpaper_preferences(path), preferences)

    def test_missing_preferences_start_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "missing.json"

            self.assertEqual(load_wallpaper_preferences(path), {})

    def test_rejects_preferences_with_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            path.write_text(json.dumps({"7:12": None}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "không đúng định dạng"):
                load_wallpaper_preferences(path)


class WallpaperAssetTests(unittest.TestCase):
    def test_lists_supported_image_files_in_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for filename in ("b.PNG", "a.jpg", "notes.txt"):
                (directory / filename).write_bytes(b"asset")

            self.assertEqual(
                [path.name for path in list_wallpapers(directory)],
                ["a.jpg", "b.PNG"],
            )

    def test_missing_wallpaper_directory_has_no_choices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_directory = Path(temporary_directory) / "missing"

            self.assertEqual(list_wallpapers(missing_directory), [])


if __name__ == "__main__":
    unittest.main()
