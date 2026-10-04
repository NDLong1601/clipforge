# ClipForge Local 1.0.0 — M7

## Reliability M5–M6

- 04/10/2026 — Sau review R0–R6: giữ kết quả AI đã nhận khi thiếu RAM/disk để resume không gửi lại yêu cầu đã tính phí; cache audio TTS thiếu/hỏng chuyển đúng intent sang chờ xác nhận, giữ trạng thái qua restart. Bổ sung 11 regression backend và luồng browser xác nhận/retry TTS. UI đóng gói được build lại từ source hiện tại. Xem `docs/reliability/R0_R6_FIXES.md`.

- 04/10/2026 — R5/R6: F8 được sửa bằng dependency telemetry đã khóa, chặn khi telemetry thiếu, reservation riêng volume data/temp, hysteresis và kiểm tra tài nguyên trong encode. Progress được drain liên tục, stderr bị giới hạn; UI có bước đang chạy/ETA ước tính, diagnostic ID liên kết event allowlist có rotation. R6 bổ sung matrix native 24 trường hợp restart + API resume/cancel, race autosave/commit và cleanup/startup, khóa registry trong lúc phục hồi, benchmark có encode thật và runner kiểm ZIP trong virtualenv mới. Các kết quả cuối ở `docs/reliability/R6_BASELINE.json`. Giữ lease cả job; VM sạch, volume giới hạn thật và provider có phí chưa nghiệm thu, không tuyên bố toàn bộ M0–M6 hoàn tất.
- Nghiệm thu R6 cuối: **390 backend, 63 frontend, 9 Chrome E2E** đều đạt; raw F1–F9 xanh, lint/build đạt. Cold/warm/resume cùng hash MP4; p95 health/queue khi encode thật **52,79/30,53 ms**. ZIP dùng UI mới, có Playwright config/E2E và LICENSE psutil; clean venv + bundled FFmpeg 7.1 đạt 18 regression và upload/render/restart. Fixture UI/ownership/HTTP dùng RAM mock 8 GiB, không thay admission của sản phẩm.

- R4: kết quả resume đồng bộ theo job/attempt/status, preview mở đúng export và draft được hòa giải khi thay đổi không trùng nhau. Auto-assemble chống gửi trùng qua hai tab/reconnect; Task Center chặn response cũ và có **Bỏ tác vụ và dữ liệu phục hồi**, cập nhật hàng đợi/dung lượng. F5/F9 đóng. Full suite đạt 347 backend + 61 frontend + 9 browser E2E Chrome; F8 × 2 vẫn là known red, chưa đủ cổng phát hành. Xem `docs/reliability/R4.md`.

- R3: kiểm tra revision/config trước enqueue và trước handler, giữ cấu hình hiệu lực trong worker; job xung đột có hành động tạo tác vụ từ bản hiện tại. Final MP4 đối chiếu dependency nguồn/font/renderer/encode/audio, cache project-result/ASR/analysis xác thực media và schema. F6/F7 đóng; F5/F8/F9 còn mở. Bằng chứng trong `docs/reliability/R3.md`.

- R2: Windows backend sở hữu cây child bằng nested Job Objects; shutdown giữ khóa instance khi worker còn có thể ghi. Job có snapshot nhưng chưa checkpoint được start lại bằng cùng ID sau restart, không tự encode/gọi AI. Task Center phân biệt start/checkpoint/bookkeeping; F3/F4 đóng, F5–F9 còn mở. Bằng chứng trong `docs/reliability/R2.md`.

- Cập nhật 03/10/2026 — R1: giữ staging upload phục hồi, journal di chuyển file và đối chiếu commit trước resume/cancel/cleanup. Kết quả đã ghi chỉ thử hoàn tất receipt/history khi bookkeeping lỗi, không chạy lại provider hay xóa export đã công bố. F1/F2 đóng; F3–F9 còn mở, chưa đủ cổng phát hành reliability. Xem `docs/reliability/R1.md`.

- Task Center hiển thị trạng thái kết nối và chi tiết lỗi/checkpoint; provider outcome chưa rõ cần xác nhận gửi lại trước khi có thể phát sinh request/phí mới.
- Diagnostic ZIP được tạo theo allowlist cục bộ. M6 thêm fault tests cho crash tại mux/commit receipt, phục hồi provider unknown, race resume, disk full/lease, queue trên 100 job, checkpoint retention và xác minh manifest gói ZIP.
- Gói ZIP chứa `docs/reliability`; entry phụ thuộc `IMPLEMENTATION_PLAN.md` đã bỏ để gói không yêu cầu khôi phục tệp đã xóa khỏi workspace.
- Chạy `python tests/benchmark_m6.py --output <đường-dẫn-ngoài-repo>` để đo cold/warm/resume cùng nguồn/FFmpeg và p95 API khi có 110 job chờ cộng một encode x264 720p đang chạy. Báo cáo phải giữ ngoài repo vì nó phản ánh môi trường máy nghiệm thu.
- Kết quả cổng M6 và giới hạn Windows/provider/media được ghi trong `docs/reliability/M6.md`; không tuyên bố nghiệm thu trên Windows sạch hay provider thật nếu chưa chạy các môi trường đó.

## Bản phát hành mã nguồn

Gói mã nguồn gồm backend, frontend đã build, dependency lock files, giấy phép, hướng dẫn chạy và bộ test. Trên Windows, cài Python 3.12+ rồi mở `MO_CLIPFORGE.bat`; lần đầu cần Internet để tạo môi trường Python và cài các package đã khóa. Node.js chỉ cần khi sửa và build giao diện.

Tạo lại gói ZIP bằng `python tools/package_release.py`. Gói được ghi vào `release/ClipForge-1.0.0-source.zip`; tệp `.sha256` bên cạnh dùng để kiểm tra tải xuống. Dữ liệu cá nhân, API key, `.venv`, `node_modules` và Git metadata không được đóng gói.

## Nội dung

- Cập nhật 02/10/2026: sửa trôi mốc ở các cảnh có thời lượng lẻ, đồng bộ AAC bằng clock 48 kHz nguyên mẫu, kiểm tra khung hình/âm thanh sau xuất và làm mới cache renderer cũ.
- Thư viện 10 sound effect tích hợp, cue thủ công, mức âm riêng và tự chèn ở chuyển cảnh/điểm nhấn lời đọc; preview và export cùng track.
- Template liên kết tên/mô tả/CTA với nội dung sản phẩm mới; hỗ trợ mẫu placeholder cũ, trường chỉnh tay và nhận diện qua AI từ source mới.

- Tái biên tập video hoặc dựng theo format mẫu, với timeline, phụ đề, voice, nhạc, logo và xuất MP4.
- Backup/restore dự án, migration schema tự động từ phiên bản 0–6 lên schema 7 và bản sao rollback trước lần ghi schema mới.
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

Ứng dụng tạo bản sao schema trước khi ghi project đã migration. Dùng mục **Dung lượng** để tải backup dự án; backup có thể bao gồm media và tùy chọn thêm export/lịch sử. Giữ cả mã nguồn/build cũ lẫn backup trước migration khi quay về phiên bản cũ. Không mở trực tiếp project schema 7 bằng backend cũ; khôi phục dữ liệu từ backup trước migration trước khi chạy bản cũ.
