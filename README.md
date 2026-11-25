# Ứng dụng chat Python Socket + MySQL

Ứng dụng chat cá nhân và nhóm, viết bằng Python với giao diện CustomTkinter hiện đại trong một cửa sổ duy nhất, giao tiếp TCP Socket và lưu tài khoản/lịch sử trong MySQL. Đăng nhập, đăng ký, hồ sơ và chat chuyển màn hình trong cùng cửa sổ; không mở cửa sổ phụ cho các thao tác này. Người dùng có thể mở hồ sơ người khác ở chế độ chỉ xem và chọn thông tin cá nhân muốn công khai. Có nút quay lại ở các trang hồ sơ; phím `Esc` cũng quay về chat. Ứng dụng hỗ trợ trạng thái trực tuyến/ngoại tuyến, gửi tin nhắn cá nhân hoặc nhóm và gửi/tải hình ảnh hoặc tệp tối đa 5 MB.

## Kiến trúc hệ thống

```text
CustomTkinter client  <--- TCP socket / JSON --->  Python chat server  <--- PyMySQL --->  MySQL
       |                                                |
       |                                                +-- thư mục uploads/ lưu tệp đính kèm
       +-- hình nền, biệt danh lưu cục bộ trên máy
```

- **Client (`client.py`)** chịu trách nhiệm giao diện, tạo request, nhận response/event và cập nhật màn hình. Client chỉ kết nối tới chat server; không kết nối trực tiếp tới MySQL.
- **Server (`server.py`)** là trung gian duy nhất: xác thực, kiểm tra quyền, xử lý chat, cập nhật trạng thái online, lưu/đọc dữ liệu và chuyển event tới các client đang kết nối.
- **MySQL (`schema.sql`)** lưu tài khoản, hồ sơ, nhóm, thành viên và lịch sử tin nhắn.
- **Tệp đính kèm** được lưu trên máy server trong thư mục cấu hình `UPLOAD_DIR`; database giữ thông tin để liên kết tệp với tin nhắn.

### Giao thức TCP socket

Server dùng IPv4/TCP (`AF_INET`, `SOCK_STREAM`), gắn vào `CHAT_HOST:CHAT_PORT`, lắng nghe kết nối rồi nhận từng client bằng `accept()`. Mỗi kết nối có một `ClientConnection` và một daemon thread xử lý riêng.

TCP là luồng byte, không tự phân cách các thông điệp. NCHAT dùng quy ước **mỗi thông điệp là một dòng JSON UTF-8 kết thúc bằng `\n`**. Request có dạng:

```json
{"id":"mã-yêu-cầu","action":"send_message","data":{"recipient_id":12,"body":"Xin chào"}}
```

- `id` tương quan response với request đang chờ.
- `action` chọn chức năng server.
- `data` chứa dữ liệu của chức năng.

Response có `type: "response"`, `id`, `ok` và `data` hoặc `error`. Server cũng có thể chủ động gửi event có `type: "event"` như `presence_changed`, `new_message`, `new_group_message` hoặc `group_updated`. Event có thể đến xen giữa response; client dùng `id` để ghép response và xử lý event riêng.

Các lần gửi trên cùng một socket được bảo vệ bằng lock để JSON từ nhiều thread không bị xen kẽ. Server và client giới hạn một dòng giao thức tối đa 8 MiB; dữ liệu gửi vượt giới hạn hoặc sai định dạng sẽ bị từ chối.

### Luồng khởi động và xử lý của server

1. Server nạp cấu hình từ môi trường/tệp `.env`.
2. Trước khi mở cổng, server kết nối MySQL, tạo database nếu cần, chạy các câu lệnh trong `schema.sql`, bổ sung một số cột tương thích cho database cũ và tạo thư mục upload.
3. Server tạo socket, `bind()` vào địa chỉ/cổng và gọi `listen()`.
4. Vòng lặp `accept()` nhận kết nối mới; thread riêng chạy `handle_client()` cho client đó, trong khi server tiếp tục nhận kết nối khác.
5. `handle_client()` đọc từng dòng, kiểm tra giới hạn và định dạng JSON, rồi chuyển `action` tới `dispatch()`.
6. `dispatch()` yêu cầu đăng nhập đối với các thao tác cần tài khoản, sau đó gọi hàm nghiệp vụ tương ứng. Server kiểm tra dữ liệu/quyền và truy vấn MySQL.
7. Server gửi response về người yêu cầu. Nếu nghiệp vụ tạo thay đổi cần báo realtime, server gửi thêm event đến các client online liên quan.
8. Khi socket đóng hoặc mất kết nối, server dọn kết nối khỏi danh sách online và phát lại `presence_changed`.

Client kết nối bằng IP/cổng người dùng nhập. Luồng nền đọc socket và đưa response/event vào `queue`; event loop Tkinter định kỳ lấy dữ liệu khỏi queue để gọi callback và cập nhật giao diện an toàn.

