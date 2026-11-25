"""Single-window CustomTkinter client for the Python Socket chat application."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import socket
import tempfile
import threading
import unicodedata
import uuid
from pathlib import Path
from tkinter import BooleanVar, Canvas, filedialog, font as tkfont, messagebox
from typing import Any, Callable

import customtkinter as ctk
from dotenv import load_dotenv
from PIL import Image, ImageOps, ImageTk

load_dotenv()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
CHAT_HOST = os.getenv("CHAT_SERVER_HOST", "127.0.0.1")
CHAT_PORT = int(os.getenv("CHAT_PORT", "5050"))

Callback = Callable[[dict[str, Any]], None]
BG = "#F4F7FB"
CARD = "#FFFFFF"
INK = "#172033"
MUTED = "#768198"
PRIMARY = "#3D6FE8"
GREEN = "#20A779"
CHAT_BACKGROUND = "#F6F8FC"
WALLPAPER_DIRECTORY = Path(__file__).resolve().parent / "anh_nen"


def parse_server_address(host: str, port_text: str) -> tuple[str, int]:
    """Chuẩn hóa IP máy chủ và kiểm tra cổng trước khi kết nối."""
    host = host.strip()
    if not host:
        raise ValueError("Vui lòng nhập IP hoặc tên máy chủ.")
    try:
        port = int(port_text)
    except ValueError as error:
        raise ValueError("Cổng phải là số từ 1 đến 65535.") from error
    if not 1 <= port <= 65535:
        raise ValueError("Cổng phải là số từ 1 đến 65535.")
    return host, port


def list_wallpapers(directory: Path) -> list[Path]:
    """Liệt kê ảnh nền được hỗ trợ theo thứ tự tên ổn định."""
    supported_extensions = {".png", ".jpg", ".jpeg", ".webp"}
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in supported_extensions
        ),
        key=lambda path: path.name.casefold(),
    )


def wallpaper_settings_path() -> Path:
    """Trả về vị trí lưu tùy chọn cục bộ của người dùng."""
    local_app_data = os.getenv("LOCALAPPDATA")
    base_directory = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base_directory / "NCHAT" / "chat_backgrounds.json"


def nickname_settings_path() -> Path:
    """Trả về tệp lưu biệt danh, tách biệt với cài đặt hình nền."""
    return wallpaper_settings_path().with_name("chat_nicknames.json")


def conversation_key(user_id: int, other_id: int) -> str:
    """Tạo khóa chung cho một cuộc trò chuyện dù thứ tự hai người thay đổi."""
    return ":".join(str(value) for value in sorted((user_id, other_id)))


def conversation_display_name(
    user: dict[str, Any], owner_id: int, nicknames: dict[str, str]
) -> str:
    """Chọn biệt danh riêng nếu có, nếu không dùng tên hiển thị công khai."""
    nickname = nicknames.get(conversation_key(owner_id, user["id"]))
    return nickname or str(user.get("display_name") or "Người dùng")


def group_member_role_text(member_id: int, leader_id: int, creator_id: int) -> str:
    """Mô tả riêng vai trò trưởng nhóm và người tạo nhóm."""
    if member_id == leader_id:
        return (
            "Trưởng nhóm · Người tạo nhóm"
            if member_id == creator_id
            else "Trưởng nhóm"
        )
    if member_id == creator_id:
        return "Thành viên · Người tạo nhóm"
    return "Thành viên"


def filter_items_by_name(
    items: list[dict[str, Any]],
    query: str,
    get_name: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    """Lọc tên theo tiền tố trước, rồi đến phần khớp không dấu."""
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return list(items)
    prefix_matches = []
    partial_matches = []
    for item in items:
        name = normalize_search_text(get_name(item))
        if name.startswith(normalized_query):
            prefix_matches.append(item)
        elif normalized_query in name:
            partial_matches.append(item)
    return prefix_matches + partial_matches


def normalize_search_text(value: str) -> str:
    """Chuẩn hóa chữ hoa/thường và dấu tiếng Việt cho tìm kiếm."""
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def load_wallpaper_preferences(path: Path) -> dict[str, str]:
    """Đọc tệp tùy chọn dạng JSON và từ chối dữ liệu sai cấu trúc."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as settings_file:
        settings = json.load(settings_file)
    if not isinstance(settings, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in settings.items()
    ):
        raise ValueError("Tệp cài đặt không đúng định dạng.")
    return settings


