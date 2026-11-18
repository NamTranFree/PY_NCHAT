import base64
import hashlib
import unittest
from unittest.mock import MagicMock, patch

from server import (
    MAX_UPLOAD_BYTES,
    ChatServer,
    ClientConnection,
    PROFILE_VISIBILITY_DEFAULTS,
    clean_filename,
    ensure_group_message_type_column,
    ensure_profile_visibility_columns,
    verify_password,
)


class PasswordHashTests(unittest.TestCase):
    def test_verifies_password_and_rejects_wrong_password(self) -> None:
        salt = b"0123456789abcdef"
        digest = hashlib.pbkdf2_hmac("sha256", b"correct-password", salt, 1000)
        stored_hash = f"pbkdf2_sha256$1000${salt.hex()}${digest.hex()}"

        self.assertTrue(verify_password(stored_hash, "correct-password"))
        self.assertFalse(verify_password(stored_hash, "wrong-password"))

    def test_rejects_invalid_hash(self) -> None:
        self.assertFalse(verify_password("not-a-valid-hash", "password"))


class InputValidationTests(unittest.TestCase):
    def test_filename_discards_path_components(self) -> None:
        self.assertEqual(clean_filename("../../photo.png"), "photo.png")
        self.assertEqual(clean_filename(r"C:\temp\photo.png"), "photo.png")

    def test_unauthenticated_client_cannot_list_accounts(self) -> None:
        server = ChatServer()
        client = ClientConnection(sock=None, address=("127.0.0.1", 0))

        with self.assertRaisesRegex(ValueError, "Vui lòng đăng nhập trước"):
            server.dispatch(client, "users", {})

    def test_rejects_attachments_over_five_mib(self) -> None:
        server = ChatServer()
        content = base64.b64encode(b"x" * (MAX_UPLOAD_BYTES + 1)).decode("ascii")

        with self.assertRaisesRegex(ValueError, "không được vượt quá 5 MB"):
            server.send_message(
                1,
                {
                    "recipient_id": 2,
                    "body": "",
                    "attachment": {
                        "name": "large.bin",
                        "mime_type": "application/octet-stream",
                        "content": content,
                    },
                },
            )

    def test_group_message_type_migration_preserves_existing_columns(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"Field": "id"}, {"Field": "body"}]

        ensure_group_message_type_column(cursor)

        self.assertIn(
            "ALTER TABLE group_messages ADD COLUMN message_type",
            cursor.execute.call_args.args[0],
        )


class AccountDisplayNameTests(unittest.TestCase):
    def test_login_returns_saved_display_name_not_password(self) -> None:
        stored_user = {
            "id": 7,
            "username": "nam1111",
            "display_name": "Nam Tran",
            "password_hash": "stored-hash",
        }
        connection_context = MagicMock()
        connection = connection_context.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = stored_user
        client = ClientConnection(sock=None, address=("127.0.0.1", 0))

        with (
            patch("server.db_connection", return_value=connection_context),
            patch("server.verify_password", return_value=True),
            patch.object(ChatServer, "broadcast_presence"),
        ):
            result = ChatServer().login(
                client, {"username": "nam1111", "password": "secret123"}
            )

        self.assertEqual(result["display_name"], "Nam Tran")
        self.assertNotIn("password", result)

    def test_login_repairs_legacy_display_name_that_equals_password(self) -> None:
        stored_user = {
            "id": 7,
            "username": "nam1111",
            "display_name": "secret123",
            "password_hash": "stored-hash",
        }
        connection_context = MagicMock()
        cursor = connection_context.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = stored_user
        client = ClientConnection(sock=None, address=("127.0.0.1", 0))

        with (
            patch("server.db_connection", return_value=connection_context),
            patch("server.verify_password", return_value=True),
            patch.object(ChatServer, "broadcast_presence"),
        ):
            result = ChatServer().login(
                client, {"username": "nam1111", "password": "secret123"}
            )

        self.assertEqual(result["display_name"], "nam1111")
        self.assertNotIn("secret123", result.values())
        self.assertEqual(cursor.execute.call_args.args[1], ("nam1111", 7))

    def test_register_stores_display_name_separately_from_credentials(self) -> None:
        connection_context = MagicMock()
        cursor = connection_context.__enter__.return_value.cursor.return_value.__enter__.return_value

        with patch("server.db_connection", return_value=connection_context):
            ChatServer().register(
                {
                    "username": "nam1111",
                    "display_name": "Nam Tran",
                    "password": "secret123",
                }
            )

        params = cursor.execute.call_args.args[1]
        self.assertEqual(params[0], "nam1111")
        self.assertNotEqual(params[1], "secret123")
        self.assertEqual(params[2], "Nam Tran")

    def test_register_rejects_display_name_equal_to_password(self) -> None:
        with self.assertRaisesRegex(ValueError, "không được trùng với mật khẩu"):
            ChatServer().register(
                {
                    "username": "nam1111",
                    "display_name": "secret123",
                    "password": "secret123",
                }
            )

    def test_profile_rejects_display_name_equal_to_password(self) -> None:
        connection_context = MagicMock()
        cursor = connection_context.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"password_hash": "stored-hash"}

        with (
            patch("server.db_connection", return_value=connection_context),
            patch("server.verify_password", return_value=True),
        ):
            with self.assertRaisesRegex(ValueError, "không được trùng với mật khẩu"):
                ChatServer().update_profile(7, {"display_name": "secret123"})

        self.assertEqual(cursor.execute.call_count, 1)