### Các nhóm request

| Nhóm | Actions |
|---|---|
| Tài khoản và hồ sơ | `register`, `login`, `profile`, `public_profile`, `update_profile` |
| Tài khoản và nhóm | `users`, `groups`, `create_group`, `group_members` |
| Quản lý nhóm | `rename_group`, `add_group_members`, `remove_group_member`, `transfer_group_leadership`, `leave_group` |
| Tin nhắn và tệp | `history`, `group_history`, `send_message`, `send_group_message`, `download` |

Đăng ký và đăng nhập được phép trước xác thực. Các request còn lại yêu cầu tài khoản đã đăng nhập; request tải tệp cũng tự kiểm tra đăng nhập và quyền truy cập.

## Yêu cầu

- Python 3.10 trở lên
- MySQL Server hoặc MariaDB đang chạy (XAMPP dùng được)
- Tài khoản MySQL có quyền tạo cơ sở dữ liệu và bảng
- Các thư viện Python trong `requirements.txt`: PyMySQL, python-dotenv, CustomTkinter và Pillow

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

## Cấu hình

Tạo `.env` từ `.env.example`. Các biến chính:

| Biến | Dùng ở đâu | Ý nghĩa |
|---|---|---|
| `DB_HOST`, `DB_PORT` | Server | Địa chỉ và cổng MySQL |
| `DB_USER`, `DB_PASSWORD` | Server | Tài khoản MySQL; không commit mật khẩu thật |
| `DB_NAME` | Server | Tên database, mặc định mẫu là `python_chat` |
| `CHAT_HOST` | Server | Địa chỉ server bind; `0.0.0.0` để nhận kết nối qua các giao diện mạng |
| `CHAT_PORT` | Server và client | Cổng TCP chat; mẫu là `5050` |
| `CHAT_SERVER_HOST` | Client | IP/tên máy server mặc định trên màn hình kết nối |
| `UPLOAD_DIR` | Server | Thư mục lưu nội dung tệp đính kèm |

Trên cùng một máy có thể dùng `127.0.0.1` làm địa chỉ server ở client. Với nhiều máy, server bind `0.0.0.0`, còn client nhập IPv4 LAN của máy server. Chỉ cần client truy cập được cổng chat của server; MySQL chỉ cần được server truy cập và không cần mở cổng database cho các máy client.

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

Trên máy chạy server, giữ `CHAT_HOST=0.0.0.0` và chạy `py server.py`. Trên mỗi máy client, chạy `py client.py`, nhập địa chỉ IPv4 của máy server (xem bằng lệnh `ipconfig`) và cổng `5050` ở cuối màn hình đăng nhập, rồi bấm **Kết nối**. Trên máy server, cho phép ứng dụng/cổng TCP `5050` qua Windows Firewall. Nếu dùng các mạng khác nhau, cần cấu hình định tuyến/port forwarding phù hợp; không dùng `127.0.0.1` trên client ở máy khác vì địa chỉ đó trỏ về chính máy client.

## Sử dụng

1. Tạo tài khoản, nhập **Tên hiển thị** (1–100 ký tự), **Tên tài khoản** (3–50 ký tự, gồm chữ/số/`_`/`.`/`-`) và mật khẩu từ 8–128 ký tự. Tên hiển thị không được trùng mật khẩu.
2. Đăng nhập, chọn **Hồ sơ** để cập nhật tên, ngày sinh (dạng `YYYY-MM-DD`), sở thích, quê quán và bật/tắt quyền xem từng thông tin. Mặc định chỉ tên hiển thị được công khai; ngày sinh, sở thích và quê quán được ẩn.
3. Chọn một tài khoản trong danh sách để xem lịch sử và bắt đầu chat. Bấm **Xem hồ sơ** để xem các thông tin người đó đã công khai; trang này chỉ đọc.
4. Dùng **Gửi tệp / ảnh** để gửi tệp tối đa 5 MB. Tệp nhận được có thể tải xuống từ tin nhắn.
5. Chọn một người để chat, sau đó nhấn nút **⋮** ở góc trên bên phải. Trang **Tùy chọn cuộc trò chuyện** trượt vào trong cùng cửa sổ. Chọn **Hình nền** để chọn ảnh trong `anh_nen` hoặc đặt lại nền; chọn **Biệt danh** để đổi tên người đó trong danh sách và khung chat; chọn **Tạo nhóm** để đặt tên nhóm và chọn ít nhất hai người khác. Người tạo được thêm tự động và ban đầu là trưởng nhóm; mỗi nhóm tối đa 50 thành viên. Vai trò người tạo được lưu riêng với trưởng nhóm, nên có thể chuyển quyền trưởng nhóm mà vẫn giữ thông tin người tạo. Trong cuộc trò chuyện nhóm, nút **⋮** cho phép đổi hình nền riêng trên máy, đổi tên nhóm, thêm thành viên, quản lý thành viên hoặc rời nhóm. Trưởng nhóm có thể chuyển quyền cho một thành viên khác hoặc xóa thành viên khỏi nhóm; trưởng nhóm cần chuyển quyền trước khi rời nhóm. Cập nhật nhóm được gửi tới thành viên đang online; hoạt động đổi tên, thêm/xóa thành viên, chuyển quyền trưởng nhóm và rời nhóm được ghi vào lịch sử. Nhóm hỗ trợ gửi/nhận tin nhắn và tệp đính kèm. Nút **＋** cạnh mục **Nhóm chat** cũng mở màn hình tạo nhóm.

