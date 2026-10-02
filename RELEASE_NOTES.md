# ClipForge Local 1.0.0 — M7

## Bản phát hành mã nguồn

Gói mã nguồn gồm backend, frontend đã build, dependency lock files, giấy phép, hướng dẫn chạy và bộ test. Trên Windows, cài Python 3.12+ rồi mở `MO_CLIPFORGE.bat`; lần đầu cần Internet để tạo môi trường Python và cài các package đã khóa. Node.js chỉ cần khi sửa và build giao diện.

Tạo lại gói ZIP bằng `python tools/package_release.py`. Gói được ghi vào `release/ClipForge-1.0.0-source.zip`; tệp `.sha256` bên cạnh dùng để kiểm tra tải xuống. Dữ liệu cá nhân, API key, `.venv`, `node_modules` và Git metadata không được đóng gói.

## Nội dung

- Cập nhật 02/10/2026: sửa trôi mốc ở các cảnh có thời lượng lẻ, đồng bộ AAC bằng clock 48 kHz nguyên mẫu, kiểm tra khung hình/âm thanh sau xuất và làm mới cache renderer cũ.
- Thư viện 10 sound effect tích hợp, cue thủ công, mức âm riêng và tự chèn ở chuyển cảnh/điểm nhấn lời đọc; preview và export cùng track.
- Template liên kết tên/mô tả/CTA với nội dung sản phẩm mới; hỗ trợ mẫu placeholder cũ, trường chỉnh tay và nhận diện qua AI từ source mới.

- Tái biên tập video hoặc dựng theo format mẫu, với timeline, phụ đề, voice, nhạc, logo và xuất MP4.
- Backup/restore dự án, migration schema tự động từ phiên bản 0–4 lên schema 5 và bản sao rollback trước lần ghi schema mới.
- Trợ lý AI có duyệt từng thay đổi, revision check, preflight và telemetry provider không chứa secret.
- Preview/render có cache, proxy, font và vùng an toàn, crossfade và âm thanh nguồn riêng.

## Sửa lỗi sau review M6–M7

- CPU retry có giới hạn khi hardware encoder lỗi tại encode thật; metadata và cache ghi encoder thực dùng.
- Thumbnail MOV xoay 90°/270° khớp video; phân tích lại giữ liên kết timeline của dự án cũ.
- Planner giữ cảnh khóa với crossfade hợp lệ; live preview trộn toàn bộ cảnh/lớp và dùng gain audio riêng.
- Preview AI khớp trạng thái commit, hiển thị ảnh hưởng tới voice/cue và cho phát/scrub trước áp dụng.
- Font dùng tên family thật, hỗ trợ alias filename cũ; caption live/ASS/SRT bàn giao cùng mốc trong overlap.
- Cache nhãn AI tách endpoint và route dự phòng; API localhost không dùng key bỏ header Authorization rỗng.
- Hồi quy: 111 backend/18 frontend đạt; lint/build đạt; hai luồng preview/export được smoke trên máy phát triển. Xem `VALIDATION.md` cho bằng chứng và giới hạn.

## Giới hạn đã biết

- Ứng dụng được thiết kế cho một người dùng trên máy cục bộ; chưa có tài khoản hoặc cộng tác nhiều người.
- Setup từ ZIP đã được kiểm trong virtualenv mới và đường dẫn có dấu/khoảng trắng trên máy phát triển; launcher cũng nhận instance cũ và từ chối cổng có chương trình khác. Chưa kiểm trên Windows mới hoàn toàn; checklist thiếu dung lượng, thiếu nguồn và khôi phục job vẫn cần chạy trên máy phát hành.
- Gọi OpenAI, Gemini, Azure hoặc API tương thích cần cấu hình riêng và có thể tính phí. Bản mã nguồn không chứa API key. Luồng provider thật chưa được gọi trong nghiệm thu nếu không có khóa được phép dùng.
- Faster Whisper là tùy chọn; cần cài model và xác nhận tài nguyên máy trước khi dùng ASR cục bộ. Smoke Windows TTS en-US đạt; giọng tiếng Việt, ASR và provider bên ngoài chưa được nghiệm thu.
- Hardware encoder phụ thuộc driver/thiết bị; khi không dùng được, export tự chọn CPU và có thể chậm hơn.
- Trên fixture tổng hợp 60 giây, phân tích M7 giảm khoảng 70% thời gian so với M0. Số đo trước lượt sửa review: preview cold-cache 9,07 giây, render lặp warm-cache 3,31 giây. Lượt preview đầu tiên cần tối ưu thêm; chưa đo lại sau sửa để tuyên bố tăng tốc.
- Preview trong trình duyệt là bản xem nhanh; MP4 preview/export phản ánh chính xác hơn renderer, phụ đề và âm thanh trộn.

## Nâng cấp và quay lại phiên bản trước

Ứng dụng tạo bản sao schema trước khi ghi project đã migration. Dùng mục **Dung lượng** để tải backup dự án; backup có thể bao gồm media và tùy chọn thêm export/lịch sử. Giữ cả mã nguồn/build cũ lẫn backup trước migration khi quay về phiên bản cũ. Không mở trực tiếp project schema 5 bằng backend cũ; khôi phục dữ liệu từ backup trước migration trước khi chạy bản cũ.