class MessageHistoryTests(unittest.TestCase):
    def test_history_accepts_tuple_returned_by_mysql_cursor(self) -> None:
        rows = (
            {
                "id": 2,
                "sender_id": 8,
                "recipient_id": 7,
                "body": "Tin sau",
                "attachment_name": None,
                "attachment_type": None,
                "created_at": None,
            },
            {
                "id": 1,
                "sender_id": 7,
                "recipient_id": 8,
                "body": "Tin trước",
                "attachment_name": None,
                "attachment_type": None,
                "created_at": None,
            },
        )
        connection_context = MagicMock()
        cursor = connection_context.__enter__().cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = rows

        with patch("server.db_connection", return_value=connection_context):
            result = ChatServer().history(7, {"user_id": 8})

        self.assertEqual([message["id"] for message in result["messages"]], [1, 2])
        self.assertEqual(
            [message["body"] for message in result["messages"]],
            ["Tin trước", "Tin sau"],
        )

    def test_create_group_adds_creator_and_selected_members_atomically(self) -> None:
        connection_context = MagicMock()
        connection = connection_context.__enter__()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"id": 8}, {"id": 9}]
        cursor.lastrowid = 15

        with (
            patch("server.db_connection", return_value=connection_context),
            patch.object(ChatServer, "broadcast_group_event") as broadcast,
        ):
            result = ChatServer().create_group(
                7, {"name": "Bạn thân", "member_ids": [8, 9]}
            )

        self.assertEqual(
            result["group"], {"id": 15, "name": "Bạn thân", "member_count": 3}
        )
        self.assertEqual(
            cursor.executemany.call_args.args[1],
            [(15, 7), (15, 8), (15, 9)],
        )
        connection.begin.assert_called_once_with()
        connection.commit.assert_called_once_with()
        broadcast.assert_called_once_with(
            [8, 9],
            "group_created",
            {
                "group": {
                    "id": 15,
                    "name": "Bạn thân",
                    "member_count": 3,
                }
            },
        )

    def test_rename_group_records_and_broadcasts_activity(self) -> None:
        connection_context = MagicMock()
        connection = connection_context.__enter__()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"member": 1},
            {
                "id": 61,
                "group_id": 15,
                "sender_id": 7,
                "body": "Nam đã đổi tên nhóm thành “Mới”.",
                "message_type": "system",
                "created_at": None,
            },
        ]
        cursor.fetchall.side_effect = [
            [{"user_id": 7}, {"user_id": 8}],
            [{"id": 7, "display_name": "Nam"}],
        ]

        with (
            patch("server.db_connection", return_value=connection_context),
            patch.object(ChatServer, "broadcast_group_event") as broadcast,
        ):
            result = ChatServer().rename_group(7, {"group_id": 15, "name": "Mới"})

        self.assertEqual(result["group"]["name"], "Mới")
        self.assertEqual(result["message"]["message_type"], "system")
        self.assertIn("đổi tên nhóm", result["message"]["body"])
        connection.commit.assert_called_once_with()
        broadcast.assert_called_once_with(
            [7, 8], "group_updated", result
        )

    def test_add_group_members_notifies_all_members_and_logs_who_was_added(self) -> None:
        connection_context = MagicMock()
        connection = connection_context.__enter__()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"member": 1},
            {
                "id": 62,
                "group_id": 15,
                "sender_id": 7,
                "body": "Nam đã thêm Linh vào nhóm.",
                "message_type": "system",
                "created_at": None,
            },
            {"name": "Nhóm"},
        ]
        cursor.fetchall.side_effect = [
            [{"user_id": 7}, {"user_id": 8}],
            [{"id": 9}],
            [
                {"id": 7, "display_name": "Nam"},
                {"id": 9, "display_name": "Linh"},
            ],
        ]

        with (
            patch("server.db_connection", return_value=connection_context),
            patch.object(ChatServer, "broadcast_group_event") as broadcast,
        ):
            result = ChatServer().add_group_members(
                7, {"group_id": 15, "member_ids": [9]}
            )

        self.assertEqual(result["group"]["member_count"], 3)
        self.assertEqual(cursor.executemany.call_args.args[1], [(15, 9)])
        self.assertIn("Nam đã thêm Linh", result["message"]["body"])
        connection.commit.assert_called_once_with()
        broadcast.assert_called_once_with(
            [7, 8, 9], "group_updated", result
        )

    def test_leave_group_records_activity_for_remaining_members(self) -> None:
        connection_context = MagicMock()
        connection = connection_context.__enter__()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"member": 1},
            {
                "id": 63,
                "group_id": 15,
                "sender_id": 7,
                "body": "Nam đã rời nhóm.",
                "message_type": "system",
                "created_at": None,
            },
            {"name": "Nhóm"},
        ]
        cursor.fetchall.side_effect = [
            [{"user_id": 7}, {"user_id": 8}],
            [{"id": 7, "display_name": "Nam"}],
        ]

        with (
            patch("server.db_connection", return_value=connection_context),
            patch.object(ChatServer, "broadcast_group_event") as broadcast,
        ):
            result = ChatServer().leave_group(7, {"group_id": 15})

        self.assertEqual(result, {"group_id": 15})
        self.assertIn("DELETE FROM group_members", cursor.execute.call_args_list[-2].args[0])
        connection.commit.assert_called_once_with()
        broadcast.assert_called_once_with(
            [8],
            "group_updated",
            {
                "group": {"id": 15, "name": "Nhóm", "member_count": 1},
                "message": {
                    "id": 63,
                    "group_id": 15,
                    "sender_id": 7,
                    "body": "Nam đã rời nhóm.",
                    "message_type": "system",
                    "created_at": None,
                },
            },
        )

    def test_create_group_requires_at_least_two_other_members(self) -> None:
        with self.assertRaisesRegex(ValueError, "ít nhất hai người khác"):
            ChatServer().create_group(7, {"name": "Nhóm", "member_ids": [8]})

    def test_create_group_rejects_members_not_found_in_database(self) -> None:
        connection_context = MagicMock()
        cursor = connection_context.__enter__().cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"id": 8}]

        with patch("server.db_connection", return_value=connection_context):
            with self.assertRaisesRegex(ValueError, "không tồn tại"):
                ChatServer().create_group(
                    7, {"name": "Nhóm", "member_ids": [8, 9]}
                )

        connection_context.__enter__().begin.assert_not_called()

    def test_group_history_requires_membership(self) -> None:
        connection_context = MagicMock()
        cursor = connection_context.__enter__().cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None

        with patch("server.db_connection", return_value=connection_context):
            with self.assertRaisesRegex(ValueError, "không phải thành viên"):
                ChatServer().group_history(7, {"group_id": 15})

        self.assertEqual(cursor.execute.call_count, 1)

    def test_group_message_is_delivered_to_other_online_members(self) -> None:
        connection_context = MagicMock()
        cursor = connection_context.__enter__().cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"member": 1},
            {"name": "Bạn thân"},
            {
                "id": 31,
                "group_id": 15,
                "sender_id": 7,
                "body": "Xin chào nhóm",
                "attachment_name": None,
                "attachment_type": None,
                "created_at": None,
                "sender_name": "Nam",
            },
        ]
        cursor.fetchall.return_value = [{"user_id": 7}, {"user_id": 8}]
        recipient = MagicMock()
        recipient.send = MagicMock()
        server = ChatServer()
        server.online[8] = recipient

        with patch("server.db_connection", return_value=connection_context):
            result = server.send_group_message(
                7, {"group_id": 15, "body": "Xin chào nhóm"}
            )

        self.assertEqual(result["message"]["group_name"], "Bạn thân")
        self.assertEqual(result["message"]["id"], 31)
        recipient.send.assert_called_once()
        event = recipient.send.call_args.args[0]
        self.assertEqual(event["event"], "new_group_message")
        self.assertEqual(event["data"]["group_id"], 15)

    def test_group_attachment_download_requires_membership(self) -> None:
        connection_context = MagicMock()
        cursor = connection_context.__enter__().cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        client = ClientConnection(sock=None, address=("127.0.0.1", 0), user_id=8)

        with patch("server.db_connection", return_value=connection_context):
            with self.assertRaisesRegex(ValueError, "không phải thành viên"):
                ChatServer().download(
                    client, {"message_id": 31, "group_id": 15}
                )

        self.assertEqual(cursor.execute.call_count, 1)


