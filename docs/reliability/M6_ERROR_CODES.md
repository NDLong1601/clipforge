# Mã lỗi tác vụ và hành động gợi ý

Các mã dưới đây là mã ổn định trong error envelope job. `detail` vẫn được giữ để tương thích client cũ; frontend nên dùng `code`, `message`, `retryable`, `actions` và `safe_details`. Không hiển thị exception thô, provider body hay path trong API public.

| Mã | Nhóm | Tự thử lại | Hành động |
|---|---|---:|---|
| `DISK_FULL` | storage | Không | Dọn dung lượng, tiếp tục sau khi đủ chỗ; diagnostics |
| `DISK_LOW` | resource | Thủ công | Encode dừng trước khi hết chỗ; giải phóng data/temp rồi tiếp tục, giữ checkpoint |
| `MEMORY_LOW` | resource | Thủ công | Giải phóng RAM rồi tiếp tục; checkpoint được giữ |
| `TELEMETRY_UNAVAILABLE` | resource | Thủ công | Kiểm cài đặt psutil/quyền đọc volume; không cấp job khi thiếu số liệu |
| `CPU_SLOT_UNAVAILABLE` | resource | Thủ công | Đợi lease CPU trước khi fallback; không chạy encode ngoài token |
| `MEDIA_MISSING` | input | Không | Chọn lại tư liệu hoặc tạo job mới |
| `MEDIA_INVALID` | input | Không | Mở chi tiết, thay tư liệu lỗi |
| `FILE_ACCESS_DENIED` | storage | Không | Kiểm tra quyền thư mục/dung lượng |
| `CHECKPOINT_INVALID` | input/conflict | Không | Tạo job mới để chạy lại bước phụ thuộc; provider-result hỏng không tự gửi lại |
| `REVISION_CONFLICT` | conflict | Không | Tạo tác vụ từ bản hiện tại; giữ checkpoint cũ |
| `CONFIG_CHANGED` | conflict | Không | Tạo tác vụ từ bản hiện tại với cấu hình/phiên bản xử lý mới |
| `MEDIA_CHANGED` | conflict | Không | Kiểm tra nguồn/font/encode config rồi tạo tác vụ từ bản hiện tại |
| `PROVIDER_AUTH_REQUIRED` | provider | Không | Sửa key/model/profile |
| `PROVIDER_QUOTA_EXCEEDED` | provider | Không | Kiểm tra hạn mức/billing |
| `PROVIDER_RATE_LIMITED` | provider | Có điều kiện | Chờ rồi tiếp tục thủ công; HTTP 429 không đồng nghĩa quota |
| `PROVIDER_UNAVAILABLE` | provider | Có điều kiện | Kiểm tra kết nối rồi tiếp tục thủ công |
| `PROVIDER_OUTCOME_UNKNOWN` | provider | Không | Đọc cảnh báo; xác nhận gửi lại có thể tốn phí |
| `ENCODER_UNAVAILABLE` | render | Không | Kiểm tra diagnostics; CPU fallback đã được thử nếu hỗ trợ |
| `VALIDATION_ERROR` | input | Không | Sửa dữ liệu/đầu vào theo thông báo đã lọc |
| `INTERNAL_ERROR` | internal | Không | Lưu diagnostic ID, tải diagnostics nếu cần hỗ trợ |

`retryable=true` biểu thị lỗi có thể thử lại sau khi khắc phục nguyên nhân; ứng dụng không tự retry background. HTTP 401/403 được phân loại là auth, 402 hoặc nội dung quota là quota, 429 là rate limit, 408/425/5xx là provider unavailable. Lỗi chưa nhận dạng trả `INTERNAL_ERROR` với ID chẩn đoán và không đưa raw exception cho client.

R3 kiểm tra revision/config trước enqueue và sau khi nhận lease. Job bị chặn đủ dữ liệu có `can_restart_current`; hành động dùng state version hiện tại để tạo ID/snapshot mới, giữ job/checkpoint cũ. Chi tiết tại [R3.md](R3.md).

R5 gắn bước hiện tại với error và mã chẩn đoán với event local đã lọc. `diagnostic_recorded=false` khi ghi event bị lỗi; event cũ có thể hết retention. Các lỗi resource chuyển job sang `interrupted/resource_wait`; ứng dụng không tự resume hoặc gọi lại provider.
