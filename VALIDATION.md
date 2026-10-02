# Kiểm tra bản bàn giao

Cập nhật xuất video, sound effect và template (02/10/2026): **149 backend test và 24 frontend test đạt; lint/build đạt**. Đã xuất lại hai project cũ ở 1080×1920/30 fps, kiểm tra clock AAC/hình và so sánh voice thấy độ trễ 0,0 ms. Browser xác nhận 10 effect, cue thủ công lưu lại, tên/mô tả cập nhật và giữ sau reload. Bằng chứng tại `review/export-sfx-template-fix/VALIDATION.md`, `old-project-results.json`, `voice-alignment.json`.

Bản hiện tại sau sửa M6–M7 (01/10/2026): **111 backend test và 18 frontend test đạt; F1–F8 đã được đóng bằng hồi quy**. Chi tiết ở mục **Hoàn thiện sau review M6–M7** cuối tài liệu. Các mục trước đó ghi lại kết quả và giới hạn tại từng lượt triển khai.

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

- Migration tuần tự schema 0→1→2→3→4→5; kiểm tra cấu trúc/liên kết trước khi lưu và kiểm tra file media riêng trước khi render.
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
- Schema tại M2 là phiên bản 3; giữ hỗ trợ nhạc video có audio. Migration làm sạch asset reference vô hiệu trên text/shape của dự án cũ, bao gồm snapshot lịch sử; validator vẫn từ chối reference đó trong dữ liệu mới. M4 nâng schema lên phiên bản 4 để thêm trạng thái khóa cảnh; M6 thêm migration 4→5 cho overlap, âm thanh nguồn, font và vùng an toàn.
- Đổi kịch bản giữ cue và đánh dấu stale; sửa cue rồi lưu/mở lại không còn stale và không làm mất cue khác. Render không cắt cue vượt timeline; preflight báo lỗi có mã và đề xuất sửa.
- Template lưu theo gói gồm manifest, JSON và ảnh; khi áp dụng, backend sao chép ảnh vào dự án rồi ánh xạ ID ở `layers` và `slot_layers`. Template JSON cũ tự tìm mọi ảnh từ dự án gốc; nếu không tìm thấy, UI yêu cầu chọn ảnh thay thế. Đã kiểm tra áp dụng lại cùng gói.
- API `/api/projects/{id}/preflight` và renderer dùng chung bộ kiểm tra file thiếu/không đọc được/sai loại, mốc nguồn, voice dài hơn timeline, cue, lớp ảnh và giới hạn thời lượng. API từ chối render khi có lỗi; renderer kiểm tra lại ngay trước khi dựng. Cảnh cần giữ khung hình cuối được báo dưới dạng cảnh báo.

### Sửa các findings trong `M0_M1_M2_REVIEW.md`

- F1: kiểm thử dự án schema 2 có nhạc dạng video, lớp text/rect/circle còn asset ID, render có audio và phục hồi từ snapshot lịch sử.
- F2: kiểm thử bytes JSON/media gốc, checksum và ZIP rollback; backup lỗi không thay project hiện tại; bản gốc còn sau 30 lần lưu và nhiều backup cùng schema được giữ riêng. Lỗi thay `project.json` sau khi backup cũng để lại cả file cũ lẫn backup hoàn chỉnh.
- F3: kiểm thử lưu/áp dụng/render gói template có ảnh role `source` và tìm/remap ảnh source trong template JSON legacy.
- F4: kiểm thử sửa cue sau khi đổi script, xác nhận cue với revision hiện tại, từ chối revision cũ và tạo lại cue sau khi đổi thời lượng trong khi giữ caption theo cảnh. Luồng sửa → lưu → mở lại và xác nhận stale đã được thao tác trên giao diện trình duyệt với dữ liệu tạm.

M0 vẫn chưa nghiệm thu video thực và voice tiếng Việt tự nhiên đã được phép dùng trong workspace.

## M3 — Autosave, redo và tác vụ nền (30/09/2026)

- Tách giao diện thành API client, hook cho phiên dự án/tác vụ/trạng thái xem trước và component panel; thêm Biome format/lint.
- Autosave có debounce, một lần lưu đang chạy cho dự án, revision/generation guard và bản nháp localStorage. Conflicts 409 giữ bản nháp và cung cấp lựa chọn so sánh/tải bản server mới.
- Undo/redo nhóm thao tác autosave có metadata riêng, hỗ trợ redo và xóa redo khi tạo nhánh chỉnh sửa mới.
- Job được lưu trong `data/jobs`; trạng thái đang chạy khi khởi động lại trở thành `interrupted` và không tự chạy lại. Job upload stage trước khi probe/thumbnail; upload và xử lý có tiến độ riêng, có thể hủy và dọn staging.
- Kiểm tra: `.venv\Scripts\python.exe -m pytest -q tests` — 61 đạt, 1 cảnh báo deprecation từ Starlette/httpx; `python -m compileall -q backend` — đạt; `npm run lint` — đạt; `npm run build` — đạt.
- Smoke test giao diện với thư mục dữ liệu tạm: đổi tên dự án tự lưu (nút Lưu trở lại trạng thái disabled), undo và redo khôi phục đúng tên. Chưa thao tác thủ công hai tab, phục hồi draft sau reload, luồng xung đột 409, tải file lớn hoặc hủy job qua UI. Bộ test tự động có kiểm tra revision conflict API, hủy job queued, phục hồi job bền và khả năng polling trong lúc probe.

## M4 — Timeline, waveform và preflight (30/09/2026)