class PublicProfileTests(unittest.TestCase):
    def test_public_profile_returns_only_enabled_fields(self) -> None:
        user = {
            "display_name": "Linh",
            "birth_date": "2000-01-02",
            "interests": "Đọc sách",
            "hometown": "Đà Nẵng",
            "is_display_name_public": 1,
            "is_birth_date_public": 0,
            "is_interests_public": 1,
            "is_hometown_public": 0,
        }
        connection_context = MagicMock()
        cursor = connection_context.__enter__().cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = user

        with patch("server.db_connection", return_value=connection_context):
            profile = ChatServer().public_profile(7, {"user_id": 8})

        self.assertEqual(
            profile,
            {
                "display_name": "Linh",
                "interests": "Đọc sách",
            },
        )
        self.assertNotIn("birth_date", profile)
        self.assertNotIn("hometown", profile)
        self.assertNotIn("username", profile)

    def test_public_profile_cannot_be_used_to_view_own_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "hồ sơ cá nhân"):
            ChatServer().public_profile(7, {"user_id": 7})

    def test_contact_list_hides_username_when_display_name_is_private(self) -> None:
        connection_context = MagicMock()
        cursor = connection_context.__enter__().cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {
                "id": 8,
                "display_name": "Linh Private",
                "is_display_name_public": 0,
            }
        ]

        with patch("server.db_connection", return_value=connection_context):
            users = ChatServer().list_users(7)["users"]

        self.assertEqual(users[0]["display_name"], "Người dùng")
        self.assertNotIn("is_display_name_public", users[0])
        self.assertNotIn("username", users[0])

    def test_update_profile_saves_visibility_preferences(self) -> None:
        own_profile = {
            "id": 7,
            "username": "nam1111",
            "display_name": "Nam",
            "birth_date": None,
            "interests": "",
            "hometown": "",
            "is_display_name_public": 1,
            "is_birth_date_public": 0,
            "is_interests_public": 0,
            "is_hometown_public": 0,
        }
        update_context = MagicMock()
        update_cursor = update_context.__enter__().cursor.return_value.__enter__.return_value
        update_cursor.fetchone.return_value = {"password_hash": "stored-hash"}
        profile_context = MagicMock()
        profile_cursor = profile_context.__enter__().cursor.return_value.__enter__.return_value
        profile_cursor.fetchone.return_value = own_profile

        with (
            patch("server.db_connection", side_effect=[update_context, profile_context]),
            patch("server.verify_password", return_value=False),
        ):
            result = ChatServer().update_profile(
                7,
                {
                    "display_name": "Nam",
                    "is_display_name_public": True,
                    "is_birth_date_public": False,
                    "is_interests_public": False,
                    "is_hometown_public": False,
                },
            )

        update_parameters = update_cursor.execute.call_args.args[1]
        self.assertEqual(update_parameters[4:8], (1, 0, 0, 0))
        self.assertEqual(result["is_birth_date_public"], 0)


class ProfileVisibilityMigrationTests(unittest.TestCase):
    def test_adds_visibility_columns_missing_from_existing_database(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"Field": "username"}, {"Field": "display_name"}]

        ensure_profile_visibility_columns(cursor)

        alter_statements = [
            call.args[0]
            for call in cursor.execute.call_args_list
            if call.args[0].startswith("ALTER TABLE users ADD COLUMN")
        ]
        self.assertEqual(len(alter_statements), 4)
        for column, default_value in PROFILE_VISIBILITY_DEFAULTS.items():
            statement = next(item for item in alter_statements if column in item)
            self.assertIn(f"DEFAULT {default_value}", statement)


if __name__ == "__main__":
    unittest.main()
