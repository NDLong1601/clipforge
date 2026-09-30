# Kiểm tra bản bàn giao

Môi trường: Windows, Python 3.13, Node.js 24; backend phục vụ tại 127.0.0.1:8765.

- Build React/Vite thành công; npm audit khi cài báo 0 lỗ hổng.
- 11 kiểm thử pytest đạt. Sau chỉnh sửa cách nhóm và tô viền phụ đề, chạy lại 3 kiểm thử liên quan: 3 đạt.
- Xuất thật video 36 giây ở 720×1280 và 1080×1920, 30 fps, H.264/AAC, có nhạc và phụ đề tiếng Việt.
- Bộ kiểm thử xuất thật có tín hiệu voice riêng, music riêng, ducking, lớp logo trượt vào, lớp chữ hiện dần, chuyển cảnh fade, karaoke và SRT.
- Đã tạo giọng bằng Windows SAPI thực tế; file audio dài khoảng 6 giây đọc được. Giọng có sẵn trên máy không được xem là giọng tiếng Việt nếu hệ điều hành chưa cài.
- Bộ khởi động `python launch.py --setup-only` cài đầy đủ vào `.venv`; chạy `python launch.py --no-browser` nhận ra server đang chạy.
- Trình duyệt: mở/lưu/mở lại dự án, đổi preset, nhập video tham chiếu, phân tích nhịp mẫu, hoàn tác và xuất 1080p đã chạy. Sau tải lại không có lỗi JavaScript trong lần kiểm tra cuối; không tràn ngang ở 1500px và 390px.
- Mã AI được kiểm thử bằng phản hồi mô phỏng: định dạng HTTP/JSON, lựa chọn cảnh, template hợp lệ/sai và từ chối ID cảnh không tồn tại.

Chưa có API key nên chưa gọi trực tiếp OpenAI/Azure để kiểm chứng chất lượng giọng, nhận dạng tiếng Việt, chọn cảnh bằng AI và suy luận template. Chưa tải/chạy model Faster Whisper tùy chọn. Chưa đánh giá trên bộ video thật của người dùng; mẫu tích hợp dùng hình minh họa và nhạc tổng hợp.

Mã nguồn này là bản đầu chạy được theo các luồng đã mô tả trong README; bố cục phức tạp từ video tham chiếu vẫn cần chỉnh và duyệt.

## M0 — baseline và hồi quy (30/09/2026)

- Trước khi thêm test M0, chạy lại bộ cũ: 14 đạt.
- Sau khi thêm harness: 18 đạt, 7 `xfail` nghiêm ngặt. Bảy ca vẫn thể hiện đúng lỗi đã xác nhận trong `CODE_REVIEW.md`; chuyển từng ca sang test thường khi lỗi được sửa ở M1/M2.
- Frontend `npm ci` và `npm run build` thành công; npm audit không báo lỗ hổng.
- Tạo fixture synthetic tại thư mục tạm: portrait/landscape 30 fps, VFR, MOV metadata xoay 90°, stress video 60 giây, WAV voice/music và PNG trong suốt.
- Benchmark mặc định: phân tích 60 giây tạo 20 cảnh/thumbnail trong 5.13 giây; render preview 5 giây trong 1.80 giây; RAM peak process tree lần lượt 129.3 MB và 283.7 MB. Xem [M0_BASELINE.md](M0_BASELINE.md) để biết cấu hình và lệnh lặp lại.

Chưa có video thực hoặc voice tiếng Việt được duyệt trong workspace để chốt acceptance media; có thể truyền bằng `--real-video` và `--vietnamese-voice` cho `tests/generate_corpus.py`.

## M1 — Bảo vệ dữ liệu dự án

- Migration tuần tự schema 0→1→2→3; kiểm tra cấu trúc/liên kết trước khi lưu và kiểm tra file media riêng trước khi render.
- Ghi JSON bằng file tạm riêng và thay thế nguyên tử; giữ snapshot cho đến khi bản mới ghi xong. Kiểm thử lỗi ghi đĩa xác nhận bản hiện tại cùng snapshot phục hồi còn nguyên.
- Kiểm thử dữ liệu 100/101 cảnh, giữ toàn bộ scene và đuôi video mẫu; cô lập dự án JSON hỏng trong danh sách, phục hồi từ lịch sử độc lập và giữ nguyên bytes lỗi để phục hồi thủ công.
- Trước lần ghi nâng schema, giữ project JSON nguyên bytes cùng bản sao media có checksum trong `projects/<id>/backups/`; backup độc lập giới hạn 25 snapshot, có manifest hướng dẫn và API/UI tải ZIP để rollback. Backup được kiểm tra lại trước khi dùng.
- Xóa asset chuyển sang soft-delete; undo phục hồi voice/music/nguồn/logo và chỉnh sửa liên quan, đồng thời hợp nhất asset nhập sau. Các lựa chọn trong planner và giao diện ẩn asset đã gỡ.
- Cấu hình hiệu lực đọc secret từ môi trường nhưng không ghi secret mới ra settings; giá trị đã lưu tiếp tục được che và giữ lại. Có thao tác riêng để chuyển cấu hình legacy sang biến môi trường.
- Bộ dò cổng nhận ra ClipForge trên toàn dải trước khi chọn cổng trống; khóa hệ điều hành theo thư mục dữ liệu ngăn tiến trình thứ hai cùng ghi.