- Thêm waveform audio thật qua `/api/projects/{id}/assets/{id}/waveform`; lấy peak mono 100 Hz, cache theo asset/kích thước/mtime và trả peak đã gom theo số điểm frontend cần.
- Timeline dùng cùng pixel/giây cho ruler, các cảnh, voice, nhạc và playhead. Có split 30 fps, trim trái/phải tối thiểu 0,2 giây, snap theo ranh giới cảnh/cue, nhân đôi/xóa/đổi thứ tự, zoom và hotkey; thao tác trim được lưu thành một lần chỉnh sau khi thả chuột.
- Thêm `Clip.locked` và migration schema 3→4. Planner giữ nguyên ID, nguồn, tốc độ, vị trí và thời lượng cảnh khóa; chỉ lập cảnh vào các khoảng còn lại. Mẫu không phù hợp khoảng trống hoặc cảnh khóa vượt thời lượng đích sẽ trả lỗi cụ thể.
- Preflight giữ chung kết quả API/render, tính lượng giữ khung theo source range, scene end và speed, có thể nhấn issue để tới đúng cảnh/tư liệu/cue/lớp. Bản preview lưu revision nguồn và hiện cảnh báo khi dự án đổi.
- Kiểm tra: `.venv\Scripts\python.exe -m pytest -q tests` — 66 đạt, 1 cảnh báo deprecation từ Starlette/httpx; `python -m compileall -q backend tests` — đạt; Biome lint và Vite build — đạt.
- Có test cho migration schema, giữ nguyên cảnh khóa/vị trí, báo xung đột thời lượng, tính frame-hold theo tốc độ và cache waveform. Chưa chạy browser E2E ở desktop/mobile cho kéo trim, phím tắt, preview hoặc điều hướng issue preflight.


## M5 — Backup, dung lượng và thư viện template (01/10/2026)

- Backup ZIP lấy snapshot khi dự án không có job đang chạy; project JSON, manifest, media và thumbnail được ghi theo luồng. Bản xuất và snapshot history/redo là tùy chọn; settings/API key không được đưa vào gói.
- Import stream file lên disk, giới hạn dung lượng/file count, từ chối đường dẫn traversal, symlink, file ngoài manifest và ZIP có checksum/kích thước sai. Nội dung được kiểm trong staging; tạo ID dự án, asset, scene, clip và layer mới; chỉ publish sau khi liên kết cùng file media qua được validation.
- Trang **Dung lượng** phân loại media, cache tái tạo, bản xuất, lịch sử/schema backup, template và thùng rác. Dọn cache hiển thị dung lượng/file trước, từ chối khi có job đang chạy và chỉ đụng tới `data/cache`. Xóa dự án chuyển folder nguyên vẹn vào thùng rác; có thao tác phục hồi và xóa hẳn riêng.
- Thư viện template hỗ trợ thumbnail SVG theo tỷ lệ, xem trước trước khi áp dụng, áp dụng theo tỷ lệ đã chọn, đổi tên/nhân bản/xóa template đã lưu và báo/ánh xạ tài nguyên ảnh thiếu. SVG dùng `preserveAspectRatio="xMidYMid meet"` cho ảnh/logo.
- Kiểm tra tĩnh: `python -m compileall -q backend`, `npm run lint` và `npm run build` đều đạt. Chưa chạy pytest hoặc thao tác E2E trên ZIP lớn, thùng rác và thư viện template trong trình duyệt.

## Sửa findings M3–M5 (01/10/2026)

Đã sửa cả F1–F10 trong `M3_M4_M5_REVIEW.md`:

- F1: trim lấy clip bất biến lúc pointerdown; nhiều pointermove cùng tọa độ không cộng dồn duration/source_start. Test cả hai cạnh, speed=2, quay về điểm bắt đầu và chỉ commit sau pointerup.
- F2: response server là nền, chỉ áp dụng chỉnh sửa phát sinh sau snapshot PUT. Voice bị bỏ khi đổi script không tự gắn lại; cue/warnings server giữ đúng. Thay voice có chủ đích và reorder/chỉnh clip trong lúc save vẫn được giữ.
- F3: đọc/prune history và redo theo revision trong JSON; hỗ trợ cả tên file revision và timestamp cũ. Snapshot mới có revision đứng trước timestamp. Test nhiều undo/redo, chỉnh sau redo, tên cũ và giới hạn 25 snapshot.
- F4: draft có owner riêng cho mỗi document, kể cả tab nhân bản kế thừa sessionStorage; reload tìm draft của document trước và tab mới có thể phục hồi draft còn trên đĩa. Save chỉ xóa payload đã được xác nhận; 409 ghi bền draft. Draft legacy được chuyển vào namespace mới sau khi ghi thành công. Test hai tab, 409/reload, chọn bản server mới, duplicate tab và draft legacy.
- F5: dùng đúng `jobs._persist`; cleanup staging chạy ngay cả khi ghi trạng thái cuối gặp lỗi đĩa. Test disconnect, lỗi copy do disk full và lỗi persist trạng thái cancelled; không commit media dở vào dự án.
- F6: backup dùng reservation riêng theo project thông qua cùng kiểm tra `jobs.busy`; không giữ jobs/store global lock suốt quá trình stream. Write/trash/job mới ở dự án backup bị từ chối; dự án khác vẫn lưu, polling, reserve và cancel được. Reservation/ZIP tạm được giải phóng khi lỗi ghi archive.
- F7: debounce theo generation chỉnh sửa cuối; autosave không flush liên tục các edit mới trong lúc response chậm. Save thủ công/đổi dự án/bắt đầu job vẫn flush qua cơ chế một PUT đang chạy. Test fake timer 1300ms và caller flush chờ PUT hiện có.
- F8: import-edit gọi nghiệp vụ `_update_project`; API test xác nhận HTTP 200 và nội dung được lưu.
- F9: tính total sau khi cộng xong mọi category, không cộng trùng other.
- F10: thumbnail rect/circle dùng `layer.color`, đồng nhất với renderer/live preview. Mẫu Nhịp nhanh giữ đúng màu #b5f36d.

