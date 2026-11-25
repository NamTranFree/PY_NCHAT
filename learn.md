# Tài liệu ôn tập và vấn đáp - NCHAT

## 1. Mục tiêu của dự án

NCHAT là ứng dụng nhắn tin cá nhân và nhóm, được xây dựng bằng Python. Mục tiêu là áp dụng kiến thức lập trình mạng, giao diện đồ họa, cơ sở dữ liệu quan hệ, xử lý đa luồng và bảo mật cơ bản vào một chương trình hoàn chỉnh.

Ứng dụng gồm hai chương trình:

- **Client (`client.py`)**: giao diện desktop bằng CustomTkinter; nhận thao tác, gửi yêu cầu tới server và hiển thị phản hồi.
- **Server (`server.py`)**: lắng nghe kết nối TCP, xác thực người dùng, xử lý nghiệp vụ và đọc/ghi MySQL.
- **MySQL (`schema.sql`)**: lưu tài khoản, hồ sơ, nhóm và lịch sử tin nhắn.

## 2. Kiến thức đã áp dụng

### Python và tổ chức chương trình

- Chia chương trình theo vai trò client/server; dùng hàm và lớp `ChatClient`, `ChatServer`, `ClientConnection` để gom trách nhiệm.
- Dùng kiểu dữ liệu, ngoại lệ, `dataclass`, `pathlib`, `queue`, `threading` và các thư viện chuẩn.
- Dùng JSON để biểu diễn yêu cầu, phản hồi và sự kiện; dùng UTF-8 để hỗ trợ tiếng Việt.
- Dùng biến môi trường và tệp `.env` để cấu hình máy chủ, cổng và thông tin kết nối cơ sở dữ liệu thay vì ghi trực tiếp cấu hình cá nhân vào mã nguồn.

### Lập trình mạng

- Dùng **TCP Socket** theo mô hình client-server. TCP cung cấp luồng byte có thứ tự; chương trình tự quy ước mỗi thông điệp kết thúc bằng ký tự xuống dòng (`\n`).
- Một yêu cầu có dạng `{"id": "...", "action": "...", "data": {...}}`. `id` giúp client ghép phản hồi với đúng yêu cầu đang chờ.
- Server trả phản hồi thành công hoặc lỗi; ngoài ra có thể chủ động gửi **event** như tin nhắn mới và thay đổi trạng thái online.
- Client dùng một luồng đọc socket riêng, còn giao diện Tkinter được cập nhật trên luồng giao diện thông qua `queue` và `root.after()`.
- `threading.Lock` bảo vệ thao tác gửi đồng thời trên cùng socket, tránh dữ liệu từ nhiều luồng bị xen kẽ.

### Giao diện đồ họa

- CustomTkinter xây dựng các màn hình đăng nhập, đăng ký, chat, hồ sơ và tùy chọn hội thoại.
- Tkinter event binding kết nối thao tác người dùng (nhấn nút, Enter, cuộn chuột) với hàm xử lý.
- Các thay đổi giao diện được xử lý trong event loop; tác vụ đọc mạng không trực tiếp cập nhật widget để tránh lỗi luồng giao diện.

### Cơ sở dữ liệu

- MySQL lưu dữ liệu có cấu trúc; PyMySQL là thư viện kết nối từ Python.
- Các bảng chính: `users`, `messages`, `chat_groups`, `group_members`, `group_messages`.
- Khóa chính định danh bản ghi; khóa ngoại biểu diễn quan hệ; unique key của `username` ngăn trùng tài khoản; index giúp truy vấn lịch sử và thành viên nhanh hơn.
- Truy vấn dùng placeholder `%s` và truyền tham số tách biệt, thay vì ghép dữ liệu người dùng vào chuỗi SQL.
- Lịch sử chat được truy vấn từ CSDL; việc lưu tin nhắn cho phép nội dung còn lại sau khi client thoát.

### Bảo mật và kiểm tra dữ liệu