def save_wallpaper_preferences(path: Path, settings: dict[str, str]) -> None:
    """Lưu JSON qua tệp tạm để tránh hỏng cài đặt khi ghi bị gián đoạn."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as settings_file:
            temporary_path = Path(settings_file.name)
            json.dump(settings, settings_file, ensure_ascii=False, indent=2)
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise


class ChatClient:
    def __init__(self, root: ctk.CTk) -> None:
        """Khởi tạo trạng thái ứng dụng, giao diện xác thực và kết nối nền."""
        self.root = root
        self.root.title("NCHAT | Trò chuyện kết nối")
        self.root.geometry("1120x780")
        self.root.minsize(900, 700)
        self.root.configure(fg_color=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.sock: socket.socket | None = None
        self.server_address: tuple[str, int] | None = None
        self.server_host_value = CHAT_HOST
        self.server_port_value = str(CHAT_PORT)
        self.send_lock = threading.Lock()
        self.incoming: queue.Queue[dict[str, Any]] = queue.Queue()
        self.callbacks: dict[str, Callback] = {}
        self.user: dict[str, Any] | None = None
        self.profile: dict[str, Any] | None = None
        self.selected_user: dict[str, Any] | None = None
        self.selected_group: dict[str, Any] | None = None
        self.groups: list[dict[str, Any]] = []
        self.users: list[dict[str, Any]] = []
        self.displayed_message_ids: set[int] = set()
        self.messages: list[dict[str, Any]] = []
        self.group_buttons: dict[int, ctk.CTkButton] = {}
        self.wallpaper_files = list_wallpapers(WALLPAPER_DIRECTORY)
        self.wallpaper_images: dict[str, Image.Image] = {}
        self.wallpaper_photo: ImageTk.PhotoImage | None = None
        self.wallpaper_canvas_item: int | None = None
        self.current_wallpaper: str | None = None
        self.message_font = tkfont.Font(root=self.root, family="Segoe UI", size=20)
        self.message_time_font = tkfont.Font(root=self.root, family="Segoe UI", size=9)
        self.message_link_font = tkfont.Font(root=self.root, family="Segoe UI", size=12, weight="bold")
        self.conversation_options_view: ctk.CTkFrame | None = None
        self.group_members_content: ctk.CTkFrame | None = None
        self.nickname_labels: dict[int, ctk.CTkLabel] = {}
        self.wallpaper_settings_path = wallpaper_settings_path()
        self.wallpaper_settings_error = ""
        try:
            self.wallpaper_settings = load_wallpaper_preferences(self.wallpaper_settings_path)
        except (OSError, ValueError) as error:
            self.wallpaper_settings = {}
            self.wallpaper_settings_error = f"Không đọc được cài đặt hình nền: {error}"
        self.nickname_settings_path = nickname_settings_path()
        self.nickname_settings_error = ""
        try:
            self.nicknames = load_wallpaper_preferences(self.nickname_settings_path)
        except (OSError, ValueError) as error:
            self.nicknames = {}
            self.nickname_settings_error = f"Không đọc được cài đặt biệt danh: {error}"
        self.closed = False
        self.screen = "auth"
        self.emoji_popup: ctk.CTkToplevel | None = None
        self.emoji_button: ctk.CTkButton | None = None
        self.root.bind_all("<Button-1>", self.on_root_click, add="+")

        self.show_auth("login")
        self.root.after(100, self.process_incoming)
        self.root.after(150, self.connect_to_server)

    def send_request(self, action: str, data: dict[str, Any], callback: Callback) -> None:
        """Gửi yêu cầu JSON tới server và lưu callback theo mã tương quan."""
        if self.sock is None:
            self.set_status("Chưa kết nối máy chủ. Hãy kiểm tra IP và cổng.", error=True)
            return
        # ID tương quan giúp ghép response bất đồng bộ với đúng callback của yêu cầu.
        request_id = uuid.uuid4().hex
        payload = json.dumps(
            {"id": request_id, "action": action, "data": data},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.callbacks[request_id] = callback
        sock = self.sock
        try:
            with self.send_lock:
                sock.sendall(payload + b"\n")
        except OSError as error:
            self.callbacks.pop(request_id, None)
            if self.sock is sock:
                self.sock = None
                self.server_address = None
            self.set_status(f"Không gửi được yêu cầu: {error}", error=True)

    def connect_to_server(self) -> bool:
        """Kết nối hoặc đổi kết nối theo IP/cổng nhập trên màn hình."""
        try:
            address = parse_server_address(
                self.server_host_entry.get(), self.server_port_entry.get()
            )
        except ValueError as error:
            self.set_status(str(error), error=True)
            return False

        self.server_host_value = address[0]
        self.server_port_value = str(address[1])
        if self.sock is not None and self.server_address == address:
            self.set_status(f"Đã kết nối tới {address[0]}:{address[1]}.")
            return True
        try:
            new_socket = socket.create_connection(address, timeout=5)
        except OSError as error:
            self.set_status(
                f"Không kết nối được tới {address[0]}:{address[1]}. "
                f"Kiểm tra IP, cổng và tường lửa. Chi tiết: {error}",
                error=True,
            )
            return False

        new_socket.settimeout(None)
        old_socket = self.sock
        self.sock = new_socket
        self.server_address = address
        self.callbacks.clear()
        if old_socket is not None:
            try:
                old_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            old_socket.close()
        threading.Thread(
            target=self.read_messages, args=(new_socket,), daemon=True, name="chat-reader"
        ).start()
        self.set_status(f"Đã kết nối tới {address[0]}:{address[1]}.")
        return True

    def read_messages(self, sock: socket.socket) -> None:
        """Đọc phản hồi và sự kiện từ socket ở luồng nền."""
        # Socket được đọc ở luồng nền; queue chuyển dữ liệu về luồng Tkinter an toàn.
        try:
            reader = sock.makefile("rb")
            while not self.closed and self.sock is sock:
                raw = reader.readline(8 * 1024 * 1024 + 1)
                if not raw:
                    if not self.closed and self.sock is sock:
                        self.sock = None
                        self.server_address = None
                        self.incoming.put(
                            {"type": "connection_error", "message": "Kết nối tới máy chủ đã bị đóng."}
                        )
                    break
                if len(raw) > 8 * 1024 * 1024 or not raw.endswith(b"\n"):
                    self.incoming.put(
                        {
                            "type": "connection_error",
                            "message": "Máy chủ gửi dữ liệu vượt quá giới hạn.",
                        }
                    )
                    break
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    self.incoming.put(
                        {"type": "connection_error", "message": f"Dữ liệu máy chủ không hợp lệ: {error}"}
                    )
                    break
                if isinstance(message, dict):
                    self.incoming.put(message)
        except OSError as error:
            if not self.closed and self.sock is sock:
                self.sock = None
                self.server_address = None
                self.incoming.put(
                    {"type": "connection_error", "message": f"Kết nối tới máy chủ bị ngắt: {error}"}
                )

    def process_incoming(self) -> None:
        """Xử lý queue mạng trong luồng giao diện và lên lịch lần đọc kế tiếp."""
        # Chạy định kỳ trong event loop để callback/event có thể cập nhật widget.
        while True:
            try:
                message = self.incoming.get_nowait()
            except queue.Empty:
                break
            if message.get("type") == "response":
                callback = self.callbacks.pop(message.get("id"), None)
                if callback is not None:
                    callback(message)
            elif message.get("type") == "event":
                self.handle_event(message)
            elif message.get("type") == "connection_error":
                self.set_status(message["message"], error=True)
        if not self.closed and self.root.winfo_exists():
            self.root.after(100, self.process_incoming)

    def handle_event(self, event: dict[str, Any]) -> None:
        """Cập nhật giao diện theo sự kiện trực tiếp do server phát."""
        if event.get("event") == "presence_changed":
            self.refresh_users()
        elif event.get("event") == "new_message":
            message = event.get("data", {})
            if (
                self.selected_user
                and message.get("sender_id") == self.selected_user["id"]
            ):
                self.add_message(message)
            else:
                sender_user = next(
                    (user for user in self.users if user["id"] == message.get("sender_id")),
                    None,
                )
                sender = (
                    self.display_name_for_user(sender_user)
                    if sender_user is not None
                    else "Một tài khoản"
                )
                self.set_status(f"Bạn vừa nhận tin nhắn mới từ {sender}.")
        elif event.get("event") == "new_group_message":
            message = event.get("data", {})
            if self.selected_group and message.get("group_id") == self.selected_group["id"]:
                self.add_message(message)
            else:
                self.set_status(
                    f"Bạn vừa nhận tin nhắn mới trong nhóm {message.get('group_name', 'chat nhóm')}."
                )
        elif event.get("event") == "group_created":
            self.refresh_groups()
            group = event.get("data", {}).get("group", {})
            self.set_status(f"Bạn vừa được thêm vào nhóm {group.get('name', 'mới')}.")
        elif event.get("event") == "group_updated":
            data = event.get("data", {})
            self.apply_group_update(data)
            if self.user and data.get("removed_user_id") == self.user["id"]:
                self.close_conversation_options(animate=False)
                self.set_status("Trưởng nhóm đã xóa bạn khỏi nhóm.", error=True)
            elif (
                self.selected_group
                and isinstance(data.get("group"), dict)
                and data["group"].get("id") == self.selected_group["id"]
                and self.conversation_options_view is not None
                and self.group_members_content is not None
                and self.group_members_content.winfo_exists()
            ):
                self.load_group_members_feature(self.group_members_content)

    def clear_root(self) -> None:
        """Đóng popup phụ rồi xóa các widget của màn hình hiện tại."""
        self.hide_emoji_picker()
        for child in self.root.winfo_children():
            child.destroy()

    def on_root_click(self, event: Any) -> None:
        """Đóng bảng emoji khi người dùng bấm ra ngoài bảng và nút mở."""
        if self.emoji_popup is None or not self.emoji_popup.winfo_exists():
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            self.hide_emoji_picker()
            return
        if widget == self.emoji_button or widget == self.emoji_popup:
            return
        if self.emoji_popup.winfo_containing(event.x_root, event.y_root) is not None:
            return
        if self.emoji_button is not None and self.emoji_button.winfo_exists():
            button_x1 = self.emoji_button.winfo_rootx()
            button_y1 = self.emoji_button.winfo_rooty()
            button_x2 = button_x1 + self.emoji_button.winfo_width()
            button_y2 = button_y1 + self.emoji_button.winfo_height()
            if button_x1 <= event.x_root <= button_x2 and button_y1 <= event.y_root <= button_y2:
                return
        self.hide_emoji_picker()

    def hide_emoji_picker(self) -> None:
        """Đóng bảng chọn emoji và xóa tham chiếu tới cửa sổ popup."""
        if self.emoji_popup is not None and self.emoji_popup.winfo_exists():
            self.emoji_popup.destroy()
        self.emoji_popup = None

    def toggle_emoji_picker(self) -> None:
        """Mở hoặc đóng bảng emoji cạnh ô soạn tin nhắn."""
        if self.emoji_popup is not None and self.emoji_popup.winfo_exists():
            self.hide_emoji_picker()
            return
        if self.emoji_button is None or not self.emoji_button.winfo_exists():
            return
        self.root.update_idletasks()
        popup_width = 220
        popup_height = 180
        x = self.emoji_button.winfo_rootx()
        y = self.emoji_button.winfo_rooty() - popup_height - 8
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = min(max(0, x), max(0, screen_width - popup_width))
        if y < 0:
            y = self.emoji_button.winfo_rooty() + self.emoji_button.winfo_height() + 8
        y = min(max(0, y), max(0, screen_height - popup_height))
        popup = ctk.CTkToplevel(self.root)
        popup.overrideredirect(True)
        popup.geometry(f"{popup_width}x{popup_height}+{x}+{y}")
        popup.configure(fg_color="white")
        popup.lift()
        inner = ctk.CTkFrame(popup, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=10, pady=10)
        emojis = ["😊", "😂", "😍", "😎", "😮", "🥰", "👍", "👎", "🎉", "🔥", "❤️", "✨", "👏", "😢", "😡", "💯"]
        for idx, emoji in enumerate(emojis):
            button = ctk.CTkButton(
                inner,
                text=emoji,
                width=36,
                height=32,
                corner_radius=8,
                fg_color="white",
                hover_color="#EDF3FF",
                text_color=INK,
                font=("Segoe UI Emoji", 16),
                command=lambda value=emoji: self.insert_emoji(value),
            )
            row, col = divmod(idx, 4)
            button.grid(row=row, column=col, padx=4, pady=4)
        self.emoji_popup = popup
        self.set_status("Chọn một biểu tượng cảm xúc.")

    def insert_emoji(self, emoji: str) -> None:
        """Chèn emoji đã chọn vào ô soạn tin và trả tiêu điểm về ô nhập."""
        if not hasattr(self, "message_entry") or not self.message_entry.winfo_exists():
            return
        self.message_entry.insert("end", emoji)
        self.message_entry.focus_set()
        self.hide_emoji_picker()

    def set_status(self, text: str, error: bool = False) -> None:
        """Hiển thị thông báo trạng thái tại màn hình đang mở."""
        if hasattr(self, "status_label") and self.status_label.winfo_exists():
            self.status_label.configure(text=text, text_color="#C44545" if error else MUTED)
        elif hasattr(self, "auth_status") and self.auth_status.winfo_exists():
            self.auth_status.configure(text=text, text_color="#C44545" if error else GREEN)

    def show_auth(self, mode: str = "login") -> None:
        """Dựng màn hình đăng nhập/đăng ký kèm cấu hình máy chủ."""
        if hasattr(self, "server_host_entry") and self.server_host_entry.winfo_exists():
            self.server_host_value = self.server_host_entry.get().strip()
            self.server_port_value = self.server_port_entry.get().strip()
        self.screen = "auth"
        self.clear_root()
        shell = ctk.CTkFrame(self.root, fg_color=BG)
        shell.pack(fill="both", expand=True)

        art = ctk.CTkFrame(shell, fg_color="#1D3265", corner_radius=0, width=390)
        art.pack(side="left", fill="y")
        art.pack_propagate(False)
        ctk.CTkLabel(
            art, text="N", width=56, height=56, corner_radius=18,
            fg_color=PRIMARY, text_color="white", font=("Segoe UI", 28, "bold"),
        ).pack(anchor="w", padx=42, pady=(52, 24))
        ctk.CTkLabel(
            art, text="Kết nối gần hơn,\ntrò chuyện dễ dàng.",
            justify="left", text_color="white", font=("Segoe UI", 29, "bold"),
        ).pack(anchor="w", padx=42, pady=(20, 12))
        ctk.CTkLabel(
            art, text="Tin nhắn của bạn luôn sẵn sàng\nmỗi khi bạn quay lại.",
            justify="left", text_color="#C6D2EE", font=("Segoe UI", 15),
        ).pack(anchor="w", padx=42)
        ctk.CTkLabel(
            art, text="NCHAT  ·  KẾT NỐI THỜI GIAN THỰC",
            text_color="#9BAED8", font=("Segoe UI", 10, "bold"),
        ).pack(side="bottom", anchor="w", padx=42, pady=34)

        area = ctk.CTkFrame(shell, fg_color=BG)
        area.pack(side="left", fill="both", expand=True)
        card = ctk.CTkFrame(area, fg_color=CARD, corner_radius=20, width=430)
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.pack_propagate(False)
        card.configure(height=680 if mode == "register" else 620)

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=38, pady=24)
        if mode == "register":
            ctk.CTkButton(
                form,
                text="←  Quay lại đăng nhập",
                width=155,
                height=34,
                fg_color="transparent",
                hover_color="#EEF2FC",
                text_color=PRIMARY,
                font=("Segoe UI", 12, "bold"),
                anchor="w",
                command=lambda: self.show_auth("login"),
            ).pack(anchor="w", pady=(0, 10))
        ctk.CTkLabel(form, text="Xin chào 👋", text_color=INK, font=("Segoe UI", 26, "bold")).pack(
            anchor="w"
        )
        ctk.CTkLabel(
            form,
            text="Tạo tài khoản mới" if mode == "register" else "Đăng nhập để tiếp tục",
            text_color=MUTED, font=("Segoe UI", 14),
        ).pack(anchor="w", pady=(4, 20))

        self.auth_entries: dict[str, ctk.CTkEntry] = {}
        if mode == "register":
            fields = [
                ("display_name", "Tên hiển thị (mọi người sẽ nhìn thấy)", False),
                ("username", "Tên tài khoản dùng để đăng nhập", False),
                ("password", "Mật khẩu", True),
            ]
        else:
            fields = [
                ("username", "Tên tài khoản", False),
                ("password", "Mật khẩu", True),
            ]
        for key, label, secret in fields:
            ctk.CTkLabel(form, text=label, text_color=INK, font=("Segoe UI", 12, "bold")).pack(
                anchor="w", pady=(8, 6)
            )
            entry = ctk.CTkEntry(
                form, height=44, corner_radius=10, border_color="#DFE5EF",
                fg_color="#FBFCFE", placeholder_text=label, show="●" if secret else "",
            )
            entry.pack(fill="x")
            self.auth_entries[key] = entry

        action_text = "Tạo tài khoản" if mode == "register" else "Đăng nhập"
        ctk.CTkButton(
            form, text=action_text, height=45, corner_radius=10, fg_color=PRIMARY,
            hover_color="#315BC0", font=("Segoe UI", 14, "bold"),
            command=self.register if mode == "register" else self.login,
        ).pack(fill="x", pady=(16, 12))
        switch_text = (
            "Đã có tài khoản?  Đăng nhập" if mode == "register"
            else "Chưa có tài khoản?  Tạo tài khoản"
        )
        ctk.CTkButton(
            form, text=switch_text, height=34, fg_color="transparent", hover_color="#EEF2FC",
            text_color=PRIMARY, font=("Segoe UI", 12, "bold"),
            command=lambda: self.show_auth("login" if mode == "register" else "register"),
        ).pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            form, text="Kết nối máy chủ", text_color=INK, font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", pady=(4, 6))
        server_fields = ctk.CTkFrame(form, fg_color="transparent")
        server_fields.pack(fill="x")
        self.server_host_entry = ctk.CTkEntry(
            server_fields, height=38, corner_radius=9, border_color="#DFE5EF",
            fg_color="#FBFCFE", placeholder_text="IP máy chủ",
        )
        self.server_host_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.server_host_entry.insert(0, self.server_host_value)
        self.server_port_entry = ctk.CTkEntry(
            server_fields, width=100, height=38, corner_radius=9, border_color="#DFE5EF",
            fg_color="#FBFCFE", placeholder_text="Port",
        )
        self.server_port_entry.pack(side="left")
        self.server_port_entry.insert(0, self.server_port_value)
        ctk.CTkButton(
            form, text="Kết nối", height=36, corner_radius=9, fg_color="#EEF2FA",
            hover_color="#E2E9F6", text_color=INK, command=self.connect_to_server,
        ).pack(fill="x", pady=(8, 0))
        self.auth_status = ctk.CTkLabel(
            form, text="", text_color=GREEN, font=("Segoe UI", 11), anchor="w", wraplength=350
        )
        self.auth_status.pack(fill="x", pady=(8, 0))
        first_entry = "display_name" if mode == "register" else "username"
        self.auth_entries[first_entry].focus_set()
        self.root.bind("<Return>", self.submit_auth)
        self.root.bind("<Escape>", self.go_back)

    def go_back(self, _event: Any = None) -> None:
        """Điều hướng phím Escape về đăng nhập hoặc trở lại màn hình chat."""
        if self.screen == "auth" and "display_name" in getattr(self, "auth_entries", {}):
            self.show_auth("login")
        elif self.screen in ("profile", "public_profile"):
            self.return_to_chat()

    def submit_auth(self, _event: Any = None) -> None:
        """Gửi biểu mẫu đăng ký hoặc đăng nhập khi nhấn Enter."""
        if self.screen == "auth":
            if "display_name" in self.auth_entries:
                self.register()
            else:
                self.login()

    def login(self) -> None:
        """Kiểm tra kết nối rồi gửi thông tin đăng nhập tới server."""
        self.set_status("")
        if not self.connect_to_server():
            return
        self.send_request(
            "login",
            {
                "username": self.auth_entries["username"].get().strip(),
                "password": self.auth_entries["password"].get(),
            },
            self.complete_login,
        )

    def register(self) -> None:
        """Kiểm tra kết nối rồi gửi thông tin đăng ký tới server."""
        self.set_status("")
        if not self.connect_to_server():
            return
        self.send_request(
            "register",
            {
                "username": self.auth_entries["username"].get().strip(),
                "display_name": self.auth_entries["display_name"].get().strip(),
                "password": self.auth_entries["password"].get(),
            },
            self.complete_register,
        )

    def complete_register(self, response: dict[str, Any]) -> None:
        """Hiển thị lỗi đăng ký hoặc chuyển sang đăng nhập sau khi tạo tài khoản."""
        if not response.get("ok"):
            self.set_status(response.get("error", "Không thể đăng ký."), error=True)
            return
        username = self.auth_entries["username"].get().strip()
        display_name = self.auth_entries["display_name"].get().strip()
        self.show_auth("login")
        self.auth_entries["username"].insert(0, username)
        self.auth_status.configure(
            text=f"Đăng ký thành công cho {display_name}. Mời bạn đăng nhập."
        )
        self.auth_entries["password"].focus_set()

    def complete_login(self, response: dict[str, Any]) -> None:
        """Lưu tài khoản đăng nhập và tải hồ sơ, bạn bè, nhóm chat."""
        if not response.get("ok"):
            self.set_status(response.get("error", "Tên tài khoản hoặc mật khẩu không đúng."), error=True)
            return
        self.user = response["data"]
        self.root.unbind("<Return>")
        self.show_chat()
        self.load_profile()
        self.refresh_users()
        self.refresh_groups()

    def show_chat(self) -> None:
        """Dựng màn hình chat gồm danh sách, lịch sử và khung soạn tin."""
        initial_sidebar_tab = getattr(self, "sidebar_tab", "friends")
        self.screen = "chat"
        self.conversation_options_view = None
        self.group_members_content = None
        self.root.unbind("<Escape>")
        self.clear_root()
        self.root.title("NCHAT")
        app = ctk.CTkFrame(self.root, fg_color=BG)
        app.pack(fill="both", expand=True, padx=20, pady=18)
        header = ctk.CTkFrame(app, fg_color="transparent", height=64)
        header.pack(fill="x", pady=(0, 14))
        header.pack_propagate(False)
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left", fill="y")
        ctk.CTkLabel(
            brand, text="N", width=42, height=42, corner_radius=14, fg_color=PRIMARY,
            text_color="white", font=("Segoe UI", 22, "bold"),
        ).pack(side="left", padx=(0, 10), pady=8)
        ctk.CTkLabel(brand, text="NCHAT", text_color=INK, font=("Segoe UI", 20, "bold")).pack(
            side="left"
        )
        display_name = self.user.get("display_name") or "Người dùng"
        self.profile_label = ctk.CTkLabel(
            header,
            text=display_name,
            text_color=INK,
            font=("Segoe UI", 13, "bold"),
        )
        self.profile_label.pack(side="right", padx=(12, 8))
        ctk.CTkButton(
            header, text="Hồ sơ", width=80, height=36, corner_radius=9,
            fg_color="white", hover_color="#E9EEF8", text_color=INK, command=self.show_profile,
        ).pack(side="right")
        ctk.CTkButton(
            header, text="Đăng xuất", width=92, height=36, corner_radius=9,
            fg_color="transparent", hover_color="#FDECEC", text_color="#B44646", command=self.logout,
        ).pack(side="right", padx=(0, 8))

        body = ctk.CTkFrame(app, fg_color="transparent")
        body.pack(fill="both", expand=True)
        sidebar = ctk.CTkFrame(body, fg_color=CARD, corner_radius=16, width=280)
        sidebar.pack(side="left", fill="y", padx=(0, 14))
        sidebar.pack_propagate(False)
        ctk.CTkLabel(sidebar, text="Tin nhắn", text_color=INK, font=("Segoe UI", 20, "bold")).pack(
            anchor="w", padx=18, pady=(18, 2)
        )
        sidebar_tabs = ctk.CTkFrame(sidebar, fg_color="transparent")
        sidebar_tabs.pack(fill="x", padx=14, pady=(10, 10))
        self.friends_tab_button = ctk.CTkButton(
            sidebar_tabs, text="Bạn bè", height=36, corner_radius=9,
            fg_color=PRIMARY, hover_color="#315BC0", text_color="white",
            font=("Segoe UI", 12, "bold"),
            command=lambda: self.show_sidebar_tab("friends"),
        )
        self.friends_tab_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.groups_tab_button = ctk.CTkButton(
            sidebar_tabs, text="Nhóm chat", height=36, corner_radius=9,
            fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
            font=("Segoe UI", 12, "bold"),
            command=lambda: self.show_sidebar_tab("groups"),
        )
        self.groups_tab_button.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.friends_view = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.friend_search_entry = ctk.CTkEntry(
            self.friends_view,
            height=36,
            corner_radius=9,
            border_color="#DFE5EF",
            placeholder_text="Tìm bạn bè...",
        )
        self.friend_search_entry.pack(fill="x", padx=14, pady=(0, 8))
        self.friend_search_entry.bind(
            "<KeyRelease>", lambda _event: self.render_users()
        )
        ctk.CTkButton(
            self.friends_view, text="↻  Làm mới", height=32, corner_radius=8, fg_color="#F0F4FC",
            hover_color="#E3EAF8", text_color=PRIMARY, command=self.refresh_users,
        ).pack(fill="x", padx=14, pady=(0, 8))
        self.user_list = ctk.CTkScrollableFrame(self.friends_view, fg_color="transparent")
        self.user_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.groups_view = ctk.CTkFrame(sidebar, fg_color="transparent")
        groups_header = ctk.CTkFrame(self.groups_view, fg_color="transparent")
        groups_header.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(
            groups_header, text="Nhóm chat", text_color=MUTED, font=("Segoe UI", 12)
        ).pack(side="left")
        ctk.CTkButton(
            groups_header, text="＋", width=28, height=26, corner_radius=7,
            fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=PRIMARY,
            command=self.open_group_creation_page,
        ).pack(side="right")
        self.group_search_entry = ctk.CTkEntry(
            self.groups_view,
            height=36,
            corner_radius=9,
            border_color="#DFE5EF",
            placeholder_text="Tìm nhóm...",
        )
        self.group_search_entry.pack(fill="x", padx=14, pady=(0, 8))
        self.group_search_entry.bind(
            "<KeyRelease>", lambda _event: self.render_groups()
        )
        self.group_list = ctk.CTkScrollableFrame(self.groups_view, fg_color="transparent")
        self.group_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.show_sidebar_tab(initial_sidebar_tab)

        self.chat_frame = ctk.CTkFrame(body, fg_color=CARD, corner_radius=16)
        self.chat_frame.pack(side="left", fill="both", expand=True)
        self.conversation_view = ctk.CTkFrame(self.chat_frame, fg_color="transparent")
        self.conversation_view.pack(fill="both", expand=True)
        chat_header = ctk.CTkFrame(self.conversation_view, fg_color="transparent", height=66)
        chat_header.pack(fill="x", padx=18)
        chat_header.pack_propagate(False)
        self.chat_title = ctk.CTkLabel(
            chat_header, text="Chọn một người hoặc nhóm để bắt đầu trò chuyện",
            text_color=INK, font=("Segoe UI", 16, "bold"),
        )
        self.chat_title.pack(side="left")
        self.chat_options_button = ctk.CTkButton(
            chat_header, text="⋮", width=38, height=36, corner_radius=9,
            fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
            font=("Segoe UI", 20, "bold"), command=self.open_conversation_menu,
        )
        if self.selected_user or self.selected_group:
            self.chat_options_button.pack(side="right")
        messages_host = ctk.CTkFrame(self.conversation_view, fg_color="transparent")
        messages_host.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.messages_scrollbar = ctk.CTkScrollbar(messages_host)
        self.messages_scrollbar.pack(side="right", fill="y")
        self.messages_canvas = Canvas(
            messages_host,
            background=CHAT_BACKGROUND,
            highlightthickness=0,
            yscrollcommand=self.update_message_scrollbar,
        )
        self.messages_scrollbar.configure(command=self.messages_canvas.yview)
        self.messages_canvas.pack(side="left", fill="both", expand=True)
        self.messages_canvas.bind("<Configure>", self.on_messages_canvas_configure)
        self.messages_canvas.bind("<MouseWheel>", self.on_messages_mousewheel)
        self.messages_canvas.bind("<Button-1>", self.on_message_canvas_click)
        self.messages_canvas.bind("<Motion>", self.on_message_canvas_motion)
        self.current_wallpaper = self.selected_wallpaper_for_current_conversation()
        composer = ctk.CTkFrame(self.conversation_view, fg_color="transparent")
        self.composer = composer
        composer.pack(fill="x", padx=14, pady=(0, 12))
        self.message_entry = ctk.CTkEntry(
            composer, height=44, corner_radius=11, border_color="#E0E6F0",
            placeholder_text="Viết tin nhắn của bạn...",
        )
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.message_entry.bind("<Return>", lambda _event: self.send_text())
        self.emoji_button = ctk.CTkButton(
            composer, text="☺", width=42, height=44, corner_radius=10,
            fg_color="#EEF2FA", hover_color="#E2E9F6", text_color=INK,
            font=("Segoe UI Symbol", 20, "bold"), command=self.toggle_emoji_picker,
        )
        self.emoji_button.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            composer, text="＋ Tệp", width=78, height=44, corner_radius=10,
            fg_color="#EEF2FA", hover_color="#E2E9F6", text_color=INK,
            command=self.choose_attachment,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            composer, text="Gửi  ➤", width=92, height=44, corner_radius=10,
            fg_color=PRIMARY, hover_color="#315BC0", command=self.send_text,
        ).pack(side="left")
        self.status_label = ctk.CTkLabel(
            app, text="Đã kết nối", text_color=MUTED, anchor="w", font=("Segoe UI", 11)
        )
        self.status_label.pack(fill="x", pady=(8, 0))
        self.render_messages()
        if self.wallpaper_settings_error:
            self.set_status(self.wallpaper_settings_error, error=True)
        elif self.nickname_settings_error:
            self.set_status(self.nickname_settings_error, error=True)

    def show_sidebar_tab(self, tab: str) -> None:
        """Chuyển giữa danh sách bạn bè và danh sách nhóm."""
        if tab not in {"friends", "groups"}:
            return
        self.sidebar_tab = tab
        if tab == "friends":
            self.groups_view.pack_forget()
            self.friends_view.pack(fill="both", expand=True)
            self.friends_tab_button.configure(
                fg_color=PRIMARY, hover_color="#315BC0", text_color="white"
            )
            self.groups_tab_button.configure(
                fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK
            )
        else:
            self.friends_view.pack_forget()
            self.groups_view.pack(fill="both", expand=True)
            self.groups_tab_button.configure(
                fg_color=PRIMARY, hover_color="#315BC0", text_color="white"
            )
            self.friends_tab_button.configure(
                fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK
            )

    def load_profile(self) -> None:
        """Yêu cầu hồ sơ riêng của tài khoản hiện đăng nhập."""
        self.send_request("profile", {}, self.complete_profile)

    def complete_profile(self, response: dict[str, Any]) -> None:
        """Cập nhật tên hồ sơ trên giao diện sau phản hồi server."""
        if not response.get("ok"):
            self.set_status(response.get("error", "Không thể tải hồ sơ."), error=True)
            return
        self.profile = response["data"]
        if self.user is not None:
            self.user["display_name"] = self.profile["display_name"]
        if hasattr(self, "profile_label") and self.profile_label.winfo_exists():
            self.profile_label.configure(
                text=self.profile["display_name"]
            )

    def show_profile(self) -> None:
        """Dựng biểu mẫu hồ sơ và các tùy chọn quyền riêng tư."""
        self.screen = "profile"
        self.clear_root()
        page = ctk.CTkFrame(self.root, fg_color=BG)
        page.pack(fill="both", expand=True, padx=28, pady=24)
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", pady=(0, 16))
        ctk.CTkButton(
            top, text="←  Quay lại chat", width=130, height=38, corner_radius=9,
            fg_color="white", hover_color="#E9EEF8", text_color=INK, command=self.return_to_chat,
        ).pack(side="left")
        ctk.CTkLabel(top, text="Thông tin cá nhân", text_color=INK, font=("Segoe UI", 22, "bold")).pack(
            side="left", padx=18
        )
        card = ctk.CTkFrame(page, fg_color=CARD, corner_radius=18, width=620)
        card.pack(anchor="n", fill="x", pady=10)
        card.pack_propagate(False)
        card.configure(height=570)
        form = ctk.CTkScrollableFrame(card, fg_color="transparent", corner_radius=0)
        form.pack(fill="both", expand=True, padx=38, pady=30)
        ctk.CTkLabel(
            form, text="Cập nhật hồ sơ", text_color=INK, font=("Segoe UI", 20, "bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            form, text="Thông tin này chỉ hiển thị với bạn",
            text_color=MUTED, font=("Segoe UI", 12),
        ).pack(anchor="w", pady=(4, 18))
        fields: dict[str, ctk.CTkEntry] = {}
        self.visibility_vars: dict[str, BooleanVar] = {}
        for key, label, placeholder in (
            ("display_name", "Tên hiển thị", "Tên bạn bè sẽ nhìn thấy"),
            ("birth_date", "Ngày sinh", "YYYY-MM-DD"),
            ("interests", "Sở thích", "Ví dụ: âm nhạc, du lịch"),
            ("hometown", "Quê quán", "Tỉnh/thành phố"),
        ):
            ctk.CTkLabel(form, text=label, text_color=INK, font=("Segoe UI", 12, "bold")).pack(
                anchor="w", pady=(8, 6)
            )
            entry = ctk.CTkEntry(
                form, height=42, corner_radius=9, border_color="#DFE5EF", placeholder_text=placeholder
            )
            value = (self.profile or {}).get(key)
            if value:
                entry.insert(0, str(value))
            entry.pack(fill="x")
            fields[key] = entry
            visibility_key = f"is_{key}_public"
            variable = BooleanVar(
                value=bool(
                    (self.profile or {}).get(
                        visibility_key,
                        key == "display_name",
                    )
                )
            )
            self.visibility_vars[visibility_key] = variable
            ctk.CTkCheckBox(
                form,
                text=f"Cho phép người khác xem {label.lower()}",
                variable=variable,
                onvalue=True,
                offvalue=False,
                text_color=INK,
                fg_color=PRIMARY,
                hover_color="#315BC0",
                border_color="#BAC5D8",
                font=("Segoe UI", 12),
            ).pack(anchor="w", pady=(6, 2))
        self.profile_status = ctk.CTkLabel(form, text="", text_color=MUTED, anchor="w")
        self.profile_status.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(
            form, text="Lưu thay đổi", height=44, corner_radius=10, fg_color=PRIMARY,
            hover_color="#315BC0", font=("Segoe UI", 13, "bold"),
            command=lambda: self.save_profile(fields, self.visibility_vars),
        ).pack(fill="x", pady=(10, 0))
        self.root.bind("<Escape>", self.go_back)

    def save_profile(
        self,
        fields: dict[str, ctk.CTkEntry],
        visibility_vars: dict[str, BooleanVar],
    ) -> None:
        """Gửi các trường hồ sơ và quyền xem của chúng để lưu trên server."""
        data = {key: entry.get().strip() for key, entry in fields.items()}
        data.update({key: variable.get() for key, variable in visibility_vars.items()})

        def saved(response: dict[str, Any]) -> None:
            """Cập nhật trạng thái biểu mẫu theo kết quả lưu hồ sơ."""
            if not response.get("ok"):
                self.profile_status.configure(text=response.get("error", "Không thể lưu."), text_color="#C44545")
                return
            self.profile = response["data"]
            if self.user is not None:
                self.user["display_name"] = self.profile["display_name"]
            self.profile_status.configure(text="Đã lưu hồ sơ thành công.", text_color=GREEN)

        self.send_request("update_profile", data, saved)

    def refresh_users(self) -> None:
        """Tải lại danh sách tài khoản và trạng thái trực tuyến."""
        if self.user and self.screen == "chat" and hasattr(self, "user_list"):
            self.send_request("users", {}, self.complete_users)

    def refresh_groups(self) -> None:
        """Tải lại danh sách nhóm mà tài khoản hiện tại tham gia."""
        if self.user and self.screen == "chat" and hasattr(self, "group_list"):
            self.send_request("groups", {}, self.complete_groups)

    def complete_groups(self, response: dict[str, Any]) -> None:
        """Lưu danh sách nhóm từ server rồi dựng lại theo nội dung tìm kiếm."""
        if self.screen != "chat":
            return
        if not response.get("ok"):
            self.set_status(response.get("error", "Không thể tải danh sách nhóm."), error=True)
            return
        self.groups = response["data"]["groups"]
        self.render_groups()

    def render_groups(self) -> None:
        """Lọc tức thời các nhóm và đồng bộ nhóm đang được chọn."""
        if self.screen != "chat" or not hasattr(self, "group_list"):
            return
        self.group_buttons.clear()
        for child in self.group_list.winfo_children():
            child.destroy()
        selected_id = self.selected_group["id"] if self.selected_group else None
        selected_group = next(
            (group for group in self.groups if group["id"] == selected_id),
            None,
        )
        if selected_group is not None:
            self.selected_group = selected_group
            self.chat_title.configure(
                text=f"{selected_group['name']}  ·  {selected_group['member_count']} thành viên"
            )
        elif selected_id is not None:
            self.selected_group = None
            self.messages.clear()
            self.displayed_message_ids.clear()
            self.chat_title.configure(text="Chọn một người hoặc nhóm để bắt đầu trò chuyện")
            if not self.selected_user:
                self.chat_options_button.pack_forget()
            self.render_messages()

        filtered_groups = filter_items_by_name(
            self.groups,
            self.group_search_entry.get(),
            lambda group: group["name"],
        )
        for group in filtered_groups:
            button = ctk.CTkButton(
                self.group_list,
                text=f"👥  {group['name']}  ·  {group['member_count']}",
                height=34,
                corner_radius=8,
                fg_color="#EEF3FF" if group["id"] == selected_id else "transparent",
                hover_color="#E3EAF8",
                text_color=INK,
                anchor="w",
                command=lambda selected=group: self.select_group(selected),
            )
            button.pack(fill="x", pady=2)
            self.group_buttons[group["id"]] = button
        if not self.groups:
            ctk.CTkLabel(
                self.group_list, text="Chưa có nhóm nào.",
                text_color=MUTED, font=("Segoe UI", 11),
            ).pack(anchor="w", padx=8, pady=5)
        elif not filtered_groups:
            ctk.CTkLabel(
                self.group_list, text="Không tìm thấy nhóm phù hợp.",
                text_color=MUTED, font=("Segoe UI", 11),
            ).pack(anchor="w", padx=8, pady=5)

    def select_group(self, group: dict[str, Any]) -> None:
        """Mở nhóm, chọn nền riêng và yêu cầu lịch sử tin nhắn."""
        if self.conversation_options_view is not None:
            self.close_conversation_options(animate=False)
        self.show_sidebar_tab("groups")
        self.selected_user = None
        self.selected_group = group
        self.chat_title.configure(
            text=f"{group['name']}  ·  {group['member_count']} thành viên"
        )
        if not self.chat_options_button.winfo_manager():
            self.chat_options_button.pack(side="right")
        self.messages.clear()
        self.displayed_message_ids.clear()
        self.current_wallpaper = self.selected_wallpaper_for_current_conversation()
        self.render_messages()
        for group_id, button in self.group_buttons.items():
            if button.winfo_exists():
                button.configure(
                    fg_color="#EEF3FF" if group_id == group["id"] else "transparent"
                )
        self.send_request(
            "group_history",
            {"group_id": group["id"]},
            lambda response: self.complete_group_history(response, group["id"]),
        )

    def complete_group_history(
        self, response: dict[str, Any], group_id: int
    ) -> None:
        """Thêm lịch sử nhóm vào giao diện nếu nhóm đó vẫn đang được chọn."""
        if self.screen != "chat" or not response.get("ok"):
            if self.screen == "chat" and not response.get("ok"):
                self.set_status(response.get("error", "Không thể tải lịch sử nhóm."), error=True)
            return
        if not self.selected_group or self.selected_group["id"] != group_id:
            return
        for message in response["data"]["messages"]:
            self.add_message(message)
        self.scroll_to_bottom()

    def complete_users(self, response: dict[str, Any]) -> None:
        """Lưu danh sách bạn bè và giữ hội thoại hiện tại nếu còn hợp lệ."""
        if self.screen != "chat":
            return
        if not response.get("ok"):
            self.set_status(response.get("error", "Không thể tải danh sách tài khoản."), error=True)
            return
        previous = self.selected_user["id"] if self.selected_user else None
        self.users = response["data"]["users"]
        if previous is not None:
            selected_user = next(
                (user for user in self.users if user["id"] == previous),
                None,
            )
            if selected_user is not None:
                self.selected_user = selected_user
                status = (
                    "đang hoạt động"
                    if selected_user["online"]
                    else "ngoại tuyến"
                )
                self.chat_title.configure(
                    text=f"{self.display_name_for_user(selected_user)}  ·  {status}"
                )
            else:
                self.selected_user = None
                self.chat_title.configure(
                    text="Chọn một người hoặc nhóm để bắt đầu trò chuyện"
                )
                self.chat_options_button.pack_forget()
                self.close_conversation_options(animate=False)
                self.current_wallpaper = None
                self.messages.clear()
                self.displayed_message_ids.clear()
                self.render_messages()
        self.render_users()

    def render_users(self) -> None:
        """Lọc tức thời danh sách bạn bè theo tên đang nhập."""
        if self.screen != "chat" or not hasattr(self, "user_list"):
            return
        self.nickname_labels.clear()
        for child in self.user_list.winfo_children():
            child.destroy()
        filtered_users = filter_items_by_name(
            self.users,
            self.friend_search_entry.get(),
            self.display_name_for_user,
        )
        for user in filtered_users:
            online = bool(user["online"])
            row = ctk.CTkFrame(
                self.user_list,
                fg_color=(
                    "#EEF3FF"
                    if self.selected_user
                    and user["id"] == self.selected_user["id"]
                    else "transparent"
                ),
                corner_radius=10,
            )
            row.pack(fill="x", pady=3)
            details = ctk.CTkFrame(row, fg_color="transparent")
            details.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=(8, 7))
            name = ctk.CTkLabel(
                details,
                text=self.display_name_for_user(user),
                text_color=INK,
                font=("Segoe UI", 13, "bold"),
                anchor="w",
            )
            name.pack(fill="x")
            self.nickname_labels[user["id"]] = name
            state = ctk.CTkLabel(
                details,
                text=f"●  {'Đang hoạt động' if online else 'Ngoại tuyến'}",
                text_color=GREEN if online else MUTED,
                font=("Segoe UI", 10),
                anchor="w",
            )
            state.pack(fill="x", pady=(1, 0))
            profile_button = ctk.CTkButton(
                row,
                text="Hồ sơ",
                width=62,
                height=30,
                corner_radius=8,
                fg_color="#EEF2FC",
                hover_color="#E2E9F6",
                text_color=PRIMARY,
                command=lambda target=user: self.view_user_profile(target),
            )
            profile_button.pack(side="right", padx=8)
            for widget in (row, details, name, state):
                widget.bind("<Button-1>", lambda _event, target=user: self.select_user(target))
        if not self.users:
            ctk.CTkLabel(
                self.user_list, text="Chưa có tài khoản nào khác.",
                text_color=MUTED, font=("Segoe UI", 12),
            ).pack(pady=18)
        elif not filtered_users:
            ctk.CTkLabel(
                self.user_list, text="Không tìm thấy bạn bè phù hợp.",
                text_color=MUTED, font=("Segoe UI", 11),
            ).pack(pady=12)

    def select_user(self, user: dict[str, Any]) -> None:
        """Mở cuộc trò chuyện cá nhân và tải lịch sử từ server."""
        if self.conversation_options_view is not None:
            self.close_conversation_options(animate=False)
        self.show_sidebar_tab("friends")
        self.selected_user = user
        self.selected_group = None
        for button in self.group_buttons.values():
            if button.winfo_exists():
                button.configure(fg_color="transparent")
        status = "đang hoạt động" if user["online"] else "ngoại tuyến"
        self.chat_title.configure(text=f"{self.display_name_for_user(user)}  ·  {status}")
        if not self.chat_options_button.winfo_manager():
            self.chat_options_button.pack(side="right")
        self.messages.clear()
        self.displayed_message_ids.clear()
        self.current_wallpaper = self.selected_wallpaper_for_current_conversation()
        self.render_messages()
        if self.wallpaper_settings_error:
            self.set_status(self.wallpaper_settings_error, error=True)
        conversation_id = user["id"]
        self.send_request(
            "history", {"user_id": conversation_id},
            lambda response: self.complete_history(response, conversation_id),
        )
        self.refresh_users()

    def display_name_for_user(self, user: dict[str, Any]) -> str:
        """Lấy tên người dùng theo biệt danh cục bộ nếu đã đặt."""
        if self.user is None:
            return str(user.get("display_name") or "Người dùng")
        return conversation_display_name(user, self.user["id"], self.nicknames)

    def view_user_profile(self, user: dict[str, Any]) -> None:
        """Yêu cầu thông tin công khai của người dùng được chọn."""
        self.screen = "public_profile"
        self.send_request(
            "public_profile",
            {"user_id": user["id"]},
            lambda response: self.show_public_profile(response, user),
        )

    def show_public_profile(
        self,
        response: dict[str, Any],
        selected_user: dict[str, Any],
    ) -> None:
        """Hiển thị các trường hồ sơ mà chủ tài khoản đã công khai."""
        if not response.get("ok"):
            if self.screen != "public_profile":
                return
            self.screen = "chat"
            self.set_status(response.get("error", "Không thể xem hồ sơ."), error=True)
            return
        if self.screen != "public_profile":
            return
        profile = response["data"]
        self.screen = "public_profile"
        self.clear_root()
        page = ctk.CTkFrame(self.root, fg_color=BG)
        page.pack(fill="both", expand=True, padx=28, pady=24)
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", pady=(0, 16))
        ctk.CTkButton(
            top,
            text="←  Quay lại chat",
            width=145,
            height=38,
            corner_radius=9,
            fg_color="white",
            hover_color="#E9EEF8",
            text_color=INK,
            command=self.return_to_chat,
        ).pack(side="left")
        ctk.CTkLabel(
            top, text="Hồ sơ người dùng", text_color=INK, font=("Segoe UI", 22, "bold")
        ).pack(side="left", padx=18)
        card = ctk.CTkFrame(page, fg_color=CARD, corner_radius=18, width=620)
        card.pack(anchor="n", fill="x", pady=10)
        card.pack_propagate(False)
        card.configure(height=440)
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=38, pady=30)
        display_name = self.display_name_for_user(selected_user)
        ctk.CTkLabel(
            content, text=display_name, text_color=INK, font=("Segoe UI", 23, "bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            content,
            text=f"{'Đang hoạt động' if selected_user['online'] else 'Ngoại tuyến'}",
            text_color=MUTED,
            font=("Segoe UI", 12),
        ).pack(anchor="w", pady=(4, 18))
        public_fields = (
            ("birth_date", "Ngày sinh"),
            ("interests", "Sở thích"),
            ("hometown", "Quê quán"),
        )
        shown_count = 0
        shown_count = int(bool(profile.get("display_name")))
        for key, label in public_fields:
            value = profile.get(key)
            if value:
                self.add_readonly_profile_field(content, label, str(value))
                shown_count += 1
        if shown_count == 0:
            ctk.CTkLabel(
                content,
                text="Người dùng chưa chia sẻ thêm thông tin cá nhân.",
                text_color=MUTED,
                font=("Segoe UI", 13),
            ).pack(anchor="w", pady=12)
        self.root.bind("<Escape>", self.go_back)

    @staticmethod
    def add_readonly_profile_field(
        parent: ctk.CTkFrame,
        label: str,
        value: str,
    ) -> None:
        """Thêm một cặp nhãn/giá trị hồ sơ chỉ đọc vào trang."""
        ctk.CTkLabel(
            parent, text=label, text_color=MUTED, font=("Segoe UI", 11)
        ).pack(anchor="w", pady=(10, 2))
        ctk.CTkLabel(
            parent, text=value, text_color=INK, font=("Segoe UI", 15), anchor="w"
        ).pack(anchor="w")

    def return_to_chat(self) -> None:
        """Dựng lại màn hình chat rồi tải lại danh sách và lịch sử đang mở."""
        self.show_chat()
        self.refresh_users()
        self.refresh_groups()
        if self.selected_user:
            conversation_id = self.selected_user["id"]
            self.send_request(
                "history",
                {"user_id": conversation_id},
                lambda response: self.complete_history(response, conversation_id),
            )
        elif self.selected_group:
            group_id = self.selected_group["id"]
            self.send_request(
                "group_history",
                {"group_id": group_id},
                lambda response: self.complete_group_history(response, group_id),
            )

    def complete_history(self, response: dict[str, Any], conversation_id: int) -> None:
        """Hiển thị lịch sử cá nhân nếu đúng cuộc trò chuyện đang được chọn."""
        if self.screen != "chat":
            return
        if not response.get("ok"):
            self.set_status(response.get("error", "Không thể tải lịch sử."), error=True)
            return
        if not self.selected_user or self.selected_user["id"] != conversation_id:
            return
        for message in response["data"]["messages"]:
            self.add_message(message)
        self.scroll_to_bottom()

    def add_message(self, message: dict[str, Any]) -> None:
        """Thêm tin nhắn mới một lần duy nhất vào dữ liệu và khung chat."""
        if self.screen != "chat" or not hasattr(self, "messages_canvas"):
            return
        message_id = message.get("id")
        if message_id in self.displayed_message_ids:
            return
        if message_id is not None:
            self.displayed_message_ids.add(message_id)
        self.messages.append(message)
        self.draw_message(message)
        self.update_message_scrollregion()
        self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        """Cuộn lịch sử xuống tin nhắn mới nhất sau khi bố cục hoàn tất."""
        if hasattr(self, "messages_canvas"):
            self.root.after(80, lambda: self.messages_canvas.yview_moveto(1.0))

    def selected_wallpaper_for_current_conversation(self) -> str | None:
        """Tìm ảnh nền lưu riêng cho cuộc trò chuyện cá nhân hoặc nhóm."""
        if not self.user:
            return None
        if self.selected_group:
            key = f"group:{self.selected_group['id']}"
        elif self.selected_user:
            key = conversation_key(self.user["id"], self.selected_user["id"])
        else:
            return None
        filename = self.wallpaper_settings.get(key)
        if filename is not None and filename not in {path.name for path in self.wallpaper_files}:
            self.wallpaper_settings_error = "Ảnh nền đã lưu không còn trong thư mục anh_nen."
            return None
        return filename

    def get_wallpaper_image(self, filename: str) -> Image.Image:
        """Đọc và cache ảnh nền để tránh mở lại tệp nhiều lần."""
        if filename not in self.wallpaper_images:
            path = next((item for item in self.wallpaper_files if item.name == filename), None)
            if path is None:
                raise ValueError("Ảnh nền không nằm trong thư mục anh_nen.")
            with Image.open(path) as image:
                self.wallpaper_images[filename] = image.convert("RGB")
        return self.wallpaper_images[filename]

    def render_messages(self, preserve_scroll: bool = False) -> None:
        """Vẽ lại nền và toàn bộ tin nhắn, có thể giữ vị trí cuộn."""
        if not hasattr(self, "messages_canvas") or not self.messages_canvas.winfo_exists():
            return
        canvas = self.messages_canvas
        old_position = canvas.yview()[0] if preserve_scroll else 1.0
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        canvas.delete("all")
        canvas.configure(background=CHAT_BACKGROUND)
        self.wallpaper_photo = None
        self.wallpaper_canvas_item = None
        if self.current_wallpaper:
            try:
                source = self.get_wallpaper_image(self.current_wallpaper)
                fitted_image = ImageOps.fit(
                    source, (width, height), method=Image.Resampling.LANCZOS
                )
                self.wallpaper_photo = ImageTk.PhotoImage(fitted_image, master=canvas)
                self.wallpaper_canvas_item = canvas.create_image(
                    0, 0, anchor="nw", image=self.wallpaper_photo, tags=("wallpaper",)
                )
            except (OSError, ValueError) as error:
                self.set_status(f"Không thể mở ảnh nền: {error}", error=True)
                self.current_wallpaper = None
        self.message_content_bottom = 12
        for message in self.messages:
            self.draw_message(message)
        self.update_message_scrollregion()
        canvas.yview_moveto(old_position)

    def draw_message(self, message: dict[str, Any]) -> None:
        """Vẽ tin hệ thống hoặc bong bóng tin nhắn, tệp và thời gian."""
        canvas = self.messages_canvas
        width = canvas.winfo_width()
        if width <= 1:
            return
        top = self.message_content_bottom
        if message.get("message_type") == "system":
            text = str(message.get("body") or "")
            timestamp = str(message.get("created_at") or "")
            item = canvas.create_text(
                width // 2,
                top + 8,
                text=f"{text}  ·  {timestamp}" if timestamp else text,
                width=max(width - 40, 1),
                anchor="n",
                justify="center",
                font=("Segoe UI", 11, "italic"),
                fill=MUTED,
            )
            bounds = canvas.bbox(item)
            self.message_content_bottom = (bounds[3] if bounds else top + 24) + 10
            return
        max_bubble_width = max(180, min(520, width - 48))
        text_width = max_bubble_width - 24
        content_y = top + 10
        content_items: list[tuple[int, bool]] = []
        measured_widths: list[int] = []
        mine = bool(self.user and message.get("sender_id") == self.user["id"])

        sender_name = message.get("sender_name")
        if self.selected_group and sender_name and not mine:
            sender_item = canvas.create_text(
                0, content_y, text=str(sender_name), width=text_width, anchor="nw",
                font=self.message_link_font, fill=PRIMARY,
            )
            sender_bounds = canvas.bbox(sender_item)
            if sender_bounds:
                measured_widths.append(sender_bounds[2] - sender_bounds[0])
                content_y = sender_bounds[3] + 5
            content_items.append((sender_item, False))
        body = str(message.get("body") or "")
        if body:
            body_item = canvas.create_text(
                0, content_y, text=body, width=text_width, anchor="nw",
                justify="left", font=self.message_font, fill=INK,
            )
            body_bounds = canvas.bbox(body_item)
            if body_bounds:
                measured_widths.append(body_bounds[2] - body_bounds[0])
                content_y = body_bounds[3] + 5
            content_items.append((body_item, False))

        attachment_name = message.get("attachment_name")
        if attachment_name:
            message_id = message.get("id")
            tags = ("attachment", f"download:{message_id}") if message_id is not None else ("attachment",)
            attachment_item = canvas.create_text(
                0, content_y, text=f"↓  {attachment_name}", width=text_width,
                anchor="nw", font=self.message_link_font, fill=PRIMARY, tags=tags,
            )
            attachment_bounds = canvas.bbox(attachment_item)
            if attachment_bounds:
                measured_widths.append(attachment_bounds[2] - attachment_bounds[0])
                content_y = attachment_bounds[3] + 5
            content_items.append((attachment_item, False))

        timestamp = str(message.get("created_at") or "")
        timestamp_item = canvas.create_text(
            0, content_y, text=timestamp, anchor="nw", font=self.message_time_font,
            fill=MUTED,
        )
        timestamp_bounds = canvas.bbox(timestamp_item)
        timestamp_width = (
            timestamp_bounds[2] - timestamp_bounds[0] if timestamp_bounds else 0
        )
        measured_widths.append(timestamp_width)
        content_items.append((timestamp_item, True))
        bubble_width = min(
            max_bubble_width,
            max([88, *(item_width + 24 for item_width in measured_widths)]),
        )
        bubble_left = width - bubble_width - 12 if mine else 12
        for item, align_right in content_items:
            bounds = canvas.bbox(item)
            item_width = bounds[2] - bounds[0] if bounds else 0
            x = bubble_left + bubble_width - 12 - item_width if align_right else bubble_left + 12
            canvas.coords(item, x, canvas.coords(item)[1])

        last_bounds = canvas.bbox(timestamp_item)
        bubble_bottom = (last_bounds[3] if last_bounds else content_y) + 8
        bubble = canvas.create_rectangle(
            bubble_left, top, bubble_left + bubble_width, bubble_bottom,
            fill="#DDE8FF" if mine else "#FFFFFF", outline="",
        )
        canvas.tag_lower(bubble, content_items[0][0])
        self.message_content_bottom = bubble_bottom + 10

    def update_message_scrollregion(self) -> None:
        """Cập nhật vùng cuộn theo chiều cao nội dung tin nhắn hiện tại."""
        if not hasattr(self, "messages_canvas") or not self.messages_canvas.winfo_exists():
            return
        canvas = self.messages_canvas
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        content_height = max(height, getattr(self, "message_content_bottom", 12))
        canvas.configure(scrollregion=(0, 0, width, content_height))
        if self.wallpaper_canvas_item is not None:
            canvas.coords(self.wallpaper_canvas_item, 0, canvas.canvasy(0))
            canvas.tag_lower(self.wallpaper_canvas_item)

    def update_message_scrollbar(self, first: str, last: str) -> None:
        """Đồng bộ thanh cuộn và vị trí ảnh nền trong canvas."""
        if hasattr(self, "messages_scrollbar") and self.messages_scrollbar.winfo_exists():
            self.messages_scrollbar.set(first, last)
        if (
            hasattr(self, "messages_canvas")
            and self.wallpaper_canvas_item is not None
            and self.messages_canvas.winfo_exists()
        ):
            self.messages_canvas.coords(
                self.wallpaper_canvas_item, 0, self.messages_canvas.canvasy(0)
            )

    def on_messages_canvas_configure(self, _event: Any) -> None:
        """Vẽ lại tin nhắn khi kích thước vùng hội thoại thay đổi."""
        self.render_messages(preserve_scroll=True)

    def on_messages_mousewheel(self, event: Any) -> str:
        """Cuộn lịch sử bằng bánh xe chuột và chặn hành vi mặc định."""
        self.messages_canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def on_message_canvas_click(self, _event: Any) -> None:
        """Bắt thao tác bấm tệp đính kèm để yêu cầu tải tệp."""
        current_items = self.messages_canvas.find_withtag("current")
        if not current_items:
            return
        for tag in self.messages_canvas.gettags(current_items[0]):
            if tag.startswith("download:"):
                self.download_attachment(
                    int(tag.removeprefix("download:")),
                    self.selected_group["id"] if self.selected_group else None,
                )
                return

    def on_message_canvas_motion(self, _event: Any) -> None:
        """Đổi con trỏ khi rê chuột lên liên kết tải tệp."""
        current_items = self.messages_canvas.find_withtag("current")
        tags = self.messages_canvas.gettags(current_items[0]) if current_items else ()
        self.messages_canvas.configure(
            cursor="hand2" if any(tag.startswith("download:") for tag in tags) else ""
        )

    def open_conversation_menu(self) -> None:
        """Mở trang tùy chọn của cuộc trò chuyện hiện tại."""
        if not self.selected_user and not self.selected_group:
            self.set_status("Hãy chọn một cuộc trò chuyện trước.")
            return
        if self.conversation_options_view is not None:
            return
        self.conversation_options_view = ctk.CTkFrame(
            self.chat_frame, fg_color=CARD, corner_radius=16,
        )
        self.conversation_options_view.place(
            x=-max(self.chat_frame.winfo_width(), 1),
            y=0,
            relwidth=1,
            relheight=1,
        )
        self.build_conversation_options_page(self.conversation_options_view)
        self.animate_conversation_options(0, 1)

    def build_conversation_options_page(self, page: ctk.CTkFrame) -> None:
        """Dựng danh sách chức năng phù hợp với chat cá nhân hoặc nhóm."""
        self.group_members_content = None
        self.clear_conversation_options_page(page)
        header = ctk.CTkFrame(page, fg_color="transparent", height=66)
        header.pack(fill="x", padx=18)
        header.pack_propagate(False)
        ctk.CTkButton(
            header, text="←", width=38, height=36, corner_radius=9,
            fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
            font=("Segoe UI", 20, "bold"), command=self.close_conversation_options,
        ).pack(side="left")
        ctk.CTkLabel(
            header, text="Tùy chọn cuộc trò chuyện", text_color=INK,
            font=("Segoe UI", 17, "bold"),
        ).pack(side="left", padx=12)

        content = ctk.CTkFrame(page, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=22, pady=(8, 20))
        if self.selected_user:
            ctk.CTkButton(
                content, text="Hình nền", height=52, corner_radius=10,
                fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
                font=("Segoe UI", 14, "bold"), anchor="w",
                command=lambda: self.show_conversation_feature("wallpaper"),
            ).pack(fill="x", pady=(0, 10))
            ctk.CTkButton(
                content, text="Biệt danh", height=52, corner_radius=10,
                fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
                font=("Segoe UI", 14, "bold"), anchor="w",
                command=lambda: self.show_conversation_feature("nickname"),
            ).pack(fill="x", pady=(0, 10))
            ctk.CTkButton(
                content, text="Tạo nhóm", height=52, corner_radius=10,
                fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
                font=("Segoe UI", 14, "bold"), anchor="w",
                command=lambda: self.show_conversation_feature("create_group"),
            ).pack(fill="x")
        elif self.selected_group:
            for label, feature in (
                ("Xem thành viên", "members"),
                ("Đổi ảnh nền chat", "wallpaper"),
                ("Đổi tên nhóm", "rename_group"),
                ("Thêm người mới vào nhóm", "add_members"),
            ):
                ctk.CTkButton(
                    content, text=label, height=52, corner_radius=10,
                    fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
                    font=("Segoe UI", 14, "bold"), anchor="w",
                    command=lambda selected=feature: self.show_conversation_feature(
                        selected
                    ),
                ).pack(fill="x", pady=(0, 10))
            ctk.CTkButton(
                content, text="Rời nhóm", height=52, corner_radius=10,
                fg_color="#FDECEC", hover_color="#F9DADA", text_color="#B44646",
                font=("Segoe UI", 14, "bold"), anchor="w",
                command=self.confirm_leave_group,
            ).pack(fill="x")

    @staticmethod
    def clear_conversation_options_page(page: ctk.CTkFrame) -> None:
        """Xóa widget cũ trước khi dựng trang tùy chọn kế tiếp."""
        for child in page.winfo_children():
            child.destroy()

    def show_conversation_feature(self, feature: str) -> None:
        """Mở trang chi tiết cho một chức năng trong menu ba chấm."""
        page = self.conversation_options_view
        if page is None or not page.winfo_exists():
            return
        if feature not in {
            "wallpaper", "nickname", "create_group", "rename_group", "add_members",
            "members",
        }:
            return
        if feature in {"rename_group", "add_members", "members"} and not self.selected_group:
            return
        self.group_members_content = None
        self.clear_conversation_options_page(page)
        header = ctk.CTkFrame(page, fg_color="transparent", height=66)
        header.pack(fill="x", padx=18)
        header.pack_propagate(False)
        ctk.CTkButton(
            header, text="←", width=38, height=36, corner_radius=9,
            fg_color="#F0F4FC", hover_color="#E3EAF8", text_color=INK,
            font=("Segoe UI", 20, "bold"),
            command=lambda: self.build_conversation_options_page(page),
        ).pack(side="left")
        title = {
            "wallpaper": "Hình nền",
            "nickname": "Biệt danh",
            "create_group": "Tạo nhóm",
            "rename_group": "Đổi tên nhóm",
            "add_members": "Thêm thành viên",
            "members": "Thành viên nhóm",
        }[feature]
        ctk.CTkLabel(
            header, text=title, text_color=INK, font=("Segoe UI", 17, "bold"),
        ).pack(side="left", padx=12)
        content = ctk.CTkFrame(page, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=22, pady=(8, 20))
        if feature == "wallpaper":
            self.build_wallpaper_feature(content)
        elif feature == "nickname":
            self.build_nickname_feature(content)
        elif feature == "rename_group":
            self.build_group_rename_feature(content)
        elif feature == "add_members":
            self.load_group_add_member_feature(content)
        elif feature == "members":
            self.load_group_members_feature(content)
        else:
            self.build_group_creation_page(content)

    def open_group_creation_page(self) -> None:
        """Mở biểu mẫu tạo nhóm từ nút cộng trong danh sách nhóm."""
        if self.conversation_options_view is None:
            self.conversation_options_view = ctk.CTkFrame(
                self.chat_frame, fg_color=CARD, corner_radius=16,
            )
            self.conversation_options_view.place(
                x=-max(self.chat_frame.winfo_width(), 1),
                y=0,
                relwidth=1,
                relheight=1,
            )
            self.animate_conversation_options(0, 1)
        self.show_conversation_feature("create_group")

    def build_group_creation_page(self, content: ctk.CTkFrame) -> None:
        """Dựng biểu mẫu nhập tên nhóm và chọn thành viên ban đầu."""
        ctk.CTkLabel(
            content, text="Đặt tên nhóm", text_color=INK,
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.group_name_entry = ctk.CTkEntry(
            content, height=40, corner_radius=9, border_color="#DFE5EF",
            placeholder_text="Ví dụ: Nhóm bạn thân",
        )
        self.group_name_entry.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(
            content, text="Chọn ít nhất 2 người khác (tối đa 49).",
            text_color=MUTED, font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(0, 8))
        self.group_member_vars: dict[int, BooleanVar] = {}
        member_list = ctk.CTkScrollableFrame(content, fg_color="#F6F8FC", height=230)
        member_list.pack(fill="both", expand=True, pady=(0, 12))
        preselected_id = self.selected_user["id"] if self.selected_user else None
        for user in self.users:
            variable = BooleanVar(value=user["id"] == preselected_id)
            self.group_member_vars[user["id"]] = variable
            ctk.CTkCheckBox(
                member_list,
                text=self.display_name_for_user(user),
                variable=variable,
                onvalue=True,
                offvalue=False,
                text_color=INK,
                fg_color=PRIMARY,
                hover_color="#315BC0",
                border_color="#BAC5D8",
                font=("Segoe UI", 12),
            ).pack(anchor="w", padx=12, pady=7)
        if len(self.users) < 2:
            ctk.CTkLabel(
                content, text="Cần ít nhất 2 người bạn để tạo nhóm.",
                text_color="#C44545", font=("Segoe UI", 11),
            ).pack(anchor="w", pady=(0, 8))
        ctk.CTkButton(
            content, text="Tạo nhóm", height=40, corner_radius=9,
            fg_color=PRIMARY, hover_color="#315BC0",
            command=self.submit_group_creation,
        ).pack(anchor="e")

    def submit_group_creation(self) -> None:
        """Kiểm tra lựa chọn rồi gửi yêu cầu tạo nhóm tới server."""
        group_name = self.group_name_entry.get().strip()
        member_ids = [
            user_id for user_id, selected in self.group_member_vars.items()
            if selected.get()
        ]
        if not group_name:
            self.set_status("Hãy nhập tên nhóm.", error=True)
            return
        if len(member_ids) < 2:
            self.set_status("Hãy chọn ít nhất hai người khác để tạo nhóm.", error=True)
            return

        def created(response: dict[str, Any]) -> None:
            """Mở nhóm vừa tạo và cập nhật các danh sách liên quan."""
            if not response.get("ok"):
                self.set_status(response.get("error", "Không thể tạo nhóm."), error=True)
                return
            group = response["data"]["group"]
            self.selected_user = None
            self.selected_group = group
            self.refresh_users()
            self.refresh_groups()
            self.select_group(group)
            self.set_status(f"Đã tạo nhóm {group['name']}.")

        self.send_request(
            "create_group",
            {"name": group_name, "member_ids": member_ids},
            created,
        )

    def apply_group_update(self, data: dict[str, Any]) -> None:
        """Áp dụng tên/số thành viên mới và tin hoạt động vào nhóm đang mở."""
        group = data.get("group")
        if not isinstance(group, dict):
            return
        self.refresh_groups()
        if not self.selected_group or self.selected_group["id"] != group.get("id"):
            return
        self.selected_group.update(group)
        self.chat_title.configure(
            text=f"{group['name']}  ·  {group['member_count']} thành viên"
        )
        message = data.get("message")
        if isinstance(message, dict):
            self.add_message(message)

    def build_group_rename_feature(self, content: ctk.CTkFrame) -> None:
        """Dựng ô nhập tên mới cho nhóm đang trò chuyện."""
        if not self.selected_group:
            return
        ctk.CTkLabel(
            content, text="Tên nhóm mới", text_color=INK,
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.group_rename_entry = ctk.CTkEntry(
            content, height=42, corner_radius=9, border_color="#DFE5EF",
            placeholder_text="Nhập tên nhóm",
        )
        self.group_rename_entry.insert(0, self.selected_group["name"])
        self.group_rename_entry.pack(fill="x", pady=(0, 12))
        ctk.CTkButton(
            content, text="Lưu tên nhóm", height=40, corner_radius=9,
            fg_color=PRIMARY, hover_color="#315BC0",
            command=self.submit_group_rename,
        ).pack(anchor="e")

    def submit_group_rename(self) -> None:
        """Gửi tên nhóm mới và cập nhật giao diện theo kết quả server."""
        if not self.selected_group:
            return
        name = self.group_rename_entry.get().strip()
        if not name or len(name) > 80:
            self.set_status("Tên nhóm không được để trống và tối đa 80 ký tự.", error=True)
            return
        group_id = self.selected_group["id"]

        def renamed(response: dict[str, Any]) -> None:
            """Đóng trang đổi tên khi server cập nhật thành công."""
            if not response.get("ok"):
                self.set_status(response.get("error", "Không thể đổi tên nhóm."), error=True)
                return
            self.apply_group_update(response["data"])
            self.close_conversation_options(animate=False)
            self.set_status("Đã đổi tên nhóm.")

        self.send_request(
            "rename_group", {"group_id": group_id, "name": name}, renamed
        )

    def load_group_add_member_feature(self, content: ctk.CTkFrame) -> None:
        """Tải thành viên hiện tại rồi chỉ cho chọn người chưa tham gia."""
        if not self.selected_group:
            return
        group_id = self.selected_group["id"]
        ctk.CTkLabel(
            content, text="Đang tải thành viên nhóm…", text_color=MUTED,
            font=("Segoe UI", 12),
        ).pack(anchor="w")

        def loaded(response: dict[str, Any]) -> None:
            """Hiển thị danh sách có thể thêm sau khi tải thành viên xong."""
            if not content.winfo_exists():
                return
            for child in content.winfo_children():
                child.destroy()
            if not response.get("ok"):
                ctk.CTkLabel(
                    content, text=response.get("error", "Không tải được thành viên."),
                    text_color="#C44545", font=("Segoe UI", 12),
                ).pack(anchor="w")
                return
            existing_ids = {
                member["id"] for member in response["data"]["members"]
            }
            self.group_add_member_vars = {}
            member_list = ctk.CTkScrollableFrame(
                content, fg_color="#F6F8FC", height=300
            )
            member_list.pack(fill="both", expand=True, pady=(0, 12))
            available_users = [
                user for user in self.users if user["id"] not in existing_ids
            ]
            for user in available_users:
                variable = BooleanVar(value=False)
                self.group_add_member_vars[user["id"]] = variable
                ctk.CTkCheckBox(
                    member_list,
                    text=self.display_name_for_user(user),
                    variable=variable,
                    onvalue=True,
                    offvalue=False,
                    text_color=INK,
                    fg_color=PRIMARY,
                    hover_color="#315BC0",
                    border_color="#BAC5D8",
                    font=("Segoe UI", 12),
                ).pack(anchor="w", padx=12, pady=7)
            if not available_users:
                ctk.CTkLabel(
                    content, text="Không còn người nào để thêm.",
                    text_color=MUTED, font=("Segoe UI", 11),
                ).pack(anchor="w", pady=(0, 8))
            ctk.CTkButton(
                content, text="Thêm vào nhóm", height=40, corner_radius=9,
                fg_color=PRIMARY, hover_color="#315BC0",
                command=self.submit_group_add_members,
            ).pack(anchor="e")

        self.send_request("group_members", {"group_id": group_id}, loaded)

    def load_group_members_feature(self, content: ctk.CTkFrame) -> None:
        """Tải danh sách thành viên và trạng thái trưởng nhóm của nhóm hiện tại."""
        if not self.selected_group:
            return
        self.group_members_content = content
        for child in content.winfo_children():
            child.destroy()
        group_id = self.selected_group["id"]
        ctk.CTkLabel(
            content, text="Đang tải thành viên nhóm…", text_color=MUTED,
            font=("Segoe UI", 12),
        ).pack(anchor="w")

        def loaded(response: dict[str, Any]) -> None:
            """Hiển thị vai trò và quyền quản lý khi dữ liệu thành viên đã tải."""
            if (
                self.group_members_content is not content
                or not content.winfo_exists()
                or not self.selected_group
                or self.selected_group["id"] != group_id
            ):
                return
            for child in content.winfo_children():
                child.destroy()
            if not response.get("ok"):
                ctk.CTkLabel(
                    content, text=response.get("error", "Không tải được thành viên."),
                    text_color="#C44545", font=("Segoe UI", 12),
                ).pack(anchor="w")
                return
            data = response["data"]
            members = data.get("members", [])
            leader_id = data.get("leader_id")
            creator_id = data.get("creator_id")
            current_user_id = (self.user or {}).get("id")
            is_leader = current_user_id == leader_id
            member_list = ctk.CTkScrollableFrame(
                content, fg_color="#F6F8FC", height=330
            )
            member_list.pack(fill="both", expand=True)
            for member in members:
                row = ctk.CTkFrame(
                    member_list, fg_color=CARD, corner_radius=9
                )
                row.pack(fill="x", padx=4, pady=4)
                name_area = ctk.CTkFrame(row, fg_color="transparent")
                name_area.pack(fill="x", padx=10, pady=(8, 0))
                ctk.CTkLabel(
                    name_area,
                    text=member["display_name"],
                    text_color=INK,
                    font=("Segoe UI", 12, "bold"),
                    anchor="w",
                ).pack(anchor="w")
                ctk.CTkLabel(
                    name_area,
                    text=group_member_role_text(
                        member["id"], leader_id, creator_id
                    ),
                    text_color=(
                        PRIMARY if member["id"] == leader_id else MUTED
                    ),
                    font=("Segoe UI", 10),
                    anchor="w",
                ).pack(anchor="w")
                if is_leader and member["id"] != current_user_id:
                    actions = ctk.CTkFrame(row, fg_color="transparent")
                    actions.pack(fill="x", padx=10, pady=(4, 8))
                    ctk.CTkButton(
                        actions,
                        text="Chuyển trưởng nhóm",
                        width=126,
                        height=30,
                        corner_radius=7,
                        fg_color="#EEF3FF",
                        hover_color="#DFE8FC",
                        text_color=PRIMARY,
                        font=("Segoe UI", 10, "bold"),
                        command=lambda selected=member: self.confirm_transfer_group_leadership(
                            selected["id"], selected["display_name"], content
                        ),
                    ).pack(side="left")
                    ctk.CTkButton(
                        actions,
                        text="Kick khỏi nhóm",
                        width=100,
                        height=30,
                        corner_radius=7,
                        fg_color="#FDECEC",
                        hover_color="#F9DADA",
                        text_color="#B44646",
                        font=("Segoe UI", 10, "bold"),
                        command=lambda selected=member: self.confirm_remove_group_member(
                            selected["id"], selected["display_name"], content
                        ),
                    ).pack(side="left", padx=(6, 0))
            if not members:
                ctk.CTkLabel(
                    content, text="Nhóm hiện không có thành viên.",
                    text_color=MUTED, font=("Segoe UI", 11),
                ).pack(anchor="w", pady=8)

        self.send_request("group_members", {"group_id": group_id}, loaded)

    def confirm_remove_group_member(
        self, member_id: int, display_name: str, content: ctk.CTkFrame
    ) -> None:
        """Xác nhận rồi gửi yêu cầu trưởng nhóm xóa thành viên."""
        if not self.selected_group:
            return
        if not messagebox.askyesno(
            "Xóa thành viên",
            f"Bạn có chắc muốn xóa {display_name} khỏi nhóm?",
        ):
            return
        group_id = self.selected_group["id"]
        self.send_group_member_action(
            "remove_group_member",
            {"group_id": group_id, "member_id": member_id},
            content,
            "Đã xóa thành viên khỏi nhóm.",
        )

    def confirm_transfer_group_leadership(
        self, member_id: int, display_name: str, content: ctk.CTkFrame
    ) -> None:
        """Xác nhận rồi chuyển quyền trưởng nhóm cho thành viên đã chọn."""
        if not self.selected_group:
            return
        if not messagebox.askyesno(
            "Chuyển trưởng nhóm",
            f"Chuyển quyền trưởng nhóm cho {display_name}?",
        ):
            return
        group_id = self.selected_group["id"]
        self.send_group_member_action(
            "transfer_group_leadership",
            {"group_id": group_id, "member_id": member_id},
            content,
            f"Đã chuyển quyền trưởng nhóm cho {display_name}.",
        )

    def send_group_member_action(
        self,
        action: str,
        data: dict[str, Any],
        content: ctk.CTkFrame,
        success_message: str,
    ) -> None:
        """Cập nhật nhóm sau thao tác quản lý thành viên thành công."""
        def updated(response: dict[str, Any]) -> None:
            if not response.get("ok"):
                self.set_status(
                    response.get("error", "Không thể cập nhật thành viên nhóm."),
                    error=True,
                )
                return
            result = response["data"]
            self.apply_group_update(result)
            self.set_status(success_message)
            if (
                self.user
                and result.get("removed_user_id") == self.user["id"]
            ):
                self.close_conversation_options(animate=False)
                return
            if (
                self.group_members_content is content
                and content.winfo_exists()
            ):
                self.load_group_members_feature(content)

        self.send_request(action, data, updated)

    def submit_group_add_members(self) -> None:
        """Gửi những người được chọn để thêm vào nhóm hiện tại."""
        if not self.selected_group:
            return
        member_ids = [
            member_id
            for member_id, selected in self.group_add_member_vars.items()
            if selected.get()
        ]
        if not member_ids:
            self.set_status("Chọn ít nhất một người để thêm vào nhóm.", error=True)
            return
        group_id = self.selected_group["id"]

        def added(response: dict[str, Any]) -> None:
            """Cập nhật danh sách nhóm và đóng trang khi thêm thành công."""
            if not response.get("ok"):
                self.set_status(response.get("error", "Không thể thêm thành viên."), error=True)
                return
            self.apply_group_update(response["data"])
            self.close_conversation_options(animate=False)
            self.set_status("Đã thêm thành viên vào nhóm.")

        self.send_request(
            "add_group_members",
            {"group_id": group_id, "member_ids": member_ids},
            added,
        )

    def confirm_leave_group(self) -> None:
        """Yêu cầu xác nhận trước khi rời nhóm."""
        if not self.selected_group:
            return
        group_name = self.selected_group["name"]
        if messagebox.askyesno(
            "Rời nhóm", f"Bạn có chắc muốn rời nhóm “{group_name}”?"
        ):
            self.leave_selected_group()

    def leave_selected_group(self) -> None:
        """Rời nhóm trên server rồi xóa hội thoại khỏi màn hình hiện tại."""
        if not self.selected_group:
            return
        group_id = self.selected_group["id"]

        def left(response: dict[str, Any]) -> None:
            """Dọn trạng thái hội thoại sau khi server xác nhận đã rời."""
            if not response.get("ok"):
                self.set_status(response.get("error", "Không thể rời nhóm."), error=True)
                return
            self.close_conversation_options(animate=False)
            self.selected_group = None
            self.messages.clear()
            self.displayed_message_ids.clear()
            self.current_wallpaper = None
            self.chat_title.configure(
                text="Chọn một người hoặc nhóm để bắt đầu trò chuyện"
            )
            if not self.selected_user:
                self.chat_options_button.pack_forget()
            self.render_messages()
            self.refresh_groups()
            self.set_status("Bạn đã rời nhóm.")

        self.send_request("leave_group", {"group_id": group_id}, left)

    def build_wallpaper_feature(self, content: ctk.CTkFrame) -> None:
        """Dựng lựa chọn ảnh nền có sẵn và nút khôi phục mặc định."""
        ctk.CTkLabel(
            content, text="Chọn hình nền cho cuộc trò chuyện này.",
            text_color=MUTED, font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(0, 14))
        if not self.wallpaper_files:
            ctk.CTkLabel(
                content, text=f"Không tìm thấy ảnh trong thư mục:\n{WALLPAPER_DIRECTORY}",
                text_color="#C44545", font=("Segoe UI", 12), justify="left",
            ).pack(anchor="w")
            return
        choices = ctk.CTkFrame(content, fg_color="transparent")
        choices.pack(fill="x", anchor="n")
        selected_filename = self.current_wallpaper
        for index, path in enumerate(self.wallpaper_files, start=1):
            try:
                image = ImageOps.fit(
                    self.get_wallpaper_image(path.name),
                    (180, 130),
                    method=Image.Resampling.LANCZOS,
                )
            except (OSError, ValueError) as error:
                self.set_status(f"Không thể mở ảnh nền {path.name}: {error}", error=True)
                return
            preview = ctk.CTkImage(light_image=image, dark_image=image, size=(180, 130))
            ctk.CTkButton(
                choices,
                text=f"Hình nền {index}" + ("  ✓" if path.name == selected_filename else ""),
                image=preview,
                compound="top",
                width=194,
                height=180,
                corner_radius=10,
                fg_color="#EEF2FC",
                hover_color="#E2E9F6",
                text_color=INK,
                border_width=2 if path.name == selected_filename else 0,
                border_color=PRIMARY,
                command=lambda filename=path.name: self.set_conversation_wallpaper(filename),
            ).pack(side="left", fill="y", expand=True, padx=5)
        ctk.CTkButton(
            content, text="Đặt lại nền mặc định", width=175, height=36, corner_radius=8,
            fg_color="#EEF2FA", hover_color="#E2E9F6", text_color=INK,
            command=lambda: self.set_conversation_wallpaper(None),
        ).pack(anchor="w", pady=(16, 0))

    def build_nickname_feature(self, content: ctk.CTkFrame) -> None:
        """Dựng biểu mẫu đặt hoặc xóa biệt danh cục bộ cho bạn bè."""
        if not self.selected_user or not self.user:
            return
        key = conversation_key(self.user["id"], self.selected_user["id"])
        current_nickname = self.nicknames.get(key, "")
        ctk.CTkLabel(
            content,
            text="Biệt danh chỉ hiển thị trên máy bạn, thay cho tên người này trong danh sách và khung chat.",
            text_color=MUTED, font=("Segoe UI", 11), wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, 12))
        nickname_entry = ctk.CTkEntry(
            content, height=42, corner_radius=9, border_color="#DFE5EF",
            placeholder_text="Nhập biệt danh",
        )
        nickname_entry.pack(fill="x", pady=(0, 12))
        if current_nickname:
            nickname_entry.insert(0, current_nickname)
        ctk.CTkButton(
            content, text="Lưu biệt danh", height=40, corner_radius=9,
            fg_color=PRIMARY, hover_color="#315BC0",
            command=lambda: self.save_conversation_nickname(nickname_entry.get()),
        ).pack(anchor="w")
        ctk.CTkButton(
            content, text="Đặt lại tên ban đầu", height=36, corner_radius=9,
            fg_color="#EEF2FA", hover_color="#E2E9F6", text_color=INK,
            command=lambda: self.save_conversation_nickname(""),
        ).pack(anchor="w", pady=(10, 0))

    def save_conversation_nickname(self, nickname: str) -> None:
        """Lưu biệt danh cục bộ và cập nhật tên trong danh sách/chat."""
        if not self.user or not self.selected_user:
            self.set_status("Hãy chọn một cuộc trò chuyện trước.")
            return
        nickname = nickname.strip()
        if len(nickname) > 40:
            self.set_status("Biệt danh không được dài quá 40 ký tự.", error=True)
            return
        key = conversation_key(self.user["id"], self.selected_user["id"])
        updated_nicknames = self.nicknames.copy()
        if nickname:
            updated_nicknames[key] = nickname
        else:
            updated_nicknames.pop(key, None)
        try:
            save_wallpaper_preferences(self.nickname_settings_path, updated_nicknames)
        except OSError as error:
            self.set_status(f"Không thể lưu biệt danh: {error}", error=True)
            return
        self.nicknames = updated_nicknames
        selected_id = self.selected_user["id"]
        for user in self.users:
            if user["id"] == selected_id:
                self.selected_user = user
                label = self.nickname_labels.get(selected_id)
                if label is not None and label.winfo_exists():
                    label.configure(text=self.display_name_for_user(user))
                break
        if self.selected_user:
            status = "đang hoạt động" if self.selected_user["online"] else "ngoại tuyến"
            self.chat_title.configure(
                text=f"{self.display_name_for_user(self.selected_user)}  ·  {status}"
            )
        self.nickname_settings_error = ""
        self.set_status(
            "Đã đặt lại tên ban đầu." if not nickname else "Đã lưu biệt danh."
        )

    def animate_conversation_options(self, step: int, direction: int) -> None:
        """Trượt trang tùy chọn vào/ra trong khung chat."""
        page = self.conversation_options_view
        if page is None or not page.winfo_exists():
            return
        steps = 10
        width = max(self.chat_frame.winfo_width(), 1)
        progress = step / steps
        x = int(-width * (1 - progress)) if direction > 0 else int(width * progress)
        page.place_configure(x=x)
        if step >= steps:
            if direction < 0:
                page.destroy()
                self.conversation_options_view = None
            return
        self.root.after(
            15,
            lambda: self.animate_conversation_options(step + 1, direction),
        )

    def close_conversation_options(self, animate: bool = True) -> None:
        """Đóng trang tùy chọn có hoặc không dùng hiệu ứng trượt."""
        if self.conversation_options_view is None:
            return
        self.group_members_content = None
        if animate:
            self.animate_conversation_options(0, -1)
        else:
            self.conversation_options_view.destroy()
            self.conversation_options_view = None

    def set_conversation_wallpaper(self, filename: str | None) -> None:
        """Lưu ảnh nền cho hội thoại hiện tại và vẽ lại khung tin nhắn."""
        if not self.user or (not self.selected_user and not self.selected_group):
            self.set_status("Hãy chọn một cuộc trò chuyện trước.")
            return
        if filename is not None:
            if filename not in {path.name for path in self.wallpaper_files}:
                self.set_status("Ảnh nền đã chọn không tồn tại.", error=True)
                return
            try:
                self.get_wallpaper_image(filename)
            except (OSError, ValueError) as error:
                self.set_status(f"Không thể mở ảnh nền: {error}", error=True)
                return
        key = (
            f"group:{self.selected_group['id']}"
            if self.selected_group
            else conversation_key(self.user["id"], self.selected_user["id"])
        )
        updated_settings = self.wallpaper_settings.copy()
        if filename is None:
            updated_settings.pop(key, None)
        else:
            updated_settings[key] = filename
        try:
            save_wallpaper_preferences(self.wallpaper_settings_path, updated_settings)
        except OSError as error:
            self.set_status(f"Không thể lưu hình nền: {error}", error=True)
            return
        self.wallpaper_settings = updated_settings
        self.current_wallpaper = filename
        self.wallpaper_settings_error = ""
        self.render_messages(preserve_scroll=True)
        self.close_conversation_options(animate=False)
        self.set_status(
            "Đã đặt lại hình nền." if filename is None else "Đã đổi hình nền cuộc trò chuyện."
        )

    def send_text(self) -> None:
        """Gửi nội dung ô soạn tới chat cá nhân hoặc nhóm đang mở."""
        if not self.selected_user and not self.selected_group:
            self.set_status("Hãy chọn một tài khoản hoặc nhóm trước.")
            return
        body = self.message_entry.get()
        if not body.strip():
            return
        is_group = self.selected_group is not None
        request_data = (
            {"group_id": self.selected_group["id"]}
            if self.selected_group
            else {"recipient_id": self.selected_user["id"]}
        )
        request_data["body"] = body

        def sent(response: dict[str, Any]) -> None:
            """Xóa ô nhập và thêm tin vào khung chat sau phản hồi thành công."""
            if not response.get("ok"):
                self.set_status(response.get("error", "Không thể gửi tin nhắn."), error=True)
                return
            self.add_message(response["data"]["message"])
            self.message_entry.delete(0, "end")
            self.set_status("Đã gửi tin nhắn.")

        # Chọn action theo loại hội thoại; server chịu trách nhiệm lưu và chuyển tiếp.
        self.send_request(
            "send_group_message" if is_group else "send_message",
            request_data,
            sent,
        )

    def choose_attachment(self) -> None:
        """Chọn tệp, mã hóa nội dung và gửi tới hội thoại hiện tại."""
        if not self.selected_user and not self.selected_group:
            self.set_status("Hãy chọn một tài khoản hoặc nhóm trước.")
            return
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Chọn ảnh hoặc tệp (tối đa 5 MB)",
            filetypes=[
                ("Ảnh và tệp phổ biến", "*.png *.jpg *.jpeg *.gif *.webp *.pdf *.doc *.docx *.txt *.zip"),
                ("Tất cả tệp", "*.*"),
            ],
        )
        if not filename:
            return
        path = Path(filename)
        try:
            size = path.stat().st_size
            if size > MAX_UPLOAD_BYTES:
                self.set_status("Tệp vượt quá giới hạn 5 MB.", error=True)
                return
            if size == 0:
                self.set_status("Không thể gửi tệp rỗng.", error=True)
                return
            content = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as error:
            self.set_status(f"Không đọc được tệp: {error}", error=True)
            return
        is_group = self.selected_group is not None
        request_data: dict[str, Any] = (
            {"group_id": self.selected_group["id"]}
            if self.selected_group
            else {"recipient_id": self.selected_user["id"]}
        )
        request_data.update(
            {
                "body": "",
                "attachment": {
                    "name": path.name,
                    "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    "content": content,
                },
            }
        )

        def sent(response: dict[str, Any]) -> None:
            """Thêm tệp vào lịch sử sau khi server xác nhận đã nhận."""
            if not response.get("ok"):
                self.set_status(response.get("error", "Không thể gửi tệp."), error=True)
                return
            self.add_message(response["data"]["message"])
            self.set_status("Đã gửi tệp.")

        self.send_request(
            "send_group_message" if is_group else "send_message",
            request_data,
            sent,
        )

    def download_attachment(
        self, message_id: int, group_id: int | None = None
    ) -> None:
        """Yêu cầu server tải tệp, kèm mã nhóm nếu là tin nhắn nhóm."""
        data: dict[str, Any] = {"message_id": message_id}
        if group_id is not None:
            data["group_id"] = group_id
        self.send_request(
            "download", data, self.save_download,
        )

    def save_download(self, response: dict[str, Any]) -> None:
        """Mở hộp thoại lưu và ghi nội dung tệp nhận được ra máy."""
        if not response.get("ok"):
            self.set_status(response.get("error", "Không thể tải tệp."), error=True)
            return
        attachment = response["data"]
        destination = filedialog.asksaveasfilename(
            parent=self.root, initialfile=Path(attachment["name"]).name, title="Lưu tệp đính kèm"
        )
        if not destination:
            return
        try:
            Path(destination).write_bytes(base64.b64decode(attachment["content"], validate=True))
        except (OSError, ValueError) as error:
            self.set_status(f"Không thể lưu tệp: {error}", error=True)
            return
        self.set_status(f"Đã lưu tệp: {destination}")

    def logout(self) -> None:
        """Xóa trạng thái tài khoản, đóng socket và trở về đăng nhập."""
        self.user = None
        self.profile = None
        self.selected_user = None
        self.selected_group = None
        self.groups.clear()
        self.messages.clear()
        self.displayed_message_ids.clear()
        old_socket = self.sock
        self.sock = None
        self.server_address = None
        if old_socket is not None:
            try:
                old_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            old_socket.close()
        self.callbacks.clear()
        self.root.title("NCHAT | Trò chuyện kết nối")
        self.show_auth("login")
        self.connect_to_server()

    def close(self) -> None:
        """Đóng kết nối mạng và cửa sổ ứng dụng."""
        if not self.closed:
            self.closed = True
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
        self.root.destroy()


def main() -> None:
    """Khởi chạy giao diện NCHAT và vòng lặp sự kiện Tkinter."""
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    ChatClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