Kết quả trên source sau sửa:

- Backend: `.venv/Scripts/python.exe -X utf8 -m pytest tests -q --tb=short -p no:cacheprovider` — **86 passed**, 1 cảnh báo deprecation từ Starlette/httpx; 58.27s. Thêm 20 ca hồi quy trong `tests/test_m345_review.py`; dữ liệu và render đặt trong TEMP.
- Frontend: `npm run test` — **12 passed**; harness Node/esbuild thực thi source với hooks, API, timer và storage mô phỏng. Không gọi AI/TTS thật.
- `npm run lint` — đạt, 14 file source; compile bằng `compile()` cho 29 file Python backend/tests — đạt.
- `npm run build` — đạt; đã cập nhật `frontend/dist` được backend phục vụ, không chỉ build ra thư mục tạm.
- Kiểm tra thêm: ZIP import vào data root sạch, giữ logo/voice/music/cue thủ công và lịch sử; render FFmpeg, dọn cache rồi render lại; template/trash; ZIP traversal/hash/symlink/file ngoài manifest bị chặn. Tùy chọn kèm exports/history và loại trừ settings/secret được kiểm tra bằng payload file.

Các findings được đóng theo test hồi quy trên. Nghiệm thu toàn bộ milestone vẫn cần browser desktop/mobile, ZIP nhiều GB và video/voice thực; test roundtrip đang dùng media tổng hợp ngắn, test exports kiểm tra payload archive. Cảnh báo TestClient không ảnh hưởng kết quả test.

## M6 — implementation status (01/10/2026)

- A1: response trợ lý đi qua schema chặt và kiểm tra clip/asset/locked state. UI chọn từng trường hoặc clip, so sánh thumbnail trước/sau, gửi candidate preview tới preflight và chỉ commit với revision cùng preview token còn hạn.
- A2: job lưu provider/model, fallback, số lần thử, thời gian, HTTP outcome và usage nếu có. Không lưu header/key. Timeout, lỗi mạng và HTTP transient retry hữu hạn; hủy dừng các lần gọi tiếp theo. Nhãn cảnh cache theo hash thumbnail/model/prompt.
- P1: phân tích frame tuần tự theo PTS đã chuẩn hóa từ frame đầu, lấy mẫu thấp độ phân giải, xét rotation trên frame đã decode và tạo thumbnail batch. Kết quả cache theo asset/file stat/cấu hình; thumbnail thiếu được tái tạo từ timestamp cảnh đã cache.
- P2: preview tạo proxy 720p cho video lớn; cache clip encode bao gồm nguồn, trim, speed, crop, layer asset/font và encoder, đồng thời kiểm tra media/kích thước/thời lượng khi tái sử dụng. Hardware encoder chỉ chọn khi encode probe thành công; CPU là fallback khi không khả dụng.
- E1/E2: font theo danh sách font cài, preflight nêu fallback thiếu font; caption có preset vùng an toàn. Crossfade dùng `sum(clip durations) - sum(overlap)` cho preview/render/audio/caption/voice; audio video nguồn có gain riêng.
- Kiểm tra tĩnh lần triển khai: `npm run build`, `python -m compileall -q backend` và `git diff --check` đạt. Không chạy pytest hoặc frontend test suite trong lượt này.
- Chưa đo benchmark M6 trên bộ video thực/VFR/MOV xoay, chưa chạy FFmpeg roundtrip mới cho crossfade/font tiếng Việt, và chưa gọi provider thật trong thay đổi này. Vì vậy các mục trên là implementation, chưa được đánh dấu nghiệm thu hiệu năng/chất lượng.

## M7 — Nghiệm thu và source release candidate (01/10/2026)

- Backend cuối lượt: `.venv\\Scripts\\python.exe -m pytest -q tests` — **92 passed**, một cảnh báo deprecation từ Starlette/httpx, 119.38s. `python -m compileall -q backend tests tools` và `git diff --check` — đạt.
- Frontend cuối lượt: `npm run test` — **12 passed**; `npm run lint` — đạt trên 14 file; `npm run build` — đạt với Vite 7.3.6.
- `tests/test_m6_acceptance.py` kiểm tra migration schema 4→5, preflight/overlap, render FFmpeg crossfade thật, media VFR và MOV xoay, token preview AI một lần và cấu trúc/manifest source ZIP. Test render xác nhận timeline dùng chung thời lượng overlap; cache thumbnail xoay được sinh theo hướng hiển thị.
- Smoke test qua trình duyệt từ app giải nén: tạo dự án minh họa, 14 clip/36 giây; sửa tay cue phụ đề và lưu; nhập PNG logo, gắn vào mẫu; chuyển sang “Theo mẫu”, dựng preview rồi xuất cuối. PyAV đọc được MP4 cuối 1080×1920, H.264/AAC, 36.0 giây; khung hình kiểm tra có logo, tiêu đề và phụ đề tiếng Việt.
- Setup chạy lại trong virtualenv mới từ ZIP giải nén dưới thư mục có khoảng trắng và dấu tiếng Việt; `launch.py --setup-only` hoàn tất. Launcher giờ thu output pip qua pipe để Rich không ghi trực tiếp đường dẫn Unicode vào console Windows. Server mở được tại cổng chỉ định.
- Browser responsive kiểm tra ở 736px và 390px; không còn nội dung chính rộng 0 khi sidebar ẩn, và `scrollWidth` tại 390px bằng 383px, nhỏ hơn viewport 390px.
- Benchmark cùng fixture synthetic 240×426, 15 fps, 60 giây trên Windows 11, Python 3.13.7, FFmpeg 7.1, 12 logical CPU/15.8 GB RAM: baseline M0 phân tích 5.13s; M7 là 1.55s (**giảm khoảng 70%**), 20 cảnh/20 thumbnail, peak 91.1 MB. Preview 5 giây cold: 9.07s/343.2 MB bằng `h264_qsv`; render lặp warm-cache: 3.31s/342.5 MB, cache hit 1/1. Baseline render M0 là 1.80s với cấu hình cũ; do M7 cold render vẫn chậm hơn, không tuyên bố tăng tốc tuyệt đối. Warm cache giảm khoảng 64% so với lượt cold; cần tối ưu thêm độ trễ preview lần đầu.
- `tools/package_release.py` tạo source ZIP gồm `frontend/dist`, source, dependency lock, license và test, kèm SHA-256 sidecar cùng manifest nội bộ; dữ liệu, secret, virtualenv và `node_modules` bị loại.

