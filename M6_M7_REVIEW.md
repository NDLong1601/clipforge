# Review M6–M7 — 01/10/2026

**Trạng thái sau sửa: đã đóng F1–F8 bằng test hồi quy, render thật và browser smoke. M7 vẫn là source release candidate, còn nghiệm thu trên Windows mới và media/provider thực.**

Suite cuối đạt **111 backend test / 18 frontend test**, lint/build đạt; đã cập nhật `frontend/dist` và tạo lại ZIP. Chi tiết: [VALIDATION.md](VALIDATION.md), mục **Hoàn thiện sau review M6–M7**. Kết quả hiện tại lưu trong `review/M6_M7/fix_results.json`; `results.json` giữ bằng chứng lỗi trước sửa.

| Finding | Kết quả sửa | Bằng chứng chính |
|---|---|---|
| F1 | Đã đóng | Fault injection hardware ở clip/join/final; CPU retry một lần, cache và metadata đúng; không retry cancel/input |
| F2 | Đã đóng | Pixel thumbnail rotation 90°/270° khớp FFmpeg; sửa thumbnail cũ không thay scene ID |
| F3 | Đã đóng | Planner giữ vị trí/cấu hình chuỗi clip khóa có overlap hợp lệ |
| F4 | Đã đóng | Normalization chung, preflight đúng candidate; browser preview/apply/undo; token gắn lựa chọn |
| F5 | Đã đóng | Family/style metadata, canonical alias cũ; font Arial và chữ tiếng Việt trong MP4 |
| F6 | Đã đóng | Nhóm cảnh opaque/fade có cả lớp; test audio độc lập; browser opacity 1/0,5 và pixel MP4 |
| F7 | Đã đóng | Live/ASS/SRT cùng mốc bàn giao; blank incoming trở lại cue voice |
| F8 | Đã đóng | Endpoint/cache route fallback, cancel và secret telemetry; localhost key rỗng hoạt động |

Nghiệm thu bổ sung: browser desktop/mobile cho panel AI, preview 720p/export 1080p ở hai chế độ, TTS Windows en-US và launcher cổng bận trên máy phát triển. Những kiểm tra này không thay thế Windows sạch, voice tiếng Việt tự nhiên hoặc ASR/provider bên ngoài.

## Review trước sửa — giữ lại để đối chiếu

**Kết luận tại thời điểm review: M6 chưa đạt nghiệm thu; M7 mới đạt mức source release candidate.**

Source đã có đầy đủ các nhóm chức năng chính, nhưng review tái hiện được **8 lỗi (1 P1, 7 P2)**. Bộ test hiện có vẫn xanh vì chưa bao phủ những tình huống này. Lượt review chỉ thêm báo cáo và script tái hiện trong `review/M6_M7/`, chưa sửa implementation.

## Findings trước sửa

### F1 — [P1] Export không chuyển CPU khi hardware encoder lỗi sau probe

- Vị trí: `backend/render.py:252`, `backend/render.py:288`, `backend/render.py:418`; encoder được giữ trong `_VIDEO_ENCODER` tại dòng 49.
- Probe 64×64 thành công chỉ xác nhận thiết bị khả dụng tại thời điểm kiểm tra. Khi GPU/driver không khả dụng ở lượt encode thật, lỗi FFmpeg đi thẳng ra job, không có lượt thử `libx264`. Các lượt sau tiếp tục dùng encoder đã cache.
- Tái hiện: giả lập hardware device unavailable sau probe thành công; danh sách encode attempt chỉ có `h264_qsv`, không có CPU retry. Job dừng ngay ở clip đầu. Đây là fault injection, không phải khẳng định driver QSV trên máy hiện tại đang lỗi.
- Cần sửa: fallback có giới hạn ở từng giai đoạn encode, xóa output dở và cập nhật encoder/cache key/telemetry đúng; không retry khi người dùng hủy hoặc lỗi đầu vào không liên quan encoder. Thêm test lỗi tại clip, join và encode cuối.

### F2 — [P2] Thumbnail MOV bị xoay ngược với video hiển thị

- Vị trí: `backend/media.py:113–116`.
- Với fixture có `frame.rotation=90`, code xoay clockwise, trong khi FFmpeg autorotate hiển thị theo chiều ngược lại. Thumbnail dùng cho thư viện/AI vì vậy có thể ngược 180° so với preview và MP4.
- Tái hiện bằng video hai nửa đỏ/xanh: thumbnail phân tích có phần trên đỏ `[253,1,0]`; thumbnail FFmpeg có phần trên xanh `[0,0,253]`. Sai số RGB trung bình là **168,36/255**. Hai ảnh đều có chiều dọc nên test hiện tại `height > width` vẫn qua.
- Cần sửa: đồng nhất quy ước rotation giữa PyAV/OpenCV và FFmpeg; đổi phiên bản cache analysis để loại thumbnail cũ. Test hướng nội dung ở cả 90° và 270°, không chỉ kiểm tra kích thước.

