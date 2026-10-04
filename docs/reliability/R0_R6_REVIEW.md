# Review độc lập bản sửa R0–R6

Ngày 04/10/2026. Review working tree hiện tại, đối chiếu F1–F9 và các thay đổi về commit/recovery, process ownership, input identity, UI lifecycle và resource pressure. Không sửa mã ứng dụng trong lượt review này.

**Cập nhật sau review:** RR1/RR2 đã được sửa trong lượt triển khai tiếp theo. Xem [R0_R6_FIXES.md](R0_R6_FIXES.md) về thay đổi và nghiệm thu. Nội dung dưới đây giữ nguyên bối cảnh phát hiện ban đầu.

## Kết luận

Các regression cũ đã được xử lý tại các nhánh đã kiểm tra. Còn **2 lỗi có reproduction độc lập** trong phối hợp provider cache với recovery; cần sửa trước khi nghiệm thu luồng tiếp tục job AI/TTS. Test hiện có chưa phủ hai tình huống này.

### RR1 — P1: thiếu RAM sau phản hồi AI làm gửi lại yêu cầu đã thành công

Vị trí: `backend/providers.py:243–247`, liên quan `backend/jobs.py:403–412`, `backend/jobs.py:746–756` và `begin_provider_call`.

Sau khi nhận HTTP 200, `ai_json` gọi `job.check()` trước khi parse/lưu kết quả. Nếu RAM hoặc disk xuống ngưỡng, `ResourcePressure` bị nhánh `except Exception` ghi thành `completed_cancelled`. Worker chuyển job sang `interrupted/resource_wait`. Khi người dùng tiếp tục sau khi tài nguyên phục hồi, `begin_provider_call` bỏ qua intent này và gửi lại cùng request. Kết quả đã trả bị mất, có thể phát sinh phí lần hai mà không qua xác nhận gửi lại yêu cầu AI.

Reproduction dùng HTTP mock, project tạm và cơ chế dispatch/resume thật:

1. Job plan gửi request; mock trả HTTP 200 và hạ telemetry RAM xuống dưới ngưỡng.
2. Job thành `interrupted`, phase `resource_wait`, intent `completed_cancelled`, request count = 1.
3. Khôi phục RAM, gọi `resume_render` với state version hợp lệ.
4. Job thành `done`, attempt = 2, request count = **2**, không gọi `authorize_unknown_retry`.

Bằng chứng: `review/reliability-rereview/reproduce_provider_pressure.py` và `provider-pressure-result.json`. Script assert hành vi lỗi để xác nhận reproduction; không phải acceptance test mong đợi.

### RR2 — P2: cache audio TTS bị thiếu làm nút xác nhận thử lại không hoạt động

Vị trí: `backend/providers.py:701–712`, caller tại `:575–577` và `:605–607`; liên quan `backend/jobs.py:1728–1733`.

Khi một intent TTS đã `success` nhưng file audio mất hoặc hash thay đổi, `_restore_provider_audio` ném `ProviderOutcomeUnknown` mà không chuyển intent tương ứng sang `outcome_unknown`. Worker đưa job sang `blocked/PROVIDER_OUTCOME_UNKNOWN`, UI có thể cho phép xác nhận retry, nhưng `authorize_unknown_retry` chỉ tìm intent `outcome_unknown` nên báo “Không tìm thấy yêu cầu AI chưa xác định”. Người dùng không tiếp tục được job dù đã đồng ý gửi lại; phải hủy/tạo lại tác vụ hoặc khôi phục file thủ công.

Reproduction dùng WAV hợp lệ và HTTP mock:

1. TTS thành công, audio và provider result được lưu bằng code thật; job bị ngắt trước khi hoàn tất.
2. Xóa duy nhất file cache trong thư mục TEMP của test, rồi resume.
3. Job `blocked`, reason `PROVIDER_OUTCOME_UNKNOWN`, nhưng intent vẫn `success`.
4. Gọi `authorize_unknown_retry` bằng state version mới nhất vẫn thất bại; tổng request vẫn là 1.

Bằng chứng: `review/reliability-rereview/reproduce_tts_recovery.py` và `tts-recovery-result.json`. Đây là reproduction của lỗi, không phải test nghiệm thu.

## Kế hoạch sửa tiếp

### S1 — xử lý RR1 trước