### Giới hạn còn lại trước nghiệm thu trên môi trường phát hành

- ZIP cuối được cài trong virtualenv mới ở máy phát triển, server khởi động tại cổng 18065, `/api/health` trả `ok`, và launcher lần hai nhận ra instance ClipForge đang chạy. Chưa nghiệm thu trên máy Windows mới hoàn toàn; cổng bị chương trình khác chiếm, thiếu dung lượng, thiếu media và phục hồi sau ngắt job chưa chạy thành checklist UI trên máy sạch. Một số quy tắc đã có kiểm thử API/unit.
- Hai luồng UI dùng media và nhạc tổng hợp của dự án mẫu. Chưa chạy TTS/ASR hoặc API OpenAI/Gemini/Azure thật trong lượt này; chưa nghiệm thu video thực/voice tiếng Việt được người dùng duyệt.
- Preview cold-cache hiện chậm hơn số baseline M0; chỉ ghi nhận lợi ích của cache ở lượt dựng lặp. M7 là **source release candidate**, chưa tuyên bố đã hoàn tất toàn bộ nghiệm thu phát hành trên môi trường máy mới.

## Hoàn thiện sau review M6–M7 (01/10/2026)

Đã đóng F1–F8 trong `M6_M7_REVIEW.md`:

- F1: lỗi hardware sau probe ở encode clip/join/final được thử CPU một lần, dọn output dở và chuyển encoder/cache sang CPU. Hủy, lỗi input và lỗi CPU không tạo vòng retry. Test fault injection đủ ba giai đoạn, kiểm tra cache CPU ở lần render tiếp theo và metadata bản xuất.
- F2: rotation 90°/270° của PyAV khớp FFmpeg autorotate theo nội dung ảnh. Đổi phiên bản cache; nút Phân tích tái tạo thumbnail cũ mà giữ ID cảnh/timeline, yêu cầu AI gắn nhãn lại ảnh xoay. Test so pixel với FFmpeg, giữ reference và bỏ qua thumbnail đã cập nhật.
- F3: planner tính incoming overlap trước kiểm tra collision; hai cảnh khóa có crossfade hợp lệ được giữ nguyên. Test chuỗi khóa toàn phần/một phần và transition của clip đầu.
- F4: preview và commit dùng chung normalization; script đổi gỡ voice và đánh dấu cue stale ngay trong candidate/preflight. Panel có preview phát/scrub cùng cảnh báo, token gắn với lựa chọn. Test preview không sửa bản lưu, từ chối thay lựa chọn/token và undo phục hồi script/voice.
- F5: catalog/resolver đọc family/style thật từ metadata font; alias filename của project cũ được chuẩn hóa. CSS/ASS dùng cùng family; font thiếu vẫn có cảnh báo. Test alias Arial và render thật chữ tiếng Việt với Arial.
- F6: live preview composite viewport/background/lớp của từng cảnh vào cùng nhóm; cảnh dưới opaque, cảnh trên fade. Audio gain được tính riêng. Test trọng số 0,5/0,5, lớp text/logo và audio; browser xác nhận nhóm có opacity 1/0,5.
- F7: caption của incoming clip có hiệu lực từ đầu transition; live, ASS và SRT dùng cùng cửa sổ caption. Incoming để trống trả lại voice cue. Test mốc phụ đề và grouping; MP4 thật chỉ có caption incoming trong overlap.
- F8: cache nhãn chứa identity endpoint và ghi theo route thực dùng sau fallback. Test chuyển endpoint, cache fallback, cancel/retry/telemetry không lộ secret. Smoke browser phát hiện và sửa thêm header Bearer rỗng khi API localhost không dùng key.

Kết quả trên source cuối:

- Backend: `.venv/Scripts/python.exe -X utf8 -m pytest tests -q --tb=short -p no:cacheprovider` — **111 passed**, **133,18s**, một cảnh báo deprecation Starlette/httpx. `tests/test_m67_review_fixes.py` thêm **19 ca** vào suite chính; data/render đặt trong TEMP.
- Frontend: `npm run test` — **18 passed**, gồm sáu ca hồi quy M6–M7. Biome lint đạt trên **16 file**; `npm run build` đạt và cập nhật `frontend/dist` thực tế. Compile in-memory **37 file Python** và `git diff --check` đạt.
- Probe frontend cũ đã xanh: opacity 1/0,5 cho RGB midpoint `[0.5,0,0.5]`. Probe backend trong `review/M6_M7/` giờ chuyển tiếp tới bộ hồi quy được duy trì, không còn là suite cố ý thất bại.
- Render hồi quy thực: MP4 1280×720, cảnh đỏ/xanh có viền xanh, crossfade, caption tiếng Việt; kiểm tra pixel blend, viền, glyph chữ, family ASS và SRT không chồng thời gian.
- Browser ở **1440×1000**: AI localhost mô phỏng → chọn đề xuất → preview/preflight → apply → undo. Candidate hiển thị bỏ voice/cue stale; apply bỏ voice và undo phục hồi script/voice. Ở **390×844**, document rộng **383px**, panel preview AI rộng **321px**, không tràn ngang.
- Browser dựng preview **720×1280** và export **1080×1920**, H.264/AAC, **5,5 giây** cho cả Tái biên tập và Theo mẫu; dữ liệu thử có voice/music tín hiệu, caption chỉnh tay, chữ và logo. Tái biên tập dùng crossfade; Theo mẫu chạy lại planner trước render. PyAV và ảnh trích từ MP4 kiểm tra đầu ra; kết quả trong `review/M6_M7/browser_export_results.json`.
- Windows TTS thực dùng **Microsoft David Desktop, en-US** tạo audio **2,369s**, cue `sentence-exact`. Máy chỉ có David/Zira en-US; kết quả này không xác nhận chất lượng giọng tiếng Việt. Không gọi provider tính phí hoặc ASR trong lượt này.
- Launcher nhận đúng ClipForge đang chạy ở 18089 và từ chối cổng 18090 do server khác chiếm, kiểm tra trên máy phát triển. ZIP được tạo lại sau khi cập nhật source/dist/docs; sidecar, manifest và bytes source được kiểm tra độc lập.

Phạm vi đóng lỗi đã đạt theo các kiểm tra trên. **M7 vẫn là source release candidate**: còn nghiệm thu Windows mới hoàn toàn, video/voice tiếng Việt thực được duyệt, ASR/provider thật và checklist UI disk full/media thiếu/job bị ngắt trên máy sạch. Benchmark cold-preview ghi ở mục trước là số đo trước lượt sửa này; chưa có số đo mới để khẳng định tăng tốc cold render.

## Dựng theo audio, chuyển cảnh mượt và che chữ nguồn (01/10/2026)

- Upload audio với tùy chọn tự dựng bật chạy nhận dạng lời đọc, phân tích thư viện và chọn cảnh theo nội dung; file audio thả vào nhóm nguồn được chuyển thành voice. Audio vẫn được lưu nếu nhận dạng/AI lỗi. Có nút chạy lại và tùy chọn tắt tự dựng.
- Chế độ dựng mượt đặt crossfade ngắn quanh mốc câu, tính overlap vào tổng thời lượng và điều chỉnh tốc độ cảnh ngắn để tránh giữ khung cuối. Live preview giữ video hiện tại và tải trước video kế tiếp.
- AI xác định vùng chữ chèn trên ảnh cảnh; render và preview dùng cùng vùng che/làm mờ trong tọa độ nguồn. Có điều chỉnh vùng theo từng clip, đặt lại vùng AI và chọn giữ chữ. Không tái tạo nền bằng inpainting; chữ di chuyển hoặc chỉ xuất hiện giữa cảnh cần xem lại và điều chỉnh vùng.
- Backend full suite đã chạy **125 passed**. Sau các bổ sung cuối, suite audio chạy **12 passed**, bao gồm FFmpeg thật cho cả che và làm mờ, cache invalidation, Gemini ASR, chuẩn hóa vùng AI 0–1000, audio thả vào thư viện nguồn và timeline khớp narration. Suite hồi quy liên quan chạy **31 passed**. Frontend **21 passed**, lint và build đạt; compileall và diff whitespace đạt.
- Gọi Gemini thật qua cấu hình sẵn có: nhận dạng `Audio.mp3` tiếng Việt dài **18,072 giây**, bốn đoạn lời đọc; phân tích **58 cảnh** từ ba video nguồn, lập timeline **6 clip**. Mốc Gemini là ước lượng theo câu, chưa xác nhận karaoke chính xác từng từ.
- Sau khi người dùng mở lại backend, chạy assemble-audio và render trên dự án `1fb1c7182463456e`. Kiểm tra MP4 thật **720×1280**, H.264, **18,072 giây**; preflight không có lỗi. Chỉnh thêm vùng che cho chữ xuất hiện giữa cảnh và mở rộng lề vùng chữ. Ảnh kiểm tra và kết quả nằm trong `review/audio_workflow/`; bản xem thử cuối được ghi trong `current_results.json`.
- Browser thực xác nhận hai tùy chọn tự dựng/mượt đang bật, sáu cảnh trên timeline, bản xuất hiện hành và không có console error. Luồng upload → phân tích audio → ghép cảnh tự động cũng đã chạy trên server kiểm thử riêng. Chưa đo benchmark độ trễ hoặc độ mượt trên các máy khác.

## Format review sản phẩm theo video người dùng (01/10/2026)

- Xem các mốc 0–20 giây của video tham chiếu 576×1024, dài 20,333 giây. Tạo artwork mới: thẻ bo góc, nền vàng có họa tiết nhẹ và giỏ hàng. Tiêu đề, mô tả, CTA là ba lớp chữ chỉnh được; giữ nội dung về tạ đeo tay của dự án.
- Áp dụng template **Review sản phẩm · Thẻ vàng**, ID `517e15cd0d2e486c`, vào dự án hiện tại. Gói mẫu chứa ảnh nền, API thư viện báo không thiếu tài nguyên. Bỏ các vùng che đen thủ công, đặt làm mờ; cắt khung và chọn cảnh sản phẩm để đưa chữ nguồn xuống dưới thẻ. Giữ audio, tổng thời lượng và sáu cảnh; thay cảnh mở đầu bằng cận cảnh sản phẩm và hai cảnh có chữ lớn bằng cảnh dùng điện thoại/làm việc nhà.
- FFmpeg đã dựng preview 720×1280 và bản cuối **1080×1920**, H.264/AAC, **30 fps**, **18,072 giây**. Preflight không có lỗi. Trích sáu mốc từ MP4 thật để xem thẻ, dấu tiếng Việt, khoảng cách phụ đề và chữ nguồn; kết quả trong `review/reference-format/results.json` và ảnh trong cùng thư mục.
- Sửa CSS `min-height: 0` cho grid item `.preview-pane`: video dọc trước đó làm pane cao 936,5 px trong grid cao 640 px, tràn qua timeline. Browser sau build xác nhận pane 639 px, video 475,8 px và nằm trọn trong grid. Build Vite và diff whitespace đạt; compile hai helper Python đạt. Không chạy lại suite backend cho thay đổi cấu hình/ảnh/CSS này.
- Browser xác nhận mẫu đã lưu, ba lớp chữ chỉnh được, chế độ làm mờ, bản MP4 revision 42 và không có console error. Ảnh chứng minh giao diện: `review/reference-format/clipforge-format.png`. Không sửa code backend hoặc yêu cầu khởi động lại server trong lượt này.

