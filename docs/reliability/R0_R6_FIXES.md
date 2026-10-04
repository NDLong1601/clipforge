# Đóng các lỗi còn lại sau review R0–R6

Ngày 04/10/2026. Các phát hiện gốc và reproduction được mô tả trong [R0_R6_REVIEW.md](R0_R6_REVIEW.md).

## RR1 — giữ phản hồi AI khi thiếu tài nguyên

`ai_json` phân biệt ResourcePressure với cancellation/shutdown sau HTTP response. Phản hồi thành công được parse, xác thực và lưu provider result trước khi ném lại lỗi tài nguyên. Job vẫn dừng, chưa áp dụng kết quả vào project; khi tiếp tục, cùng request lấy kết quả đã lưu thay vì gửi lại và tính phí thêm.

Nếu ghi provider result thất bại, intent còn `sending` được chuyển bền vững sang `outcome_unknown` khi resume. Người dùng phải xác nhận trước khi gửi lại. Cache metadata bị mất cũng đi qua luồng xác nhận, không tự tạo request mới. Cancellation/shutdown giữ hành vi chặn áp dụng phản hồi muộn.

## RR2 — xác nhận đúng intent TTS và giữ trạng thái qua restart

Cache hit trả lại cả ID intent. Khi audio thiếu/hỏng, nhánh restore đánh dấu đúng intent `outcome_unknown`, kèm `result_invalid` để startup không phục hồi metadata cũ thành một kết quả hợp lệ. Xác nhận hợp lệ chuyển intent sang `retry_authorized`; resume tạo request mới và không đọc lại cache hỏng.

Kiểm thử xác nhận state version cũ và hai xác nhận đồng thời bảo đảm chỉ một xác nhận được nhận. File nguồn/project chưa commit vẫn được giữ nguyên trong các nhánh dừng.

## Kiểm chứng

- `tests/test_provider_recovery_review.py`: 11 regression về RAM/disk sau HTTP 200, lỗi ghi provider result, OpenAI/Azure TTS, file thiếu/hash sai, reload registry sau restart và xác nhận đồng thời.
- Browser thêm luồng cache TTS hỏng → checkbox xác nhận phí → retry qua API thật → attempt mới done → project refresh đúng một lần. Phản hồi TTS được mô phỏng; regression backend kiểm WAV thật với HTTP mock.
- Frontend dist được build lại từ source hiện tại để launcher dùng đúng UI R0–R6.

Lượt full backend đầu: 375 passed / 26 failed do RAM máy giảm dưới ngưỡng ở cuối lượt chạy. Record của matrix render ghi `interrupted/MEMORY_LOW`; bài CAS không đến barrier và bài legacy render cũng bị ngắt bởi guard. Đã cố định telemetry RAM 8 GiB chỉ trong fixture matrix write-fault/CAS R6 và fixture legacy migration. Không đổi ngưỡng hoặc telemetry production; kiểm thử R5 và missing-psutil vẫn kiểm guard riêng.

Kết quả cuối:

| Kiểm tra | Kết quả |
| --- | --- |
| Backend full lần đầu | 375 passed, 26 failed do RAM; 583,51 giây |
| Chạy lại toàn bộ 26 bài thất bại sau sửa fixture | **26 passed**, 207,41 giây |
| Kiểm bổ sung R6 ngoài matrix và module legacy | **18 passed** |
| Tổng test backend khác nhau đã xác minh đạt | **401** (gồm 11 regression mới); không phải một lượt full xanh duy nhất |
| Frontend unit / lint / build | **63 passed** / 21 files đạt / đạt |
| Chrome E2E | **10 passed**, 0 fail/skip/flaky; một backend crash/restart thật |
| Release ZIP | Manifest 197 mục đúng hash, có regression mới và UI vừa build |
| Git diff whitespace | Đạt |

Backend có một cảnh báo deprecation Starlette/TestClient. Log XML nằm ở `review/reliability-rereview/fix-backend.xml`, `fix-backend-retry.xml`, `fix-fixtures.xml`; browser log ở `fix-browser/`. ZIP cập nhật: `release/ClipForge-1.0.0-source.zip`; không commit ZIP/log/data vào repository.

Không dùng API trả phí thật. Dữ liệu test và log ở TEMP/`review/`, không đưa vào Git. Các số benchmark/clean ZIP cũ trong R6.md là bằng chứng lịch sử, không phải phép đo của lượt sửa này.
