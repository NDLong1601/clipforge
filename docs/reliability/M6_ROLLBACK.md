# Nâng cấp và rollback

## Nâng cấp

1. Đóng ClipForge đúng cách để job có thể lưu checkpoint.
2. Giữ bản ZIP/build hiện tại và sao lưu thư mục `data` hoặc tải backup project qua **Dung lượng**. Giữ riêng `settings.json`; tệp này có thể chứa API key và không được chia sẻ.
3. Giải nén release mới vào thư mục mới; không ghi đè code đang chạy.
4. Khởi chạy release mới. Trước khi project schema được nâng, ứng dụng tạo backup schema để có thể khôi phục. Kiểm tra danh sách project, media, export và undo/redo.
5. Mở trang **Tác vụ** và xử lý job `interrupted` theo checkpoint; job provider `outcome_unknown` cần xác nhận gửi lại.

## Quay lại runtime cũ

1. Dừng release mới và giữ nguyên data gốc để điều tra.
2. Nếu project đã migration lên schema mới, không mở data đó bằng runtime cũ. Phục hồi backup đã tạo trước migration hoặc bản sao dữ liệu toàn thư mục.
3. Chạy runtime cũ trên bản sao data đã phục hồi. Kiểm tra project, undo/redo, media và export trước khi dùng lại thường ngày.
4. Giữ nguyên `data/job_runs` khi còn job cần resume. Job schema mới không được runtime cũ tiếp tục và việc xóa chúng có thể làm mất checkpoint.

Rollback code không đảo migration tự động. Bản sao schema và backup project là recovery point; dữ liệu phát sinh sau thời điểm backup cần được sao lưu riêng trước khi phục hồi.

R5/R6 thêm psutil đã khóa, tiến độ/bước và event journal. Khi job dừng do thiếu RAM/disk/telemetry, giữ nguyên dữ liệu phục hồi và khắc phục tài nguyên trước khi resume. Không dùng runtime cũ thiếu guard tài nguyên để ép chạy job đó. Snapshot trước R5/R6 nằm trong `review/reliability-r5/source-baseline` và `review/reliability-r6/source-baseline`; đây là bản sao source, không phải backup dữ liệu người dùng. Gói mới có UI build trong TEMP, không thay dist đang có của checkout. Dùng thư mục giải nén mới khi muốn chạy UI R6.