- Mật khẩu không lưu dạng rõ: server tạo salt ngẫu nhiên và dùng PBKDF2-HMAC-SHA256 để tạo hash; khi đăng nhập, so sánh hash bằng `hmac.compare_digest`.
- Server xác thực trạng thái đăng nhập và quyền thành viên nhóm trước các thao tác cần quyền.
- Dữ liệu đầu vào được kiểm tra, ví dụ định dạng tài khoản, độ dài mật khẩu/tin nhắn và kích thước tệp.
- Tên tệp được làm sạch trước khi lưu; tệp có giới hạn 5 MB.
- Cấu hình riêng nằm trong `.env`; `.gitignore` loại `.env`, môi trường ảo và dữ liệu upload khỏi Git.
- Đây là bảo mật nền tảng cho đồ án, không đồng nghĩa với mã hóa đầu cuối. Giao tiếp hiện dùng TCP thường; triển khai thực tế nên bổ sung TLS, giới hạn đăng nhập và chính sách quản lý tệp phù hợp.

### Kiểm thử

- `test_server.py` kiểm tra các trường hợp như xác minh mật khẩu, chặn yêu cầu chưa đăng nhập, kiểm tra tệp quá dung lượng và cập nhật schema.
- `test_client.py` kiểm tra các tiện ích và hành vi của client.
- Kiểm thử giúp phát hiện lỗi sớm mà không cần thao tác toàn bộ ứng dụng bằng tay; các thao tác phụ thuộc MySQL/socket có thể cần mock hoặc môi trường chạy riêng.

## 3. Luồng hoạt động tổng quát

```text
Client khởi chạy
  -> tạo giao diện, kết nối TCP đến IP:cổng của server
  -> tạo luồng nền đọc phản hồi / event

Server khởi chạy
  -> đọc cấu hình .env
  -> kiểm tra/tạo database và bảng
  -> bind socket, listen và accept kết nối
  -> tạo một luồng xử lý cho mỗi client

Người dùng thao tác trên client
  -> client tạo JSON {id, action, data}, gửi qua socket kèm \n
  -> server đọc từng dòng, giải mã JSON và gọi dispatch()
  -> server kiểm tra đăng nhập/quyền, xử lý nghiệp vụ và truy vấn MySQL
  -> server gửi response có cùng id; nếu cần gửi event tới client online
  -> luồng đọc của client đưa dữ liệu vào queue
  -> event loop lấy dữ liệu khỏi queue, cập nhật giao diện
```

### Luồng đăng ký và đăng nhập

1. Người dùng nhập tên tài khoản, mật khẩu và tên hiển thị.
2. Client gửi action `register` hoặc `login`.
3. Server kiểm tra dữ liệu. Khi đăng ký, server tạo salt, băm mật khẩu rồi lưu hash vào `users`.
4. Khi đăng nhập, server lấy hash đã lưu và xác minh mật khẩu.
5. Nếu hợp lệ, server đánh dấu kết nối là online và thông báo thay đổi trạng thái cho các client khác.

### Luồng gửi tin nhắn cá nhân

1. Người dùng chọn một tài khoản, nhập nội dung và nhấn Gửi.
2. `ChatClient.send_text()` gửi action `send_message` với `recipient_id` và `body`.
3. `ChatServer.dispatch()` yêu cầu client đã đăng nhập rồi chuyển tới `send_message()`.
4. Server kiểm tra người nhận và nội dung, ghi tin nhắn vào bảng `messages`.
5. Nếu người nhận đang online, server gửi event `new_message` tới người đó.
6. Client người gửi nhận response để hiển thị tin; client người nhận nhận event để cập nhật cuộc trò chuyện.

Tin nhắn nhóm theo luồng tương tự, nhưng dùng action `send_group_message`, kiểm tra tư cách thành viên, lưu vào `group_messages` và phát event cho các thành viên đang online.

### Luồng gửi tệp

1. Client đọc tệp, kiểm tra tệp không rỗng và không vượt quá 5 MB.
2. Nội dung byte được mã hóa Base64 để có thể gửi bên trong JSON.
3. Server giải mã và kiểm tra kích thước/nội dung, lưu tệp bằng tên nội bộ ngẫu nhiên trong thư mục `uploads`.
4. Thông tin tên hiển thị, loại MIME và đường dẫn nội bộ được lưu cùng bản ghi tin nhắn.
5. Khi tải xuống, server xác minh người dùng thuộc cuộc trò chuyện hoặc nhóm trước khi đọc và trả nội dung tệp.