### F3 — [P2] Planner từ chối hai clip khóa có crossfade hợp lệ

- Vị trí: `backend/planner.py:112–115`.
- Planner coi `locked_start < last` là chồng lấn bất hợp lệ trước khi xét incoming overlap. Hai clip khóa kề nhau có crossfade đúng cấu hình vì vậy không thể dựng lại timeline, dù preflight chấp nhận.
- Tái hiện: hai clip 2,75s, overlap 0,5s, target 5s; vị trí khóa `[0, 2.25]`; preflight `ok=True`, planner báo “Vị trí các cảnh đã khóa bị chồng lấn”.
- Cần sửa: phân biệt overlap chuyển cảnh được phép và collision thật; lấp khoảng trống bằng cùng phép tính project clock. Giữ nguyên ID, nguồn, thời lượng và vị trí của cả hai clip khóa.

### F4 — [P2] Bản AI preview khác trạng thái thực tế sau commit

- Vị trí: `backend/main.py:589–600`, `backend/main.py:616`; normalization khi script đổi tại dòng 215–217.
- `assistant-preview` tạo và kiểm tra candidate còn voice cũ. `apply-ai` sau đó chạy `_update_project`, tự bỏ voice và đánh dấu cue stale khi script thay đổi. Người dùng duyệt một trạng thái nhưng lưu ra trạng thái khác; preflight cũng được chạy trước normalization.
- Tái hiện bằng API: preview `can_apply=True`, `voice_id` còn; commit HTTP 200, `voice_id=''`.
- Cần sửa: dùng chung bước tạo candidate đã normalization cho preview và commit; hiển thị tác động tới voice/cue trong bản duyệt và kiểm tra preflight trên trạng thái sẽ thực sự lưu. Test thêm undo cho đề xuất đổi script.

### F5 — [P2] Danh sách font dùng tên file thay cho tên family thực

- Vị trí: `backend/font_manager.py:48–61`, `backend/font_manager.py:88`; tên này được sử dụng trong CSS và ASS.
- Catalog đưa các alias như `arialbd` vào lựa chọn. Resolver trả family `arialbd`, `found=True`, dù font thực là `Arial`. Pillow đọc trực tiếp file nên vẫn vẽ được, còn trình duyệt/libass tìm theo family này và có thể fallback; preflight không báo thiếu font.
- Tái hiện trên Windows: catalog/resolved family `arialbd`; `ImageFont.getname()[0]` trả `Arial`.
- Cần sửa: đọc metadata family/style từ font, gom style theo family và trả tên family thực cho CSS/ASS; ánh xạ alias cũ. Kiểm tra wrap/cỡ chữ tiếng Việt bằng preview và MP4 với một số font được chọn.

### F6 — [P2] Crossfade live bị lộ background, khác blend trong MP4

- Vị trí: `frontend/src/components/Preview.jsx:42–43`, `frontend/src/components/Preview.jsx:157`.
- Hai plane chồng lên nhau đều giảm opacity. Ở giữa transition, plane sau opacity 0,5 che plane trước đã opacity 0,5, nên đóng góp thực của plane trước chỉ còn 0,25 và background còn 0,25. `xfade` trong renderer trộn hai cảnh với tổng trọng số 1.
- Probe thực thi source JSX tại midpoint cho opacity `[0.5,0.5]`. Với cảnh đỏ → xanh trên nền đen, phép compositing cho RGB chuẩn hóa `[0.25,0,0.5]`, thay vì `[0.5,0,0.5]`. Đây là kiểm tra component và phép compositing, chưa phải screenshot browser.
- Cần sửa: composite từng cảnh gồm video và các lớp thành một nhóm, giữ cảnh dưới opaque và fade cảnh trên hoặc dùng phép blend tương đương renderer. Tách gain audio khỏi opacity hình. Test cả viewport có viền/background và lớp text/logo.

### F7 — [P2] Caption theo clip chồng nhau lúc crossfade, live chỉ hiện caption mới

- Vị trí: `backend/render.py:123`, `backend/render.py:130`; live chọn incoming caption tại `frontend/src/components/Preview.jsx:128`.
- Renderer đặt caption từng clip trên toàn bộ duration của clip, rồi thêm cả hai override vào ASS/SRT. Trong overlap, cả hai dialogue cùng hoạt động; live chỉ chọn caption của incoming clip.
- Tái hiện: clip First 0–2s; clip Second bắt đầu 1,5s. SRT vẫn có First 0–2s và Second 1,5–3,5s, chồng **0,5s**. MP4 có thể xếp cả hai dòng trong cùng vùng phụ đề.
- Cần sửa: chốt quy tắc bàn giao caption trong transition, dùng chung cho live và ASS/SRT; cắt/ghép override theo các khoảng hiệu lực không chồng nhau. Giữ timing cue/voice trên project clock.

