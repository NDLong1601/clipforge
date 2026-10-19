# Thành phần bên thứ ba

Giao diện đã build chứa React/React DOM (MIT), biểu tượng Lucide (ISC) và font Be Vietnam Pro (SIL Open Font License). Bản sao giấy phép nằm trong `licenses/`.

Các gói Python, Vite và phụ thuộc khác được tải qua pip/npm theo giấy phép tương ứng của từng gói. FFmpeg không nằm trong gói ZIP mã nguồn; được cung cấp qua imageio-ffmpeg khi cài đặt. Khi phân phối một bộ cài kèm binary FFmpeg, cần kiểm tra giấy phép của đúng bản binary và codec đi kèm.

Telemetry RAM/CPU dùng psutil 7.2.2 (BSD 3-Clause), tải qua pip theo requirements-lock. Bản sao LICENSE từ wheel đã kiểm thử nằm tại `licenses/psutil-BSD-3-Clause.txt`.

Tư liệu trong dự án demo được tạo bằng Pillow/FFmpeg và bộ tổng hợp âm trong `backend/demo.py`, không tải video hay nhạc bên ngoài.

Bộ lọc khuôn mặt dùng mô hình YuNet của OpenCV Zoo (MIT), chạy trên máy, không gửi video lên dịch vụ ngoài.
Mô hình, giấy phép và commit/SHA-256 nguồn được lưu trong `backend/assets/`.
Nguồn: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet.

Bản dọc vẽ tay (`backend/portrait.py`) chuyển từ bước presentation của bộ srt-whiteboard-animation-custom của người dùng. Nền lưới, nhạc Relaxed và hai sprite người kể trong `backend/assets/portrait/` lấy từ bộ đó; prompt tạo sprite lưu cùng tệp provenance. Cần xác nhận quyền sử dụng nhạc Relaxed trước khi phân phối công khai.

Chế độ vẽ tay (`backend/whiteboard/`) dùng renderer srt-whiteboard-animation của geeklee (MIT, commit 696a724), chép nguyên trong `backend/whiteboard/vendor/` cùng LICENSE và upstream.json; chỉ phần ghi video được thay bằng ống FFmpeg khi chạy. Ảnh bàn tay `backend/assets/whiteboard/hand.png` và các preset nét vẽ lấy từ bộ srt-whiteboard-animation-custom của người dùng.