## Tiêu đề tương phản và nhịp review nhanh (01/10/2026)

- Đọc screenshot người dùng và xem các khung của video tham chiếu `tải xuống (8).mp4` (29,604 giây). Dùng nhịp chuyển góc để tham khảo; giữ nguồn tạ đeo tay và narration của dự án. Phân tích cut bằng FFmpeg ở nửa trên video chỉ là phép đo tham khảo, không phải nhãn cảnh chính xác.
- Đổi tiêu đề của mẫu **Review sản phẩm · Thẻ vàng** và dự án hiện tại từ vàng sang xanh than `#14334a`, nền/viền chữ màu kem. Tỷ lệ tương phản tính trên nền kem `#fff1c7` là **11,65:1**. Cập nhật cả helper tạo mẫu và gói mẫu đã lưu.
- Re-edit dự án `1fb1c7182463456e`: **18 clip**, 17 clip dài **1,0 giây**, clip cuối **1,072 giây**, tổng **18,072 giây**, cắt thẳng. Nhóm góc quay liên quan theo cụm 2–3 giây: sản phẩm, đeo tạ, việc nhà, đi lại, dùng điện thoại, vận động nhẹ. Thay góc nghỉ chân ở cụm đi lại bằng góc bước đi khác; kiểm tra lựa chọn trên khung hình nguồn thật.
- Chỉnh crop hai cảnh đeo tạ/đi bộ để chữ nguồn nằm sau thẻ vàng, bỏ blur ở các vùng đã khuất. Với nguồn ngang, làm mờ cả dải phụ đề ở đáy để chữ không lọt ra ngoài mép thẻ. Giữ lựa chọn tắt phụ đề của người dùng, voice ID, mốc câu và âm lượng voice. Thao tác qua API có revision và history; không đổi runtime backend/frontend hoặc khởi động lại server.
- Preflight không có lỗi. Bản cuối `59b09c28f65e45f0/video.mp4` là **1080×1920, H.264/AAC, 30 fps, 18,072 giây**, project revision **55**. Giải mã đủ **542 frame**; ở vùng footage thu nhỏ không có cặp frame liên tiếp giống hệt, không phát hiện đoạn giữ khung cuối. Xem 18 khung đại diện và khung chi tiết các cảnh được sửa để kiểm tra tiêu đề, chữ nguồn, blur và khung sản phẩm.
- Browser hiển thị bản MP4 1080×1920 revision 55, tiêu đề xanh than và timeline 18 cảnh. Ảnh chứng minh: `review/fast-rhythm/clipforge-final.png`; metadata và timeline: `review/fast-rhythm/results.json`; contact sheet: `review/fast-rhythm/final-contact.jpg`. Compile các helper Python và diff whitespace đạt. Không chạy lại full test suite cho thay đổi cấu hình/template và bản dựng này.

## Lưu format thành template tái sử dụng (01/10/2026)

- Chuẩn hóa gói đã lưu `517e15cd0d2e486c` thành **Review sản phẩm · Thẻ vàng · Nhịp nhanh**, giữ viewport, artwork, màu và vị trí chữ của format đã duyệt. Thư viện có một mục cho ID này; không tạo bản trùng.
- Nội dung mặc định trong thư viện là ô **TÊN SẢN PHẨM**, **MÔ TẢ SẢN PHẨM**, **KHÁM PHÁ SẢN PHẨM** để dùng cho sản phẩm mới. Tiêu đề xanh than, mô tả nâu; cả ba vẫn là lớp chữ chỉnh được. Nhịp mẫu lấy 18 ô từ bản hiện tại, chuyển cảnh cut, phụ đề tắt sẵn. Helper tạo format cũng cập nhật cut/18 ô/caption off.
- Schema validate qua API đạt; SHA-256 của ảnh nền khớp manifest, thumbnail có artwork và thư viện báo không thiếu ảnh. Áp dụng gói sang một project trong thư mục tạm độc lập đạt: ảnh tự sao chép, asset ID remap, mẫu và cấu hình caption/transition đúng. So sánh toàn bộ JSON dự án đang chạy trước/sau đạt, không thay timeline, nội dung hay exports của người dùng.
- Browser xác nhận mẫu trong thư viện và bố cục xem trước. Ảnh: `review/reusable-template/clipforge-template.png`; cấu hình/kết quả: `review/reusable-template/results.json`. Compile hai helper đạt; diff whitespace của tài liệu đạt. Không đổi backend/frontend runtime, không cần khởi động lại server.
- Nhịp 18 ô được planner hiện có co theo audio; template không lưu thiết lập project `smooth_transitions` hoặc tự đảm bảo mỗi audio mới có đúng một góc/giây. Hướng dẫn `review/reusable-template/HUONG_DAN.md` nêu cách tắt Dựng mượt, điều chỉnh số góc và chữ nguồn khi áp dụng cho tư liệu mới.

