# Thành phần bên thứ ba

Giao diện đã build chứa React/React DOM (MIT), biểu tượng Lucide (ISC) và font Be Vietnam Pro (SIL Open Font License). Bản sao giấy phép nằm trong `licenses/`.

Các gói Python, Vite và phụ thuộc khác được tải qua pip/npm theo giấy phép tương ứng của từng gói. FFmpeg không nằm trong gói ZIP mã nguồn; được cung cấp qua imageio-ffmpeg khi cài đặt. Khi phân phối một bộ cài kèm binary FFmpeg, cần kiểm tra giấy phép của đúng bản binary và codec đi kèm.

Tư liệu trong dự án demo được tạo bằng Pillow/FFmpeg và bộ tổng hợp âm trong `backend/demo.py`, không tải video hay nhạc bên ngoài.