## M2 — Âm thanh, phụ đề, template và preflight (30/09/2026)

- Tạo lại cue yêu cầu revision hiện tại; có nút xác nhận giữ cue sau khi rà soát; nút tạo lại luôn dùng được và cảnh báo trước khi thay toàn bộ cue thủ công. Caption ghi đè theo cảnh không bị thay.
- Template đóng gói mọi ảnh đang hoạt động được tham chiếu trong lớp, gồm ảnh role `source`; khi sao chép sang dự án đích, ảnh được đăng ký role `overlay`. Index legacy, `layers`, `slot_layers` và danh sách thay thế dùng cùng quy tắc.
- `.venv\Scripts\python.exe -m pytest -q tests`: 57 đạt, không còn `xfail`; có một cảnh báo deprecation từ Starlette/httpx trong TestClient.
- `npm run build`: thành công với Vite 7.3.6; `python -m compileall -q backend` không báo lỗi.
- Audio voice/nhạc được chuẩn hóa sang WAV 48 kHz stereo và cache theo SHA-256 nội dung cùng cấu hình chuẩn hóa. Gain người dùng được áp sau chuẩn hóa; ducking đọc nhánh voice trước gain; mix cuối dùng limiter peak, không chạy loudnorm cuối chuỗi.
- Hồi quy render MP4 kiểm tra riêng voice, nhạc và hai track: 80% so với 10% xấp xỉ 18 dB; gain 0 tạo track im lặng.
- Schema hiện tại là phiên bản 3; giữ hỗ trợ nhạc video có audio. Migration làm sạch asset reference vô hiệu trên text/shape của dự án cũ, bao gồm snapshot lịch sử; validator vẫn từ chối reference đó trong dữ liệu mới.
- Đổi kịch bản giữ cue và đánh dấu stale; sửa cue rồi lưu/mở lại không còn stale và không làm mất cue khác. Render không cắt cue vượt timeline; preflight báo lỗi có mã và đề xuất sửa.
- Template lưu theo gói gồm manifest, JSON và ảnh; khi áp dụng, backend sao chép ảnh vào dự án rồi ánh xạ ID ở `layers` và `slot_layers`. Template JSON cũ tự tìm mọi ảnh từ dự án gốc; nếu không tìm thấy, UI yêu cầu chọn ảnh thay thế. Đã kiểm tra áp dụng lại cùng gói.
- API `/api/projects/{id}/preflight` và renderer dùng chung bộ kiểm tra file thiếu/không đọc được/sai loại, mốc nguồn, voice dài hơn timeline, cue, lớp ảnh và giới hạn thời lượng. API từ chối render khi có lỗi; renderer kiểm tra lại ngay trước khi dựng. Cảnh cần giữ khung hình cuối được báo dưới dạng cảnh báo.

### Sửa các findings trong `M0_M1_M2_REVIEW.md`

- F1: kiểm thử dự án schema 2 có nhạc dạng video, lớp text/rect/circle còn asset ID, render có audio và phục hồi từ snapshot lịch sử.
- F2: kiểm thử bytes JSON/media gốc, checksum và ZIP rollback; backup lỗi không thay project hiện tại; bản gốc còn sau 30 lần lưu và nhiều backup cùng schema được giữ riêng. Lỗi thay `project.json` sau khi backup cũng để lại cả file cũ lẫn backup hoàn chỉnh.
- F3: kiểm thử lưu/áp dụng/render gói template có ảnh role `source` và tìm/remap ảnh source trong template JSON legacy.
- F4: kiểm thử sửa cue sau khi đổi script, xác nhận cue với revision hiện tại, từ chối revision cũ và tạo lại cue sau khi đổi thời lượng trong khi giữ caption theo cảnh. Luồng sửa → lưu → mở lại và xác nhận stale đã được thao tác trên giao diện trình duyệt với dữ liệu tạm.

M0 vẫn chưa nghiệm thu video thực và voice tiếng Việt tự nhiên đã được phép dùng trong workspace.