1. Tách `Cancelled`, shutdown interruption và `ResourcePressure`; không gom mọi exception từ `job.check()` thành `completed_cancelled`.
2. Với phản hồi thành công đã nhận, parse/validate và lưu provider result bền vững trước khi dừng vì thiếu tài nguyên. Resume phải dùng lại kết quả đã lưu, không áp dụng kết quả vào project khi chưa qua commit/CAS.
3. Nếu không thể lưu/khôi phục kết quả, giữ intent đủ để yêu cầu xác nhận retry; không chuyển sang trạng thái bị `begin_provider_call` bỏ qua. Xử lý cả lỗi ghi đĩa ở ranh giới này.
4. Thêm test RAM thấp và disk thấp ngay sau HTTP 200: lần đầu interrupted; phục hồi tài nguyên và resume vẫn chỉ có **1 HTTP request** khi result lưu được. Nhánh lưu thất bại phải chặn resend cho tới khi xác nhận rõ ràng.
5. Chạy lại test cancellation/shutdown/unknown outcome để bảo đảm cancel vẫn không commit và shutdown không tự gửi lại.

### S2 — phục hồi RR2 theo đúng intent

1. Cho nhánh cache hit giữ được ID intent, thay vì chỉ trả `(None, cached)`; có thể dùng một API chuyên dụng để đánh dấu artifact cache không còn hợp lệ theo fingerprint/intent ID.
2. Khi audio thiếu/hỏng, chuyển đúng intent sang trạng thái cần xác nhận và persist trước khi báo `ProviderOutcomeUnknown`. Không tự động gọi lại dịch vụ và không thay đổi các intent không liên quan.
3. Khi xác nhận, đánh dấu đúng intent `retry_authorized`; lần resume tiếp theo phải bỏ cache lỗi, gửi đúng một request mới, lưu audio/result mới rồi tiếp tục.
4. Test cả OpenAI và Azure; file thiếu, hash sai, restart sau khi blocked, xác nhận state version cũ, và hai request xác nhận đồng thời. Tiêu chí: chưa xác nhận không gửi mới; xác nhận hợp lệ gửi một lần; retry thành công không quay lại cache cũ.

### S3 — nghiệm thu lại phần giao nhau

1. Chuyển hai reproduction thành regression tests với assertion hành vi đúng, dùng fixture isolation của bộ test hiện có.
2. Thêm browser case: TTS bị chặn vì cache hỏng → xác nhận gửi lại → attempt mới thành công → refresh đúng một lần.
3. Chạy backend R0–R6 + Windows process regression, frontend unit/lint và browser E2E. Sau khi đạt, chạy full suite và build gói release mới; cập nhật R6 baseline theo kết quả thực tế.
4. Chỉ đóng RR1 khi không có resend ngoài ý muốn; chỉ đóng RR2 khi vòng xác nhận/retry chạy trọn vẹn, kể cả sau restart.

## Kiểm thử chạy lại trong lượt review

| Kiểm tra | Kết quả mới |
| --- | --- |
| Backend `test_reliability_r0.py` đến `test_reliability_r6.py` | **231 passed**, 511,13 giây; 1 cảnh báo deprecation Starlette/TestClient |
| Windows process ownership `test_reliability_r0_process.py` | **1 passed** |
| Frontend unit `npm test` | **63 passed** |
| Frontend lint | **21 files**, đạt |
| Browser runner R4, gồm build UI vào TEMP và backend crash/restart | **9 passed**, 0 failed/skipped/flaky |
| Reproduction RR1 và RR2 | Cả hai tái hiện được lỗi, HTTP mock và dữ liệu TEMP |

Log browser: `review/reliability-rereview/browser/results.json`, `build.txt`, `lifecycle.json`. Tổng backend chạy lại: **232 passed**. Không suy ra hệ thống hết lỗi chỉ từ bộ test xanh: RR1/RR2 là các tình huống bổ sung chưa được bộ test hiện tại phát hiện.

## Giới hạn

Provider dùng mock để kiểm chính xác số request, không phát sinh phí thật. Browser dùng backend và dữ liệu cô lập. Lượt này không chạy lại toàn bộ benchmark/clean ZIP/VM hoặc các dịch vụ AI trả phí; số liệu nghiệm thu cũ trong R6.md không được xem là phép đo mới. Build UI qua runner vào TEMP theo workflow hiện có.