Tên tài khoản dùng để đăng nhập chỉ dùng ở màn hình đăng nhập/đăng ký; ứng dụng không hiển thị tên này trong chat hoặc hồ sơ của người khác. Nếu một người ẩn tên hiển thị, danh sách và hồ sơ dùng nhãn chung thay thế.

Danh sách tài khoản/nhóm có thể tìm kiếm theo tên; tìm kiếm bỏ qua dấu tiếng Việt và ưu tiên kết quả bắt đầu bằng nội dung đã nhập. Biệt danh và hình nền được lưu riêng trên máy hiện tại, không đồng bộ qua server.

Server chỉ cho phép một kết nối đăng nhập trên mỗi tài khoản tại một thời điểm. Lịch sử hiển thị tối đa 100 tin nhắn gần nhất cho mỗi cuộc trò chuyện. Nội dung tin nhắn tối đa 5.000 ký tự; tệp đính kèm không vượt quá 5 MiB. Tệp tải lên được lưu trong thư mục `UPLOAD_DIR` trên server.

Nếu tài khoản cũ có tên hiển thị trùng mật khẩu, lần đăng nhập tiếp theo sẽ tự đổi tên hiển thị thành tên tài khoản để không lộ mật khẩu. Sau đó vào **Hồ sơ** để đặt tên hiển thị mong muốn. Mật khẩu không được dùng làm tên hiển thị khi đăng ký hoặc cập nhật hồ sơ.

## Mô hình dữ liệu và an toàn

- `users`: tên đăng nhập, hash mật khẩu, tên hiển thị, thông tin hồ sơ và các cờ quyền riêng tư.
- `messages`: tin nhắn cá nhân và metadata của tệp đính kèm.
- `chat_groups`: tên nhóm, người tạo và trưởng nhóm hiện tại.
- `group_members`: thành viên của từng nhóm.
- `group_messages`: tin nhắn thường, tin hệ thống và metadata tệp trong nhóm.

Mật khẩu không lưu dạng rõ; server tạo salt ngẫu nhiên và dùng PBKDF2-HMAC-SHA256. Truy vấn MySQL truyền tham số riêng thay vì ghép trực tiếp dữ liệu người dùng vào SQL. Server kiểm tra xác thực và quyền thành viên trước các thao tác nhóm/tải tệp; tên tệp hiển thị được làm sạch và file lưu trên server bằng tên nội bộ ngẫu nhiên.

**Giới hạn bảo mật:** giao tiếp hiện dùng TCP thường, chưa bật TLS và không có mã hóa đầu cuối. Base64 chỉ là cách biểu diễn nội dung tệp trong JSON, không phải mã hóa. Không nên đưa server ra Internet công cộng nếu chưa bổ sung TLS, kiểm soát truy cập mạng và các biện pháp bảo vệ vận hành phù hợp.

## Khắc phục sự cố

- **Không kết nối được:** kiểm tra server đã chạy, IP/cổng trên client đúng, firewall cho phép TCP `CHAT_PORT` và máy client truy cập được máy server.
- **Không kết nối được MySQL:** kiểm tra dịch vụ MySQL/MariaDB đang chạy và các giá trị `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` trong `.env`.
- **Client khác không thấy server:** server cần bind đúng giao diện (thường là `0.0.0.0`), client phải nhập IP của máy server; không nhập `127.0.0.1` từ máy khác.
- **Không tải được tệp:** kiểm tra thư mục `UPLOAD_DIR` trên server còn tệp và tài khoản có quyền truy cập tin nhắn/nhóm tương ứng.

## Cấu trúc

- `server.py`: cấu hình và khởi tạo database, TCP server, xác thực, quyền, nghiệp vụ chat, trạng thái online và tệp.
- `client.py`: giao diện CustomTkinter, kết nối TCP, gửi request và nhận response/event.
- `anh_nen/`: các ảnh được dùng làm hình nền chat.
- `schema.sql`: schema MySQL.
- `.env.example`: mẫu cấu hình; tạo `.env` riêng và không đưa mật khẩu thật vào Git.
- `uploads/`: thư mục được server tạo để lưu tệp đính kèm.
