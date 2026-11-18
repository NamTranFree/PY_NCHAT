# Ứng dụng chat Python Socket + MySQL

Ứng dụng chat cá nhân và nhóm, viết bằng Python với giao diện CustomTkinter hiện đại trong một cửa sổ duy nhất, giao tiếp TCP Socket và lưu tài khoản/lịch sử trong MySQL. Đăng nhập, đăng ký, hồ sơ và chat chuyển màn hình trong cùng cửa sổ; không mở cửa sổ phụ cho các thao tác này. Người dùng có thể mở hồ sơ người khác ở chế độ chỉ xem và chọn thông tin cá nhân muốn công khai. Có nút quay lại ở các trang hồ sơ; phím `Esc` cũng quay về chat. Ứng dụng hỗ trợ trạng thái trực tuyến/ngoại tuyến, gửi tin nhắn cá nhân hoặc nhóm và gửi/tải hình ảnh hoặc tệp tối đa 5 MB.

## Yêu cầu

- Python 3.10 trở lên
- MySQL Server hoặc MariaDB đang chạy (XAMPP dùng được)
- Tài khoản MySQL có quyền tạo cơ sở dữ liệu và bảng

## Cài đặt

Mở PowerShell tại thư mục dự án:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Nếu đã cài các thư viện từ trước, chạy lại `py -m pip install -r requirements.txt` để cài giao diện CustomTkinter.

Mở **XAMPP Control Panel** và bấm **Start** ở dòng **MySQL**. Trong XAMPP thông thường, MySQL/MariaDB dùng `127.0.0.1:3306`, tài khoản `root` và chưa đặt mật khẩu; `.env.example` đã đặt sẵn các giá trị này. Nếu bạn đã đổi mật khẩu hoặc cổng trong XAMPP, cập nhật `DB_PASSWORD` hoặc `DB_PORT` trong `.env`.

Khi khởi động, server tự tạo database `python_chat` và các bảng. Hoặc bạn có thể mở **phpMyAdmin** từ XAMPP, chọn tab **Import**, rồi nhập tệp `schema.sql`. Cả cách tự tạo lẫn import đều yêu cầu tài khoản MySQL có quyền tạo database và bảng.

## Chạy ứng dụng

Mở hai cửa sổ PowerShell, kích hoạt môi trường ảo và chạy tại thư mục này.

Cửa sổ 1 — khởi động server:

```powershell
.\.venv\Scripts\Activate.ps1
py server.py
```

Cửa sổ 2 — khởi động giao diện client:

```powershell
.\.venv\Scripts\Activate.ps1
py client.py
```

Trên máy chạy server, giữ `CHAT_HOST=0.0.0.0` và chạy `py server.py`. Trên mỗi máy client, chạy `py client.py`, nhập địa chỉ IPv4 của máy server (xem bằng lệnh `ipconfig`) và cổng `5050` ở cuối màn hình đăng nhập, rồi bấm **Kết nối**. Các máy cần truy cập được cùng một server và database; trên máy server, cho phép ứng dụng/cổng TCP `5050` qua Windows Firewall. Nếu dùng các mạng khác nhau, cần cấu hình định tuyến/port forwarding phù hợp; không dùng `127.0.0.1` trên client vì địa chỉ đó trỏ về chính máy client.

## Sử dụng

1. Tạo tài khoản, nhập riêng **Tên hiển thị** (tên người khác nhìn thấy), **Tên tài khoản** (dùng để đăng nhập) và mật khẩu ít nhất 8 ký tự.
2. Đăng nhập, chọn **Hồ sơ** để cập nhật tên, ngày sinh (dạng `YYYY-MM-DD`), sở thích, quê quán và bật/tắt quyền xem từng thông tin. Mặc định chỉ tên hiển thị được công khai; ngày sinh, sở thích và quê quán được ẩn.
3. Chọn một tài khoản trong danh sách để xem lịch sử và bắt đầu chat. Bấm **Xem hồ sơ** để xem các thông tin người đó đã công khai; trang này chỉ đọc.
4. Dùng **Gửi tệp / ảnh** để gửi tệp tối đa 5 MB. Tệp nhận được có thể tải xuống từ tin nhắn.
5. Chọn một người để chat, sau đó nhấn nút **⋮** ở góc trên bên phải. Trang **Tùy chọn cuộc trò chuyện** trượt vào trong cùng cửa sổ. Chọn **Hình nền** để chọn ảnh trong `anh_nen` hoặc đặt lại nền; chọn **Biệt danh** để đổi tên người đó trong danh sách và khung chat; chọn **Tạo nhóm** để đặt tên nhóm và chọn ít nhất hai người khác. Người tạo được thêm tự động; mỗi nhóm tối đa 50 thành viên. Trong cuộc trò chuyện nhóm, nút **⋮** cho phép đổi hình nền riêng trên máy, đổi tên nhóm, thêm thành viên hoặc rời nhóm. Thành viên đang online nhận cập nhật nhóm ngay; tin đổi tên, thêm người và rời nhóm được lưu trong lịch sử trò chuyện. Nhóm hỗ trợ gửi/nhận tin nhắn và tệp đính kèm. Nút **＋** cạnh mục **Nhóm chat** cũng mở màn hình tạo nhóm.

Tên tài khoản dùng để đăng nhập chỉ dùng ở màn hình đăng nhập/đăng ký; ứng dụng không hiển thị tên này trong chat hoặc hồ sơ của người khác. Nếu một người ẩn tên hiển thị, danh sách và hồ sơ dùng nhãn chung thay thế.

Server chỉ cho phép một kết nối đăng nhập trên mỗi tài khoản tại một thời điểm. Lịch sử hiển thị tối đa 100 tin nhắn gần nhất cho mỗi cuộc trò chuyện. Tệp tải lên được lưu trong thư mục `uploads`.

Nếu tài khoản cũ có tên hiển thị trùng mật khẩu, lần đăng nhập tiếp theo sẽ tự đổi tên hiển thị thành tên tài khoản để không lộ mật khẩu. Sau đó vào **Hồ sơ** để đặt tên hiển thị mong muốn. Mật khẩu không được dùng làm tên hiển thị khi đăng ký hoặc cập nhật hồ sơ.

## Cấu trúc

- `server.py`: TCP server, xác thực tài khoản, trạng thái online, xử lý tin nhắn và tệp.
- `client.py`: giao diện desktop CustomTkinter, các trang đăng nhập/đăng ký/hồ sơ/chat trong cùng cửa sổ.
- `anh_nen/`: các ảnh được dùng làm hình nền chat.
- `schema.sql`: schema MySQL.
- `.env.example`: mẫu cấu hình; không đưa mật khẩu thật vào mã nguồn.
