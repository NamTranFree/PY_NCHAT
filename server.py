"""TCP chat server with MySQL-backed accounts and message history."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import socket
import threading
import uuid
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

load_dotenv()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024 * 1024
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
PROFILE_VISIBILITY_COLUMNS = {
    "is_display_name_public": "Tên hiển thị",
    "is_birth_date_public": "Ngày sinh",
    "is_interests_public": "Sở thích",
    "is_hometown_public": "Quê quán",
}
PROFILE_VISIBILITY_DEFAULTS = {
    "is_display_name_public": 1,
    "is_birth_date_public": 0,
    "is_interests_public": 0,
    "is_hometown_public": 0,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(threadName)s: %(message)s",
)
logger = logging.getLogger("chat-server")


def required_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Thiếu cấu hình môi trường {name}.")
    return value


DB_HOST = required_env("DB_HOST", "127.0.0.1")
DB_PORT = int(required_env("DB_PORT", "330"))
DB_USER = required_env("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = required_env("DB_NAME", "python_chat")
CHAT_HOST = required_env("CHAT_HOST", "0.0.0.0")
CHAT_PORT = int(required_env("CHAT_PORT", "5050"))
UPLOAD_DIR = Path(required_env("UPLOAD_DIR", "uploads")).resolve()

if not DATABASE_NAME_RE.fullmatch(DB_NAME):
    raise RuntimeError("DB_NAME chỉ được chứa chữ, số và dấu gạch dưới.")


def db_connection() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=5,
    )


def initialize_database() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    sql = schema_path.read_text(encoding="utf-8")
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    with pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    with db_connection() as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                if statement.lower().startswith(("create database", "use ")):
                    continue
                cursor.execute(statement)
            ensure_profile_visibility_columns(cursor)
            ensure_group_message_type_column(cursor)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Đã kiểm tra cơ sở dữ liệu %s.", DB_NAME)


def ensure_profile_visibility_columns(cursor: Any) -> None:
    cursor.execute("SHOW COLUMNS FROM users")
    existing_columns = {row["Field"] for row in cursor.fetchall()}
    for column in PROFILE_VISIBILITY_COLUMNS:
        if column not in existing_columns:
            default_value = PROFILE_VISIBILITY_DEFAULTS[column]
            cursor.execute(
                f"ALTER TABLE users ADD COLUMN `{column}` TINYINT(1) NOT NULL "
                f"DEFAULT {default_value}"
            )
            logger.info("Đã thêm cột quyền riêng tư %s vào bảng users.", column)


def ensure_group_message_type_column(cursor: Any) -> None:
    cursor.execute("SHOW COLUMNS FROM group_messages")
    existing_columns = {row["Field"] for row in cursor.fetchall()}
    if "message_type" not in existing_columns:
        cursor.execute(
            "ALTER TABLE group_messages ADD COLUMN message_type VARCHAR(20) "
            "NOT NULL DEFAULT 'message'"
        )
        logger.info("Đã thêm cột loại tin nhắn vào bảng group_messages.")


def send_json(sock: socket.socket, lock: threading.Lock, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with lock:
        sock.sendall(data + b"\n")


def clean_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = "".join(char for char in name if char.isprintable() and char not in '<>:"/\\|?*')
    return name[:255] or "tep_tin"


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(rounds),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@dataclass
class ClientConnection:
    sock: socket.socket
    address: tuple[str, int]
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    user_id: int | None = None

    def send(self, payload: dict[str, Any]) -> None:
        send_json(self.sock, self.send_lock, payload)


class ChatServer:
    def __init__(self) -> None:
        self.clients_lock = threading.Lock()
        self.online: dict[int, ClientConnection] = {}

    def broadcast_presence(self) -> None:
        with self.clients_lock:
            clients = list(self.online.values())
        for client in clients:
            try:
                client.send({"type": "event", "event": "presence_changed"})
            except OSError:
                logger.warning("Không thể thông báo trạng thái tới %s.", client.address)

    def handle_client(self, client: ClientConnection) -> None:
        logger.info("Kết nối từ %s.", client.address)
        try:
            reader = client.sock.makefile("rb")
            while True:
                raw = reader.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                    client.send(
                        {
                            "type": "response",
                            "id": None,
                            "ok": False,
                            "error": "Yêu cầu quá lớn hoặc không đúng định dạng.",
                        }
                    )
                    break
                request: dict[str, Any] = {}
                try:
                    request = json.loads(raw.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("Yêu cầu không hợp lệ.")
                    request_id = request.get("id")
                    action = request.get("action")
                    data = request.get("data", {})
                    if not isinstance(data, dict) or not isinstance(action, str):
                        raise ValueError("Yêu cầu không hợp lệ.")
                    result = self.dispatch(client, action, data)
                    client.send(
                        {"type": "response", "id": request_id, "ok": True, "data": result}
                    )
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    client.send(
                        {
                            "type": "response",
                            "id": None,
                            "ok": False,
                            "error": "Dữ liệu gửi lên không phải JSON UTF-8 hợp lệ.",
                        }
                    )
                    logger.info("Dữ liệu không hợp lệ từ %s: %s", client.address, error)
                except pymysql.MySQLError:
                    logger.exception("Lỗi MySQL khi xử lý yêu cầu từ %s.", client.address)
                    client.send(
                        {
                            "type": "response",
                            "id": request.get("id"),
                            "ok": False,
                            "error": "Không thể xử lý yêu cầu do lỗi cơ sở dữ liệu.",
                        }
                    )
                except (ValueError, KeyError, TypeError) as error:
                    logger.info("Yêu cầu lỗi từ %s: %s", client.address, error)
                    client.send(
                        {
                            "type": "response",
                            "id": request.get("id") if isinstance(request, dict) else None,
                            "ok": False,
                            "error": str(error),
                        }
                    )
        except (ConnectionError, OSError):
            logger.info("Ngắt kết nối: %s.", client.address)
        finally:
            try:
                client.sock.close()
            except OSError:
                logger.warning("Lỗi khi đóng kết nối %s.", client.address, exc_info=True)
            became_offline = False
            if client.user_id is not None:
                with self.clients_lock:
                    if self.online.get(client.user_id) is client:
                        del self.online[client.user_id]
                        client.user_id = None
                        became_offline = True
            if became_offline:
                self.broadcast_presence()

    def dispatch(
        self, client: ClientConnection, action: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        if action == "register":
            return self.register(data)
        if action == "login":
            return self.login(client, data)
        if action == "download":
            return self.download(client, data)
        if client.user_id is None:
            raise ValueError("Vui lòng đăng nhập trước.")
        if action == "profile":
            return self.profile(client.user_id)
        if action == "public_profile":
            return self.public_profile(client.user_id, data)
        if action == "update_profile":
            return self.update_profile(client.user_id, data)
        if action == "users":
            return self.list_users(client.user_id)
        if action == "groups":
            return self.list_groups(client.user_id)
        if action == "create_group":
            return self.create_group(client.user_id, data)
        if action == "group_members":
            return self.group_members(client.user_id, data)
        if action == "rename_group":
            return self.rename_group(client.user_id, data)
        if action == "add_group_members":
            return self.add_group_members(client.user_id, data)
        if action == "leave_group":
            return self.leave_group(client.user_id, data)
        if action == "history":
            return self.history(client.user_id, data)
        if action == "group_history":
            return self.group_history(client.user_id, data)
        if action == "send_message":
            return self.send_message(client.user_id, data)
        if action == "send_group_message":
            return self.send_group_message(client.user_id, data)
        raise ValueError("Chức năng không được hỗ trợ.")

    def register(self, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        display_name = str(data.get("display_name", "")).strip()
        if not USERNAME_RE.fullmatch(username):
            raise ValueError("Tên tài khoản cần dài 3-50 ký tự, chỉ gồm chữ, số, _, . hoặc -.")
        if len(password) < 8 or len(password) > 128:
            raise ValueError("Mật khẩu cần có từ 8 đến 128 ký tự.")
        if not display_name or len(display_name) > 100:
            raise ValueError("Tên hiển thị không được để trống và tối đa 100 ký tự.")
        if hmac.compare_digest(display_name, password):
            raise ValueError("Tên hiển thị không được trùng với mật khẩu.")
        salt = secrets.token_bytes(16)
        derived_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
        hashed = f"pbkdf2_sha256$600000${salt.hex()}${derived_key.hex()}"
        with db_connection() as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(
                        "INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)",
                        (username, hashed, display_name),
                    )
                except pymysql.err.IntegrityError as error:
                    if error.args and error.args[0] == 1062:
                        raise ValueError("Tên tài khoản này đã được sử dụng.") from error
                    raise
        return {"message": "Đăng ký thành công. Bạn có thể đăng nhập."}

    def login(self, client: ClientConnection, data: dict[str, Any]) -> dict[str, Any]:
        if client.user_id is not None:
            raise ValueError("Kết nối này đã đăng nhập.")
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, username, display_name, password_hash FROM users WHERE username = %s",
                    (username,),
                )
                user = cursor.fetchone()
        if user is None or not verify_password(user["password_hash"], password):
            raise ValueError("Tên tài khoản hoặc mật khẩu không đúng.")
        if hmac.compare_digest(user["display_name"], password):
            safe_display_name = user["username"]
            with db_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET display_name = %s WHERE id = %s",
                        (safe_display_name, user["id"]),
                    )
            user["display_name"] = safe_display_name
            logger.warning(
                "Đã thay tên hiển thị trùng mật khẩu bằng tên tài khoản cho user id %s.",
                user["id"],
            )
        with self.clients_lock:
            if user["id"] in self.online:
                raise ValueError("Tài khoản này đang đăng nhập ở một nơi khác.")
            client.user_id = int(user["id"])
            self.online[client.user_id] = client
        self.broadcast_presence()
        return {
            "id": int(user["id"]),
            "username": user["username"],
            "display_name": user["display_name"],
        }

    def profile(self, user_id: int) -> dict[str, Any]:
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, username, display_name, birth_date, interests, hometown, "
                    "is_display_name_public, is_birth_date_public, "
                    "is_interests_public, is_hometown_public "
                    "FROM users WHERE id = %s",
                    (user_id,),
                )
                user = cursor.fetchone()
        if user is None:
            raise ValueError("Không tìm thấy tài khoản.")
        if isinstance(user["birth_date"], date):
            user["birth_date"] = user["birth_date"].isoformat()
        return user

    def public_profile(self, viewer_id: int, data: dict[str, Any]) -> dict[str, Any]:
        try:
            profile_user_id = int(data.get("user_id", 0))
        except (ValueError, TypeError) as error:
            raise ValueError("Tài khoản hồ sơ không hợp lệ.") from error
        if profile_user_id == viewer_id:
            raise ValueError("Hãy dùng trang hồ sơ cá nhân để xem thông tin của bạn.")
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT display_name, birth_date, interests, hometown, "
                    "is_display_name_public, is_birth_date_public, "
                    "is_interests_public, is_hometown_public "
                    "FROM users WHERE id = %s",
                    (profile_user_id,),
                )
                user = cursor.fetchone()
        if user is None:
            raise ValueError("Không tìm thấy tài khoản.")
        public_profile: dict[str, Any] = {}
        for field, visibility_column in (
            ("display_name", "is_display_name_public"),
            ("birth_date", "is_birth_date_public"),
            ("interests", "is_interests_public"),
            ("hometown", "is_hometown_public"),
        ):
            if user[visibility_column]:
                value = user[field]
                if field == "birth_date" and isinstance(value, date):
                    value = value.isoformat()
                public_profile[field] = value
        return public_profile

    @staticmethod
    def parse_visibility_flag(data: dict[str, Any], key: str) -> int:
        value = data.get(key, PROFILE_VISIBILITY_DEFAULTS[key])
        if isinstance(value, bool):
            return int(value)
        if value in (0, 1, "0", "1"):
            return int(value)
        raise ValueError(f"Tùy chọn hiển thị {PROFILE_VISIBILITY_COLUMNS[key]} không hợp lệ.")

    def update_profile(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        display_name = str(data.get("display_name", "")).strip()
        interests = str(data.get("interests", "")).strip()
        hometown = str(data.get("hometown", "")).strip()
        birth_date = str(data.get("birth_date", "")).strip() or None
        visibility = {
            key: self.parse_visibility_flag(data, key)
            for key in PROFILE_VISIBILITY_COLUMNS
        }
        if not display_name or len(display_name) > 100:
            raise ValueError("Tên hiển thị không được để trống và tối đa 100 ký tự.")
        if len(interests) > 500 or len(hometown) > 100:
            raise ValueError("Sở thích tối đa 500 ký tự, quê quán tối đa 100 ký tự.")
        if birth_date:
            try:
                datetime.strptime(birth_date, "%Y-%m-%d")
            except ValueError as error:
                raise ValueError("Ngày sinh phải theo định dạng YYYY-MM-DD.") from error
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
                user = cursor.fetchone()
                if user is None:
                    raise ValueError("Không tìm thấy tài khoản.")
                if verify_password(user["password_hash"], display_name):
                    raise ValueError("Tên hiển thị không được trùng với mật khẩu.")
                cursor.execute(
                    "UPDATE users SET display_name = %s, birth_date = %s, interests = %s, hometown = %s, "
                    "is_display_name_public = %s, is_birth_date_public = %s, "
                    "is_interests_public = %s, is_hometown_public = %s "
                    "WHERE id = %s",
                    (
                        display_name,
                        birth_date,
                        interests,
                        hometown,
                        visibility["is_display_name_public"],
                        visibility["is_birth_date_public"],
                        visibility["is_interests_public"],
                        visibility["is_hometown_public"],
                        user_id,
                    ),
                )
        return self.profile(user_id)

    def list_users(self, user_id: int) -> dict[str, Any]:
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, display_name, is_display_name_public "
                    "FROM users WHERE id <> %s ORDER BY display_name",
                    (user_id,),
                )
                users = cursor.fetchall()
        with self.clients_lock:
            online_ids = set(self.online)
        for user in users:
            if not user["is_display_name_public"]:
                user["display_name"] = "Người dùng"
            user.pop("is_display_name_public", None)
            user.pop("username", None)
            user["online"] = int(user["id"]) in online_ids
        return {"users": users}

    def list_groups(self, user_id: int) -> dict[str, Any]:
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT g.id, g.name, COUNT(gm.user_id) AS member_count "
                    "FROM chat_groups g "
                    "JOIN group_members mine ON mine.group_id = g.id AND mine.user_id = %s "
                    "JOIN group_members gm ON gm.group_id = g.id "
                    "GROUP BY g.id, g.name ORDER BY g.name",
                    (user_id,),
                )
                groups = list(cursor.fetchall())
        for group in groups:
            group["id"] = int(group["id"])
            group["member_count"] = int(group["member_count"])
        return {"groups": groups}

    def create_group(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ValueError("Tên nhóm không được để trống và tối đa 80 ký tự.")
        raw_member_ids = data.get("member_ids")
        if not isinstance(raw_member_ids, list):
            raise ValueError("Danh sách thành viên không hợp lệ.")
        try:
            member_ids = [int(member_id) for member_id in raw_member_ids]
        except (ValueError, TypeError) as error:
            raise ValueError("Danh sách thành viên không hợp lệ.") from error
        if len(set(member_ids)) != len(member_ids) or user_id in member_ids:
            raise ValueError("Danh sách thành viên bị trùng hoặc không hợp lệ.")
        if len(member_ids) < 2:
            raise ValueError("Hãy chọn ít nhất hai người khác để tạo nhóm.")
        if len(member_ids) > 49:
            raise ValueError("Một nhóm chỉ được có tối đa 50 thành viên.")
        placeholders = ", ".join(["%s"] * len(member_ids))
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT id FROM users WHERE id IN ({placeholders})",
                    tuple(member_ids),
                )
                existing_ids = {int(row["id"]) for row in cursor.fetchall()}
                if existing_ids != set(member_ids):
                    raise ValueError("Một hoặc nhiều thành viên không tồn tại.")
                connection.begin()
                try:
                    cursor.execute(
                        "INSERT INTO chat_groups (name, created_by) VALUES (%s, %s)",
                        (name, user_id),
                    )
                    group_id = int(cursor.lastrowid)
                    group_members = [(group_id, member_id) for member_id in [user_id, *member_ids]]
                    cursor.executemany(
                        "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)",
                        group_members,
                    )
                    connection.commit()
                except pymysql.MySQLError:
                    connection.rollback()
                    raise
        group = {
            "id": group_id,
            "name": name,
            "member_count": len(member_ids) + 1,
        }
        self.broadcast_group_event(
            [member_id for member_id in member_ids],
            "group_created",
            {"group": group},
        )
        return {
            "group": group
        }

    def group_members(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        group_id = self.parse_group_id(data)
        with db_connection() as connection:
            with connection.cursor() as cursor:
                self.require_group_membership(cursor, group_id, user_id)
                cursor.execute(
                    "SELECT gm.user_id AS id, "
                    "CASE WHEN u.is_display_name_public = 1 THEN u.display_name "
                    "ELSE 'Người dùng' END AS display_name "
                    "FROM group_members gm JOIN users u ON u.id = gm.user_id "
                    "WHERE gm.group_id = %s ORDER BY u.display_name",
                    (group_id,),
                )
                members = list(cursor.fetchall())
        for member in members:
            member["id"] = int(member["id"])
        return {"members": members}

    @staticmethod
    def public_user_names(cursor: Any, user_ids: list[int]) -> dict[int, str]:
        placeholders = ", ".join(["%s"] * len(user_ids))
        cursor.execute(
            "SELECT id, CASE WHEN is_display_name_public = 1 THEN display_name "
            "ELSE 'Người dùng' END AS display_name "
            f"FROM users WHERE id IN ({placeholders})",
            tuple(user_ids),
        )
        return {
            int(row["id"]): str(row["display_name"])
            for row in cursor.fetchall()
        }

    @staticmethod
    def record_group_activity(
        cursor: Any, group_id: int, actor_id: int, body: str
    ) -> dict[str, Any]:
        cursor.execute(
            "INSERT INTO group_messages (group_id, sender_id, body, message_type) "
            "VALUES (%s, %s, %s, 'system')",
            (group_id, actor_id, body),
        )
        message_id = int(cursor.lastrowid)
        cursor.execute(
            "SELECT id, group_id, sender_id, body, message_type, created_at "
            "FROM group_messages WHERE id = %s",
            (message_id,),
        )
        message = cursor.fetchone()
        message["id"] = int(message["id"])
        message["group_id"] = int(message["group_id"])
        message["sender_id"] = int(message["sender_id"])
        if isinstance(message["created_at"], datetime):
            message["created_at"] = message["created_at"].strftime("%Y-%m-%d %H:%M")
        return message

    def broadcast_group_event(
        self, user_ids: list[int], event_name: str, data: dict[str, Any]
    ) -> None:
        with self.clients_lock:
            recipients = [
                self.online[user_id]
                for user_id in set(user_ids)
                if user_id in self.online
            ]
        for recipient in recipients:
            try:
                recipient.send(
                    {"type": "event", "event": event_name, "data": data}
                )
            except OSError:
                logger.warning(
                    "Không thể gửi sự kiện %s tới tài khoản %s.",
                    event_name,
                    recipient.user_id,
                )

    def rename_group(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        group_id = self.parse_group_id(data)
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ValueError("Tên nhóm không được để trống và tối đa 80 ký tự.")
        with db_connection() as connection:
            with connection.cursor() as cursor:
                self.require_group_membership(cursor, group_id, user_id)
                cursor.execute(
                    "SELECT user_id FROM group_members WHERE group_id = %s",
                    (group_id,),
                )
                member_ids = [int(row["user_id"]) for row in cursor.fetchall()]
                names = self.public_user_names(cursor, [user_id])
                actor_name = names.get(user_id, "Một thành viên")
                connection.begin()
                try:
                    cursor.execute(
                        "UPDATE chat_groups SET name = %s WHERE id = %s",
                        (name, group_id),
                    )
                    message = self.record_group_activity(
                        cursor,
                        group_id,
                        user_id,
                        f"{actor_name} đã đổi tên nhóm thành “{name}”.",
                    )
                    connection.commit()
                except pymysql.MySQLError:
                    connection.rollback()
                    raise
        group = {"id": group_id, "name": name, "member_count": len(member_ids)}
        self.broadcast_group_event(
            member_ids, "group_updated", {"group": group, "message": message}
        )
        return {"group": group, "message": message}

    def add_group_members(
        self, user_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        group_id = self.parse_group_id(data)
        raw_member_ids = data.get("member_ids")
        if not isinstance(raw_member_ids, list) or not raw_member_ids:
            raise ValueError("Chọn ít nhất một người để thêm vào nhóm.")
        try:
            member_ids = [int(member_id) for member_id in raw_member_ids]
        except (ValueError, TypeError) as error:
            raise ValueError("Danh sách thành viên không hợp lệ.") from error
        if len(set(member_ids)) != len(member_ids) or user_id in member_ids:
            raise ValueError("Danh sách thành viên bị trùng hoặc không hợp lệ.")
        if len(member_ids) > 49:
            raise ValueError("Một nhóm chỉ được có tối đa 50 thành viên.")
        placeholders = ", ".join(["%s"] * len(member_ids))
        with db_connection() as connection:
            with connection.cursor() as cursor:
                self.require_group_membership(cursor, group_id, user_id)
                cursor.execute(
                    "SELECT user_id FROM group_members WHERE group_id = %s",
                    (group_id,),
                )
                existing_member_ids = [
                    int(row["user_id"]) for row in cursor.fetchall()
                ]
                existing_ids = set(existing_member_ids)
                if existing_ids.intersection(member_ids):
                    raise ValueError("Một hoặc nhiều người đã ở trong nhóm.")
                if len(existing_ids) + len(member_ids) > 50:
                    raise ValueError("Một nhóm chỉ được có tối đa 50 thành viên.")
                cursor.execute(
                    f"SELECT id FROM users WHERE id IN ({placeholders})",
                    tuple(member_ids),
                )
                found_ids = {int(row["id"]) for row in cursor.fetchall()}
                if found_ids != set(member_ids):
                    raise ValueError("Một hoặc nhiều thành viên không tồn tại.")
                names = self.public_user_names(cursor, [user_id, *member_ids])
                actor_name = names.get(user_id, "Một thành viên")
                added_names = [names.get(member_id, "Người dùng") for member_id in member_ids]
                connection.begin()
                try:
                    cursor.executemany(
                        "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)",
                        [(group_id, member_id) for member_id in member_ids],
                    )
                    message = self.record_group_activity(
                        cursor,
                        group_id,
                        user_id,
                        f"{actor_name} đã thêm {', '.join(added_names)} vào nhóm.",
                    )
                    connection.commit()
                except pymysql.MySQLError:
                    connection.rollback()
                    raise
                member_ids = [*existing_member_ids, *member_ids]
                cursor.execute(
                    "SELECT name FROM chat_groups WHERE id = %s",
                    (group_id,),
                )
                group_row = cursor.fetchone()
        group = {
            "id": group_id,
            "name": group_row["name"],
            "member_count": len(member_ids),
        }
        self.broadcast_group_event(
            member_ids, "group_updated", {"group": group, "message": message}
        )
        return {"group": group, "message": message}

    def leave_group(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        group_id = self.parse_group_id(data)
        with db_connection() as connection:
            with connection.cursor() as cursor:
                self.require_group_membership(cursor, group_id, user_id)
                cursor.execute(
                    "SELECT user_id FROM group_members WHERE group_id = %s",
                    (group_id,),
                )
                remaining_ids = [
                    int(row["user_id"])
                    for row in cursor.fetchall()
                    if int(row["user_id"]) != user_id
                ]
                names = self.public_user_names(cursor, [user_id])
                actor_name = names.get(user_id, "Một thành viên")
                connection.begin()
                try:
                    message = self.record_group_activity(
                        cursor, group_id, user_id, f"{actor_name} đã rời nhóm."
                    )
                    cursor.execute(
                        "DELETE FROM group_members WHERE group_id = %s AND user_id = %s",
                        (group_id, user_id),
                    )
                    if remaining_ids:
                        cursor.execute(
                            "SELECT name FROM chat_groups WHERE id = %s",
                            (group_id,),
                        )
                        group_row = cursor.fetchone()
                    connection.commit()
                except pymysql.MySQLError:
                    connection.rollback()
                    raise
        if remaining_ids:
            group = {
                "id": group_id,
                "name": group_row["name"],
                "member_count": len(remaining_ids),
            }
            self.broadcast_group_event(
                remaining_ids,
                "group_updated",
                {"group": group, "message": message},
            )
        return {"group_id": group_id}

    @staticmethod
    def require_group_membership(cursor: Any, group_id: int, user_id: int) -> None:
        cursor.execute(
            "SELECT 1 FROM group_members WHERE group_id = %s AND user_id = %s",
            (group_id, user_id),
        )
        if cursor.fetchone() is None:
            raise ValueError("Bạn không phải thành viên của nhóm này.")

    @staticmethod
    def parse_group_id(data: dict[str, Any]) -> int:
        try:
            group_id = int(data.get("group_id", 0))
        except (ValueError, TypeError) as error:
            raise ValueError("Nhóm trò chuyện không hợp lệ.") from error
        if group_id < 1:
            raise ValueError("Nhóm trò chuyện không hợp lệ.")
        return group_id

    def group_history(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        group_id = self.parse_group_id(data)
        with db_connection() as connection:
            with connection.cursor() as cursor:
                self.require_group_membership(cursor, group_id, user_id)
                cursor.execute(
                    "SELECT gm.id, gm.group_id, gm.sender_id, gm.body, gm.message_type, "
                    "gm.attachment_name, gm.attachment_type, gm.created_at, "
                    "CASE WHEN u.is_display_name_public = 1 THEN u.display_name "
                    "ELSE 'Người dùng' END AS sender_name "
                    "FROM group_messages gm JOIN users u ON u.id = gm.sender_id "
                    "WHERE gm.group_id = %s ORDER BY gm.id DESC LIMIT 100",
                    (group_id,),
                )
                messages = list(cursor.fetchall())
        messages.reverse()
        for message in messages:
            message["id"] = int(message["id"])
            message["group_id"] = int(message["group_id"])
            message["sender_id"] = int(message["sender_id"])
            if isinstance(message["created_at"], datetime):
                message["created_at"] = message["created_at"].strftime("%Y-%m-%d %H:%M")
        return {"messages": messages}

    def history(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        try:
            other_id = int(data.get("user_id", 0))
        except (ValueError, TypeError) as error:
            raise ValueError("Tài khoản trò chuyện không hợp lệ.") from error
        if other_id == user_id:
            raise ValueError("Không thể mở cuộc trò chuyện với chính mình.")
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, sender_id, recipient_id, body, attachment_name, attachment_type, created_at "
                    "FROM messages WHERE (sender_id = %s AND recipient_id = %s) "
                    "OR (sender_id = %s AND recipient_id = %s) ORDER BY id DESC LIMIT 100",
                    (user_id, other_id, other_id, user_id),
                )
                messages = list(cursor.fetchall())
        messages.reverse()
        for message in messages:
            message["id"] = int(message["id"])
            message["sender_id"] = int(message["sender_id"])
            message["recipient_id"] = int(message["recipient_id"])
            if isinstance(message["created_at"], datetime):
                message["created_at"] = message["created_at"].strftime("%Y-%m-%d %H:%M")
        return {"messages": messages}

    def send_message(self, sender_id: int, data: dict[str, Any]) -> dict[str, Any]:
        try:
            recipient_id = int(data.get("recipient_id", 0))
        except (ValueError, TypeError) as error:
            raise ValueError("Tài khoản nhận không hợp lệ.") from error
        if recipient_id == sender_id:
            raise ValueError("Không thể gửi tin nhắn cho chính mình.")
        body = str(data.get("body", ""))
        if len(body) > 5000:
            raise ValueError("Tin nhắn tối đa 5000 ký tự.")
        attachment = data.get("attachment")
        attachment_name: str | None = None
        attachment_type: str | None = None
        attachment_path: str | None = None
        attachment_bytes: bytes | None = None
        if attachment is not None:
            if not isinstance(attachment, dict):
                raise ValueError("Tệp đính kèm không hợp lệ.")
            attachment_name = clean_filename(str(attachment.get("name", "")))
            attachment_type = str(attachment.get("mime_type", "application/octet-stream"))[:100]
            encoded = attachment.get("content")
            if not isinstance(encoded, str):
                raise ValueError("Thiếu nội dung tệp đính kèm.")
            try:
                attachment_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("Nội dung tệp đính kèm không hợp lệ.") from error
            if len(attachment_bytes) > MAX_UPLOAD_BYTES:
                raise ValueError("Tệp đính kèm không được vượt quá 5 MB.")
            if not attachment_bytes:
                raise ValueError("Không thể gửi tệp rỗng.")
        if not body.strip() and attachment is None:
            raise ValueError("Hãy nhập tin nhắn hoặc chọn tệp để gửi.")
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM users WHERE id = %s", (recipient_id,))
                if cursor.fetchone() is None:
                    raise ValueError("Không tìm thấy tài khoản nhận.")
                if attachment_bytes is not None:
                    attachment_path = f"{uuid.uuid4().hex}"
                    (UPLOAD_DIR / attachment_path).write_bytes(attachment_bytes)
                cursor.execute(
                    "INSERT INTO messages "
                    "(sender_id, recipient_id, body, attachment_name, attachment_type, attachment_path) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        sender_id,
                        recipient_id,
                        body,
                        attachment_name,
                        attachment_type,
                        attachment_path,
                    ),
                )
                message_id = int(cursor.lastrowid)
                cursor.execute(
                    "SELECT id, sender_id, recipient_id, body, attachment_name, attachment_type, created_at "
                    "FROM messages WHERE id = %s",
                    (message_id,),
                )
                message = cursor.fetchone()
        message["id"] = int(message["id"])
        message["sender_id"] = int(message["sender_id"])
        message["recipient_id"] = int(message["recipient_id"])
        if isinstance(message["created_at"], datetime):
            message["created_at"] = message["created_at"].strftime("%Y-%m-%d %H:%M")
        with self.clients_lock:
            recipient = self.online.get(recipient_id)
        if recipient is not None:
            try:
                recipient.send({"type": "event", "event": "new_message", "data": message})
            except OSError:
                logger.warning("Không thể chuyển tin nhắn tới tài khoản %s.", recipient_id)
        return {"message": message}

    def send_group_message(self, sender_id: int, data: dict[str, Any]) -> dict[str, Any]:
        group_id = self.parse_group_id(data)
        body = str(data.get("body", ""))
        if len(body) > 5000:
            raise ValueError("Tin nhắn tối đa 5000 ký tự.")
        attachment = data.get("attachment")
        attachment_name: str | None = None
        attachment_type: str | None = None
        attachment_path: str | None = None
        attachment_bytes: bytes | None = None
        if attachment is not None:
            if not isinstance(attachment, dict):
                raise ValueError("Tệp đính kèm không hợp lệ.")
            attachment_name = clean_filename(str(attachment.get("name", "")))
            attachment_type = str(attachment.get("mime_type", "application/octet-stream"))[:100]
            encoded = attachment.get("content")
            if not isinstance(encoded, str):
                raise ValueError("Thiếu nội dung tệp đính kèm.")
            try:
                attachment_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("Nội dung tệp đính kèm không hợp lệ.") from error
            if len(attachment_bytes) > MAX_UPLOAD_BYTES:
                raise ValueError("Tệp đính kèm không được vượt quá 5 MB.")
            if not attachment_bytes:
                raise ValueError("Không thể gửi tệp rỗng.")
        if not body.strip() and attachment is None:
            raise ValueError("Hãy nhập tin nhắn hoặc chọn tệp để gửi.")
        with db_connection() as connection:
            with connection.cursor() as cursor:
                self.require_group_membership(cursor, group_id, sender_id)
                cursor.execute(
                    "SELECT name FROM chat_groups WHERE id = %s",
                    (group_id,),
                )
                group = cursor.fetchone()
                if group is None:
                    raise ValueError("Không tìm thấy nhóm.")
                if attachment_bytes is not None:
                    attachment_path = f"{uuid.uuid4().hex}"
                    (UPLOAD_DIR / attachment_path).write_bytes(attachment_bytes)
                cursor.execute(
                    "INSERT INTO group_messages "
                    "(group_id, sender_id, body, attachment_name, attachment_type, attachment_path) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        group_id,
                        sender_id,
                        body,
                        attachment_name,
                        attachment_type,
                        attachment_path,
                    ),
                )
                message_id = int(cursor.lastrowid)
                cursor.execute(
                    "SELECT gm.id, gm.group_id, gm.sender_id, gm.body, gm.message_type, "
                    "gm.attachment_name, gm.attachment_type, gm.created_at, "
                    "CASE WHEN u.is_display_name_public = 1 THEN u.display_name "
                    "ELSE 'Người dùng' END AS sender_name "
                    "FROM group_messages gm JOIN users u ON u.id = gm.sender_id "
                    "WHERE gm.id = %s",
                    (message_id,),
                )
                message = cursor.fetchone()
                cursor.execute(
                    "SELECT user_id FROM group_members WHERE group_id = %s",
                    (group_id,),
                )
                member_ids = [int(row["user_id"]) for row in cursor.fetchall()]
        message["id"] = int(message["id"])
        message["group_id"] = int(message["group_id"])
        message["sender_id"] = int(message["sender_id"])
        if isinstance(message["created_at"], datetime):
            message["created_at"] = message["created_at"].strftime("%Y-%m-%d %H:%M")
        message["group_name"] = group["name"]
        with self.clients_lock:
            recipients = [
                self.online[member_id]
                for member_id in member_ids
                if member_id != sender_id and member_id in self.online
            ]
        for recipient in recipients:
            try:
                recipient.send(
                    {"type": "event", "event": "new_group_message", "data": message}
                )
            except OSError:
                logger.warning(
                    "Không thể chuyển tin nhắn nhóm tới tài khoản %s.",
                    recipient.user_id,
                )
        return {"message": message}

    def download(self, client: ClientConnection, data: dict[str, Any]) -> dict[str, Any]:
        if client.user_id is None:
            raise ValueError("Vui lòng đăng nhập trước.")
        try:
            message_id = int(data.get("message_id", 0))
        except (ValueError, TypeError) as error:
            raise ValueError("Tin nhắn không hợp lệ.") from error
        with db_connection() as connection:
            with connection.cursor() as cursor:
                if data.get("group_id") is not None:
                    group_id = self.parse_group_id(data)
                    self.require_group_membership(cursor, group_id, client.user_id)
                    cursor.execute(
                        "SELECT gm.attachment_name, gm.attachment_type, gm.attachment_path "
                        "FROM group_messages gm WHERE gm.id = %s AND gm.group_id = %s",
                        (message_id, group_id),
                    )
                    attachment = cursor.fetchone()
                else:
                    cursor.execute(
                        "SELECT attachment_name, attachment_type, attachment_path FROM messages "
                        "WHERE id = %s AND (sender_id = %s OR recipient_id = %s)",
                        (message_id, client.user_id, client.user_id),
                    )
                    attachment = cursor.fetchone()
        if attachment is None or attachment["attachment_path"] is None:
            raise ValueError("Không tìm thấy tệp hoặc bạn không có quyền tải tệp này.")
        path = UPLOAD_DIR / attachment["attachment_path"]
        try:
            content = path.read_bytes()
        except OSError as error:
            logger.exception("Không thể đọc tệp đính kèm %s.", path)
            raise ValueError("Tệp đính kèm hiện không thể tải xuống.") from error
        return {
            "name": attachment["attachment_name"],
            "mime_type": attachment["attachment_type"],
            "content": base64.b64encode(content).decode("ascii"),
        }

    def serve_forever(self) -> None:
        initialize_database()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((CHAT_HOST, CHAT_PORT))
            server_socket.listen()
            logger.info("Chat server đang lắng nghe tại %s:%s.", CHAT_HOST, CHAT_PORT)
            while True:
                sock, address = server_socket.accept()
                client = ClientConnection(sock, address)
                threading.Thread(
                    target=self.handle_client,
                    args=(client,),
                    daemon=True,
                    name=f"client-{address[0]}-{address[1]}",
                ).start()


if __name__ == "__main__":
    ChatServer().serve_forever()