## Thêm template thẻ hồng trái tim (01/10/2026)

- Xem lại contact sheet và khung đầy đủ của `tải xuống (8).mp4` (576×1024, 29,604 giây). Tạo artwork mới từ hình học: nền hồng chuyển mềm, thẻ hồng/kem bo góc, huy hiệu trái tim và điểm sáng. Không lấy chữ hoặc hình sản phẩm từ video tham chiếu vào gói mẫu.
- Thêm gói **Review sản phẩm · Thẻ hồng · Trái tim**, ID `eaa4bd24ccee4c9a`, viewport cao 72%, nền phủ ở nửa dưới. Tiêu đề và mô tả là hai lớp chữ đỏ rượu chỉnh được để dễ đọc trên thẻ hồng. Không thêm CTA vì khung tham chiếu không có CTA. Lưu 18 ô/cut/caption off theo nhịp dựng đã dùng; planner vẫn co ô theo audio.
- Validate schema qua API, kiểm tra tài nguyên SHA-256 và áp dụng gói sang một project độc lập trong thư mục tạm đạt. Render FFmpeg thật 5 góc ở **720×1280, 30 fps, 5,0 giây**, xem năm khung đại diện để kiểm tra chữ tiếng Việt, tỷ lệ artwork và cắt nguồn. Chỉnh riêng góc đeo tạ cổ chân trong bản thử để chữ nguồn nằm sau mép thẻ hồng.
- Thư viện báo không thiếu tài nguyên. So sánh gói thẻ vàng và toàn bộ JSON dự án hiện tại trước/sau đạt, giữ nguyên bản đang dựng. Browser hiển thị cả hai mẫu và modal xem trước mẫu hồng; ảnh `review/pink-heart-template/clipforge-template.png`. Bản thử và kết quả ở cùng thư mục. Compile helper và diff whitespace đạt. Không sửa backend/frontend runtime hoặc khởi động lại server.

## Dựng video Vitamin khoảng 22 giây (01/10/2026)

- Dùng dự án Vitamin `5556f14238b84591` đã có lời đọc và mẫu thẻ vàng. SHA-256 của chín tư liệu nguồn và audio khớp thư mục sản phẩm được chỉ định. Giữ lời đọc, tăng tempo **1,279636×** bằng `atempo` từ **28,152** xuống **22,0 giây**, giữ cao độ; co các mốc câu tương ứng.
- Dựng **21 góc**, mỗi góc khoảng **0,94–1,21 giây**, cắt thẳng theo các cụm tóc/B7, B5, B2, B9, nhóm vitamin và Pentavite. Footage tăng tốc nhẹ **1,12×**. Tạo ba đoạn zoom từ ảnh B2/B5/B9 được cung cấp và crop cảnh viên vitamin để loại chữ Trung Quốc cùng nhãn hộp khác. Chọn/crop lại các góc Pentavite để chữ chèn cũ nằm ngoài khung hoặc sau artwork; giữ nhãn thật trên sản phẩm.
- Tiêu đề xanh than **PENTAVITE B PLUS / 8 LOẠI VITAMIN NHÓM B**, mô tả và CTA là chữ sửa được trong bản sao mẫu của dự án. Giữ phụ đề tắt. Mẫu trong thư viện và dự án tạ đeo tay trước đó không bị chỉnh.
- Preflight đạt, không có vấn đề. Xem ba khung đầu/giữa/cuối của cả 21 góc ở preview; chỉnh hai lựa chọn nguồn sau kiểm tra. Bản xuất cuối **652c30d0330d42b8/video.mp4**, revision **25**: **1080×1920, H.264/AAC, 30 fps, 22,0 giây**. Giải mã đủ **660 frame**, xem 21 khung đại diện; vùng footage thu nhỏ không có hai frame liền nhau giống hệt. Track audio đủ **22,0 giây**, có tín hiệu; phép đo này không thay cho duyệt chất giọng bằng tai.
- Bản giao `review/vitamin-22s/Vitamin_Pentavite_22s.mp4` có hash khớp bản xuất. Browser hiển thị dự án 21 cảnh/22 giây, góc kết sản phẩm và cấu hình 1080p; ảnh `review/vitamin-22s/clipforge-final.png`. Metadata/timeline/cues lưu ở `results.json`, khung thực ở `final-contact.jpg`. Compile helper dựng/kiểm tra và diff whitespace tài liệu đạt. Không đổi runtime hoặc khởi động lại server; không chạy lại full test suite cho bản dựng media này.

## Bổ sung ảnh B7 vào video Vitamin (01/10/2026)