## 4. Mẫu trả lời khi thầy hỏi

**Hỏi: Em đã sử dụng những kiến thức gì?**

> Em sử dụng Python để tổ chức chương trình theo mô hình client-server. Client dùng CustomTkinter làm giao diện, hai phía giao tiếp qua TCP Socket với dữ liệu JSON và quy ước xuống dòng để phân tách thông điệp. Server dùng đa luồng để phục vụ nhiều kết nối, dùng MySQL/PyMySQL để lưu tài khoản và lịch sử chat. Em cũng áp dụng kiểm tra dữ liệu, băm mật khẩu có salt, phân quyền thành viên nhóm và viết unit test cho các chức năng quan trọng.

**Hỏi: Một tin nhắn đi từ người gửi tới người nhận như thế nào?**

> Client gửi yêu cầu JSON có action và dữ liệu tới server qua TCP. Server giải mã, kiểm tra người gửi đã đăng nhập chưa, xác nhận người nhận, lưu tin vào MySQL rồi trả response cho người gửi. Nếu người nhận online, server đồng thời gửi event tới socket của họ. Client nhận dữ liệu ở luồng đọc mạng, đưa vào queue và event loop cập nhật giao diện.

**Hỏi: Vì sao cần luồng riêng và queue ở client?**

> Nếu đọc socket ngay trên luồng giao diện thì giao diện có thể bị treo khi mạng chậm. Vì vậy luồng nền chuyên đọc mạng; nó không đụng trực tiếp vào widget mà đưa dữ liệu vào queue. Luồng giao diện định kỳ lấy dữ liệu từ queue để cập nhật an toàn.

**Hỏi: Vì sao không lưu mật khẩu trực tiếp?**

> Nếu database bị lộ thì mật khẩu dạng rõ sẽ bị lộ ngay. Dự án lưu hash tạo bằng PBKDF2-HMAC-SHA256 với salt riêng cho từng tài khoản. Khi đăng nhập, server băm lại mật khẩu nhập vào và so sánh an toàn với hash đã lưu.

**Hỏi: TCP có tự chia ranh giới từng tin nhắn không?**

> Không. TCP là luồng byte, nên chương trình quy ước mỗi JSON kết thúc bằng `\n`. Bên nhận đọc từng dòng, giới hạn độ dài dữ liệu, sau đó mới giải mã JSON.

## 5. Các điểm nên trình bày hoặc cải thiện thêm

- Có thể giải thích rõ ranh giới trách nhiệm: giao diện và trải nghiệm ở client; xác thực, quyền và nghiệp vụ ở server; dữ liệu lâu dài ở MySQL.
- Thử demo hai client đăng nhập cùng server, chat cá nhân, chat nhóm, tắt một client để quan sát trạng thái online/offline, rồi đăng nhập lại để xem lịch sử.
- Nếu phát triển tiếp: thêm TLS, kiểm thử tích hợp với MySQL thật, giới hạn tần suất đăng nhập, phân trang lịch sử và quản lý vòng đời/xóa tệp đính kèm.
- Khi demo trên mạng LAN, server cần lắng nghe địa chỉ phù hợp và firewall cho phép cổng chat; client phải nhập IP của máy server, không phải `127.0.0.1` của máy client.

## 6. Tệp cần biết khi thuyết trình

- `client.py`: giao diện, gửi yêu cầu, nhận event và hiển thị tin nhắn.
- `server.py`: socket server, xác thực, xử lý nghiệp vụ, gửi event và thao tác dữ liệu.
- `schema.sql`: cấu trúc database và quan hệ giữa các bảng.
- `.env.example`: danh sách cấu hình mẫu; giá trị thật đặt trong `.env`.
- `test_client.py`, `test_server.py`: các kiểm thử tự động.
- `requirements.txt`: các thư viện ngoài cần cài.