### F8 — [P2] Cache nhãn AI bị dùng chung giữa các endpoint khác nhau

- Vị trí: `backend/providers.py:183`, `backend/providers.py:207`.
- Cache key chỉ có loại provider/model/prompt/hash ảnh; không có identity endpoint. Hai API compatible cùng đặt tên model, hoặc cùng tên model trên hai server local, dùng chung nhãn dù người dùng chuyển endpoint và tắt fallback.
- Tái hiện: gọi profile `one` ở endpoint 19001, sau đó chọn `two` ở 19002 với cùng `same-model`; lần hai không gọi provider, tags vẫn là `one`.
- Cần sửa: đưa endpoint chuẩn hóa/identity provider vào cache key ở cả đọc và ghi; dùng route thực tế của lượt gọi thành công. Không đưa API key vào cache hay telemetry. Thêm test đổi endpoint và đổi fallback.

## Đối chiếu nghiệm thu tại thời điểm review

| Hạng mục | Đánh giá | Điều kiện còn lại |
|---|---|---|
| M6 A1 — duyệt AI | Chưa đạt | F4; kiểm tra candidate đúng trạng thái commit, lựa chọn và undo |
| M6 A2 — provider/cache | Đạt một phần | F8; bổ sung test retry/cancel/telemetry theo route |
| M6 P1 — phân tích cảnh | Đạt một phần | F2; kiểm hướng MOV thực tế, không chỉ width/height |
| M6 P2 — render/cache | Đạt một phần | F1; test cache invalidation theo từng cảnh và fallback ở encode thật |
| M6 E1 — font/phụ đề | Đạt một phần | F5; kiểm wrap/family tiếng Việt bằng MP4 thật |
| M6 E2 — overlap/audio | Chưa đạt | F3, F6, F7; đồng nhất planner/live/caption/render |
| M7 — gói source ZIP | Đạt kiểm tra cấu trúc/checksum | 103 entry manifest hợp lệ, source/dist trong ZIP khớp workspace |
| M7 — nghiệm thu phát hành đầy đủ | Chưa đạt | Sửa F1–F8 và hoàn thành checklist môi trường sạch/hai luồng đầy đủ |

`VALIDATION.md` và `RELEASE_NOTES.md` đã ghi đúng giới hạn của M7: chưa có Windows mới hoàn toàn, checklist cổng bận/disk full/media thiếu/job bị ngắt trên UI máy sạch, và hai luồng với TTS/ASR cùng video/voice tiếng Việt thực. Những phần này chưa được xem là đạt chỉ từ virtualenv mới trên máy phát triển. Review hiện tại không chạy lại browser smoke và không gọi dịch vụ AI/TTS tính phí.

## Kiểm tra đã thực hiện trước sửa

- Backend suite chính: **92 passed**, 1 cảnh báo Starlette/httpx, **112,46s**. Data/render nằm trong TEMP.
- Frontend suite chính: **12 passed**; Biome lint đạt 14 file; Vite build thành công ra TEMP và có cùng tên/hash asset JS/CSS với `frontend/dist` hiện tại.
- Script review backend: **7 failed / 7 case** theo assertion hành vi mong đợi; mỗi failure tái hiện một finding, **4,14s**. Không gọi provider thật.
- Script review frontend: **1 assertion failed**, tái hiện F6 bằng component source và phép alpha compositing.
- Release ZIP: SHA-256 sidecar hợp lệ; **103** manifest entry đúng checksum; không có file source đã thay đổi so với ZIP; không có entry trong các thư mục/tên riêng tư được kiểm tra (`data`, `.venv`, `node_modules`, `.git`, `.env`).
- `git diff --check`: exit 0; chỉ có cảnh báo chuyển line ending LF/CRLF.

Chạy lại probe trên Windows từ thư mục project (đặt `CLIPFORGE_DATA` và `--basetemp` vào TEMP):

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest review/M6_M7/test_review_repros.py -q -s --tb=short -p no:cacheprovider
node review/M6_M7/frontend_repro.cjs
```

Trên source trước sửa, các probe trả exit 1 như kết quả lịch sử ở trên. Sau sửa, backend probe chuyển tiếp tới `tests/test_m67_review_fixes.py` (19 ca đã vào suite chính); frontend probe xác nhận trọng số 0,5/0,5 và trả exit 0. Không dùng số liệu lỗi lịch sử làm trạng thái hiện tại.