- Đọc ảnh **B7.avif** 360×360 do người dùng cung cấp, chuyển thành nguồn PNG và đoạn phóng nhẹ 2 giây. Giữ nguyên file AVIF; B7/Biotin hiện đầy đủ phía trên thẻ vàng. Chèn vào cảnh 2 của đoạn mở đầu và cảnh 11 của phần liệt kê vitamin.
- Kiểm tra riêng audio 8,1–13,4 giây qua profile Gemini đang dùng: B7 được ước lượng nói tại **10,05–10,35 giây**. Dời cửa sổ hình B7 về **9,85–10,45 giây**, đồng thời co các góc B2/B5/B9 ở cụm này và thêm góc viên vitamin cuối cụm. Mốc Gemini là ước lượng, không cam kết căn từng phoneme. Timeline mới **22 góc / 22 giây**; giữ nguyên nội dung lời đọc, voice ID, âm lượng, mẫu và các cảnh khác. Preflight không có vấn đề.
- Kiểm tra audio sâu phát hiện bản AAC từ renderer có các burst packet với bước timestamp 1 sample và số mẫu decode vượt thời lượng 22 giây; chỉ kiểm tra metadata duration như lượt trước không đủ. Dựng lại riêng audio từ cùng voice đã chuẩn hóa, dùng `asetpts=N/SR/TB`, copy nguyên stream video. Lưu bản chưa sửa để đối chiếu; cập nhật file MP4 mới và metadata export qua hàm lưu có history. Không thay bản xuất đã giao trước hoặc khởi động lại server.
- Bản cuối **bc75f7a1f4094ecd/video.mp4**, revision **33**: **1080×1920, H.264/AAC, 30 fps, 22,0 giây**. Decode đủ **660 frame**, xem 22 khung; không có hai frame footage liền nhau giống hệt. Audio có **1033 packet**, bước PTS **1024 sample**, decode **1.056.768 sample** gồm phần padding AAC cuối; tín hiệu 22 giây tương quan **0,9999858** với voice qua limiter, sai số RMS tương đối **0,0049363**. Bản giao `review/vitamin-b7/Vitamin_Pentavite_22s_B7.mp4` khớp hash bản trong thư viện.
- Compile helper chèn B7 và helper kiểm tra đạt; diff whitespace tài liệu đạt. Kết quả/mốc audio/khung MP4 trong `review/vitamin-b7`. Lượt này sửa audio của bản xuất mới; lỗi timestamp trong pipeline render đang chạy chưa được sửa, nên bản tự xuất lại cần kiểm tra tương tự.

## Lọc khuôn mặt và sửa video Vitamin (02/10/2026)

- Thêm checkbox **Hỗ trợ bản quyền · tránh cảnh có khuôn mặt** trong Tư liệu. Bật checkbox lưu `avoid_faces` trước khi gọi job `filter-faces`, thay ngay những clip có mặt/chưa kiểm tra được; lựa chọn lưu theo dự án, áp dụng vào planner thường, planner AI và dựng theo audio. Nút lọc lại dùng sau khi chỉnh nguồn/mốc cắt. Cảnh khóa có mặt báo lỗi, không thay một phần timeline; khi không đủ cảnh sạch không quay về chọn cảnh có mặt. Preflight chặn clip có mặt/chưa có kết quả hợp lệ khi chế độ bật.
- YuNet chạy cục bộ, lấy mẫu 8 fps cộng đầu/cuối đoạn cắt, chuẩn hóa xoay và xét crop đang dùng. Cache theo file, mtime, kích thước, đoạn cắt, crop và phiên bản mô hình. Scene status do server quản lý; dữ liệu client không được tự đổi kết quả quét. Mô hình MIT được đóng gói với license, commit và SHA-256 trong `backend/assets/`. Lọc theo phát hiện mặt không xác minh quyền sử dụng tư liệu và có thể bỏ sót/báo nhầm.
- Dự án Vitamin giữ **22 clip/22 giây**, IDs/thời lượng/mốc B7, voice ID, âm lượng và toàn bộ template. Thay các góc ngủ/cận mặt/buồn nôn/người cầm lọ bằng gối-bàn tay, B2/B9 và lọ trong tay. Dùng một góc tóc sạch khác; giới hạn đoạn viên vitamin và đổi góc kết để qua bộ lọc bảo thủ. Lựa chọn tự động được rà lại để không dùng nhãn thuốc khác; màn hình điện thoại có ảnh mặt nhỏ đã được bỏ. Preflight cuối không có vấn đề.
- Tái hiện lỗi AAC với input H.264 ghép nhiều clip: chỉ thêm `asetpts=N/SR/TB` chưa đủ. Xuất audio riêng, xuất hình/phụ đề riêng, rồi mux hai stream; kiểm tra số AAC packet và clock ngay trong renderer. Bản 22 giây kiểm tra trên QSV có **1033 packet**, bước **1024 sample**, decode **1.056.768 sample** (gồm AAC padding), tương quan voice qua limiter **0,9999858**, sai số RMS **0,0049363**. Giữ bản lỗi để đối chiếu; áp dụng bản được kiểm tra vào export mới `c119ea3661f440b3`, revision **42**. Các bản xuất đã giao trước được giữ nguyên.
- MP4 cuối **1080×1920, H.264/AAC, 30 fps**, decode **660 frame**; xem 3 khung/clip và 12 khung bộ dò báo để loại các báo nhầm ở nếp gối, bàn tay/viên và tượng trang trí. Không thấy mặt người trong 66 khung duyệt và 12 khung báo. Mốc hình B7 vẫn là 1,094–2,188 và 9,85–10,45 giây. Bản giao `review/vitamin-no-faces/Vitamin_Pentavite_22s_KhongMat.mp4` khớp hash export.
- Backend suite sau sửa renderer: 137 bài đạt, một test fallback cũ còn trỏ tên bước xuất `preview.mp4`; đổi sang bước encode mới `final_video.mp4`, nhóm 30 bài liên quan đạt. Thêm test AAC cho timeline cắt lẻ, cả ba nhánh voice/music/ducking đạt. Frontend **22 bài đạt**, Biome lint và Vite build đạt; compile Python và diff whitespace đạt. Cấu hình/cảnh/packet/khung kiểm tra ở `review/vitamin-no-faces/`.
- Runtime checkbox đã hoạt động và được kiểm tra bằng giao diện. Bộ duyệt tự động chặn dừng helper server để nạp bản sửa audio; đã yêu cầu người dùng đóng/mở lại ClipForge, không dùng đường vòng để dừng tiến trình. File giao đã có âm thanh đúng; bản sửa renderer trên đĩa cần phiên server mới để áp dụng cho lần tự xuất tiếp theo.
- Browser tải lại xác nhận checkbox vẫn được tích, nhịp 22 giây và bản mẫu được giữ. Ảnh giao diện: `review/vitamin-no-faces/clipforge-checkbox.png`.
