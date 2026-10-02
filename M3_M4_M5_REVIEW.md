# Review M3–M4–M5

Ngày review: 01/10/2026. Đối chiếu source hiện tại với `IMPLEMENTATION_PLAN.md`, mục M3–M5. Review không sửa mã chạy ứng dụng hoặc dữ liệu đang sử dụng.

**Cập nhật sau sửa, 01/10/2026:** đã sửa F1–F10; bộ test hiện tại đạt 86 backend / 12 frontend, lint/compile/build đạt. Các kết luận và ca thất bại dưới đây là bằng chứng ở thời điểm review trước sửa. Xem [VALIDATION.md](C:/Users/PC/Documents/clipforge-source/clipforge/VALIDATION.md) để biết kết quả xác minh và phạm vi nghiệm thu còn lại. Script frontend review hiện chạy bộ regression được duy trì; `frontend_results.json` giữ kết quả trước sửa.

## Kết luận nghiệm thu

| Mốc | Kết luận | Lý do |
|---|---|---|
| M3 | **Chưa đạt** | Có lỗi mất bản nháp giữa hai tab, phục hồi voice cũ trong autosave, undo sai sau redo, lỗi nhánh hủy upload; debounce chưa đúng yêu cầu. |
| M4 | **Chưa đạt** | Trim tính cộng dồn theo số sự kiện pointermove, làm sai cả duration và source_start. Các test backend về waveform, giữ clip khóa và preflight đã đạt. |
| M5 | **Đạt một phần, chưa nghiệm thu hoàn tất** | Roundtrip ZIP và các thao tác quản lý cơ bản đã chạy được, nhưng backup chặn job polling toàn ứng dụng; tổng dung lượng và thumbnail mẫu còn sai. Chưa có bằng chứng ZIP nhiều GB và browser trên màn hình nhỏ. |

**10 phát hiện đã xác nhận: 4 P1, 6 P2.** P1 cần xử lý trước khi coi bản biên tập này sẵn sàng bàn giao; P2 là lỗi chức năng/hiệu năng cần hoàn tất để nghiệm thu đúng phạm vi.

## Phát hiện cần sửa

### F1 — [P1, M4] Trim cộng lặp cùng một delta vào clip đã thay đổi

Vị trí: [Timeline.jsx:142](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/components/Timeline.jsx:142), cùng đoạn cập nhật `dragRef.current` ở dòng 185–190.

`delta` luôn tính từ tọa độ bắt đầu drag, nhưng `clip` lại lấy từ `original.clips`, tức bản preview đã bị sửa ở lần pointermove trước. Duration và source_start vì vậy tiếp tục cộng/trừ delta cũ mỗi lần nhận sự kiện, dù chuột vẫn ở cùng vị trí.

- Clip ban đầu 3s, kéo cạnh phải thêm 1s, gửi hai pointermove cùng tọa độ: thực tế thành **5s**, đúng phải là **4s**.
- Clip có source_start=5, duration=3, speed=2; kéo cạnh trái 0.5s, hai pointermove cùng tọa độ: thực tế source_start=7/duration=2; đúng phải là **6/2.5**.

Hướng sửa: mọi phép tính delta, giới hạn và snapping phải lấy clip bất biến trong `baseClips` lúc pointerdown. Preview chỉ là kết quả đầu ra của phép tính đó. Thêm test nhiều pointermove, quay về điểm bắt đầu, hai cạnh và speed khác 1.

### F2 — [P1, M3] Autosave chỉ rebase revision, làm mất thay đổi do backend chuẩn hóa

Vị trí: [useProjectSession.js:195](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/hooks/useProjectSession.js:195); hành vi backend liên quan tại [main.py:206](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:206).

Khi có chỉnh sửa mới trong lúc PUT đang chạy, hook giữ toàn bộ object local và chỉ lấy revision/updated_at từ response. Tuy nhiên backend có thay đổi hợp lệ ở các trường khác: đổi script sẽ bỏ voice_id cũ và cập nhật trạng thái cue/warnings.

Tái hiện: dự án đang có voice → đổi script → PUT chậm → trong lúc chờ, đổi âm lượng nhạc. Response đầu đã bỏ voice_id, nhưng PUT thứ hai gửi lại voice_id cũ cùng script mới. Backend nhận nó như việc chọn lại voice; dự án cuối cùng dùng lời đọc của kịch bản cũ. Harness giữ được âm lượng mới 0.2 nhưng gửi lại voice cũ; ca backend độc lập xác nhận request đó được chấp nhận.

Hướng sửa: lấy response server làm nền và áp dụng phần thay đổi phát sinh sau snapshot gửi đi, thay vì giữ cả object local. Không sửa backend thành cấm mọi lựa chọn lại voice vì người dùng vẫn có thể chủ động chọn file voice. Test cần kiểm tra cả dữ liệu chuẩn hóa từ server và chỉnh sửa mới của người dùng.

### F3 — [P1, M3] Undo sau redo rồi chỉnh mới bỏ qua phiên bản ngay trước đó

Vị trí: [store.py:499](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:499), [store.py:390](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:390).

Save thường đặt snapshot theo revision (`00000005.json`); redo đặt snapshot theo timestamp (`20261001T…-00000004.json`). `_valid_history` chọn bằng thứ tự tên file giảm dần, nên snapshot timestamp luôn đứng trước snapshot revision mới hơn.

Tái hiện đã chạy: A → B → C → Undo về B → Redo về C → sửa thành D → Undo. Kết quả là **B**, đúng phải là **C**. Sau đó chuỗi lịch sử cũng không còn phản ánh thứ tự thao tác.

Hướng sửa: dùng cùng một định dạng có sequence/revision sắp xếp được cho tất cả snapshot, hoặc chọn theo metadata thứ tự rõ ràng. Kiểm tra cả giới hạn 25 snapshot vì đoạn dọn lịch sử cũng đang sort theo tên. Bổ sung chuỗi nhiều lần undo/redo và chỉnh mới sau redo.

### F4 — [P1, M3] Một tab lưu thành công có thể xóa draft của tab đang conflict

Vị trí: [useProjectSession.js:6](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/hooks/useProjectSession.js:6), [useProjectSession.js:189](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/hooks/useProjectSession.js:189).

Draft dùng một key localStorage chung theo project ID. Khi tab A lưu xong, nó xóa key đó vô điều kiện, kể cả nội dung hiện tại của key thuộc chỉnh sửa mới của tab B. Nhánh bắt 409 giữ bản trong memory nhưng không ghi lại draft vừa bị xóa.

Tái hiện: A/B cùng mở revision 1; A gửi save; B chỉnh tên và ghi draft; A nhận save thành công; B save gặp 409. UI B có conflict nhưng **localStorage không còn draft**. Reload/đóng tab B lúc này làm mất chỉnh sửa chưa lưu.

Hướng sửa: phân biệt draft theo tab/session và generation/operation; chỉ xóa draft đúng bản đã xác nhận lưu. Ghi bền bản đang conflict. Test hai session dùng cùng localStorage, save chồng thời gian và reload sau 409.

### F5 — [P2, M3] Nhánh lỗi/hủy upload gọi hàm không tồn tại

Vị trí: [main.py:351](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:351).

`_persist(job)` không được định nghĩa/import trong main.py; hàm nằm ở jobs.py. Khi upload bị disconnect, vượt giới hạn trong lúc copy hoặc gặp lỗi ghi file, handler phát sinh `NameError`, thay lỗi ban đầu bằng lỗi server. Lệnh dọn staging ngay sau đó không được chạy và trạng thái cuối không được lưu bền đúng cách.

Đã tái hiện bằng UploadFile nhỏ với request báo disconnected: `Cancelled` bị thay thành `NameError: name '_persist' is not defined`.

Hướng sửa: gọi cơ chế cập nhật/persist trạng thái đúng namespace; đặt cleanup trong finally để vẫn chạy khi persist lỗi. Bổ sung test cancel trong lúc nhận dữ liệu và lỗi ghi đĩa, kiểm tra cả staging lẫn record job trên đĩa.

### F6 — [P2, M5/M3] Tạo backup giữ khóa toàn cục suốt lúc stream ZIP

Vị trí: [portable.py:66](C:/Users/PC/Documents/clipforge-source/clipforge/backend/portable.py:66).

`jobs.LOCK` và `store.LOCK` được giữ qua toàn bộ quá trình đọc media, tính hash và ghi ZIP, có thể tới 10GB. Job polling, cancel/reserve và lưu dự án khác đều phải chờ các khóa này; job đang chạy ở dự án khác cũng có thể bị chặn lúc commit. Streaming giảm RAM nhưng chưa giữ giao diện/tác vụ đáp ứng.

Đã giữ một bước ghi ZIP bằng barrier và gọi `jobs.all_jobs` cho dự án khác: polling bị chặn cho tới khi bước ghi ZIP được giải phóng.

Hướng sửa: dùng khóa/snapshot riêng theo dự án và chỉ giữ khóa toàn cục trong đoạn kiểm tra/đặt trạng thái ngắn. Các đường ghi/trash của cùng dự án cần tuân theo cùng cơ chế để giữ snapshot nhất quán. Test polling, cancel và save dự án B trong lúc backup A đang stream.

### F7 — [P2, M3] Autosave chưa debounce theo lần chỉnh sửa cuối

Vị trí: [useProjectSession.js:280](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/hooks/useProjectSession.js:280).

Effect chỉ phụ thuộc dirty/project ID/conflict/save. Khi dirty đã là true, các lần gõ tiếp không thay dependency nên timer 1300ms ban đầu không được reset. Save có thể chạy giữa lúc gõ; vòng while sau response lại lưu ngay chỉnh sửa mới. Hành vi này không đạt yêu cầu “1–2 giây ngừng chỉnh”, có thể tạo nhiều lần ghi/snapshot hơn dự kiến.

Harness xác nhận timer không đổi sau lần chỉnh thứ hai. Hướng sửa: theo dõi generation/editedAt hoặc reset timer ngay khi edit; tách flush bắt buộc cho đổi dự án/job khỏi lịch autosave. Thêm fake-timer test gõ liên tục và đếm PUT sau khi ngừng gõ.

### F8 — [P2, M3] API import-edit bị hỏng sau thay chữ ký update_project

Vị trí: [main.py:507](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:507).

`update_project` nay bắt buộc có `request` để đọc history-operation header, nhưng `/api/projects/{pid}/import-edit` vẫn gọi nó với hai tham số. Một Project hợp lệ cũng phát sinh `TypeError: update_project() missing 1 required positional argument: 'request'`; endpoint trả lỗi server.

Hướng sửa: gọi `_update_project` từ nghiệp vụ import hoặc chuyển Request đúng cách. Thêm API test cho import JSON chỉnh sửa còn được hỗ trợ. Lỗi này không ảnh hưởng endpoint import ZIP mới.

### F9 — [P2, M5] Tổng dung lượng cộng hai lần phần other của dự án

Vị trí: [portable.py:514](C:/Users/PC/Documents/clipforge-source/clipforge/backend/portable.py:514).

`total` đã chứa other trong `sum(category_totals.values())`, sau đó lại cộng toàn bộ other khi thêm dữ liệu ứng dụng. Dự án thử chỉ có project.json 1026 byte được báo tổng **2052 byte**, trong khi tổng các category là **1026 byte**.

Hướng sửa: tính total một lần sau khi hoàn tất mọi category hoặc chỉ cộng phần bổ sung. Test tổng category và tổng file thực tế với nhiều dự án, cache và trash.

### F10 — [P2, M5] Thumbnail template dùng sai màu của lớp hình khối

Vị trí: [template_packages.py:225](C:/Users/PC/Documents/clipforge-source/clipforge/backend/template_packages.py:225).

SVG dùng `layer.background` làm fill của rect/circle, trong khi renderer và live preview dùng `layer.color`. Với mẫu có sẵn “Nhịp nhanh”, thanh màu cần là **#b5f36d** nhưng thumbnail thành **#000000**. Người dùng xem bố cục/màu không đúng trước khi áp dụng.

Hướng sửa: thống nhất thuộc tính màu hình khối với renderer/live preview; test các mẫu có sẵn và mẫu tùy chỉnh. Khi làm QA hình ảnh nên kiểm tra thêm opacity và lớp theo slot vì thumbnail hiện chưa thể hiện đầy đủ các trường này.

## Bằng chứng kiểm tra

- Bộ test của dự án: **66 passed**, 1 cảnh báo deprecation từ FastAPI/Starlette TestClient; 53.99s. Không có file `tests/test_m5.py` ở source được review.
- `npm run lint`: đạt, Biome kiểm tra 12 file.
- Build production: đạt, Vite build ra thư mục tạm ngoài dự án.
- Compile toàn bộ Python backend bằng `compile()`: đạt, không tạo bytecode trong source.
- Bộ kiểm tra review bổ sung: **14 ca, 8 đạt / 6 thất bại**, xác nhận F3/F5/F6/F8/F9/F10. Ca voice là kiểm tra ảnh hưởng request do hook gửi sai, không yêu cầu backend cấm người dùng chủ động chọn lại voice.
- Harness thực thi hook/component từ source qua esbuild, với React hooks/API/pointer listeners mô phỏng: xác nhận F1/F2/F4/F7. Hai lần pointermove cùng tọa độ là đầu vào có chủ đích để chứng minh tính bất biến của trim.

Các kiểm tra bổ sung đã đạt: ZIP → import vào data root mới, ID project/assets mới và tham chiếu hợp lệ, giữ phụ đề thủ công, undo/redo lịch sử import, render MP4 qua FFmpeg; dọn audio cache rồi render lại; đóng gói/đổi tên/nhân bản/xóa/áp dụng template; trash/restore/xóa hẳn; chặn dọn cache khi có job; từ chối ZIP traversal, đường dẫn ổ đĩa, hash sai, symlink và file ngoài manifest, không publish dự án dở.

Giới hạn bằng chứng: media roundtrip là ảnh/logo và audio tổng hợp ngắn. Chưa nghiệm thu audio tiếng Việt tự nhiên, video thực/VFR/MOV xoay, ZIP nhiều GB, tùy chọn kèm bản xuất trong ZIP, dịch vụ AI/TTS thật hoặc E2E browser desktop/mobile. Harness không thay thế kiểm tra thao tác thật trên trình duyệt.

## Thứ tự hoàn thiện đề xuất

1. Sửa F1–F4 trước để bảo vệ nội dung chỉnh sửa. Chuyển ca tái hiện thành regression test thích hợp, gồm frontend test có response chậm và shared localStorage.
2. Sửa F5/F7/F8; nghiệm thu upload đang nhận dữ liệu, debounce và các endpoint cũ.
3. Sửa F6/F9/F10; bổ sung bộ test M5 vào `tests`, đo backup lớn với job polling/cancel đồng thời.
4. Chạy E2E hai tab → conflict → reload; trim/split với speed khác 1; replan cảnh khóa; preflight chọn đúng đối tượng; preview revision cũ; desktop và chiều rộng nhỏ.
5. Nghiệm thu ZIP kèm exports/history vào data root sạch rồi render bằng video/voice thực. Khi các lỗi trên và các ca acceptance tương ứng đều đạt mới đánh dấu M3–M5 hoàn tất.

## Chạy lại các ca review

Các script nằm trong [review/M3_M4_M5](C:/Users/PC/Documents/clipforge-source/clipforge/review/M3_M4_M5/backend_repro.py). Chạy từ thư mục `clipforge`; mọi dữ liệu kiểm thử đặt trong TEMP. Các assertion thất bại là bằng chứng trên source hiện tại, không phải bộ test đã được sửa cho đạt.

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:CLIPFORGE_DATA = Join-Path $env:TEMP 'clipforge-m345-review-import'
$env:PYTHONDONTWRITEBYTECODE = '1'
& '.venv/Scripts/python.exe' -X utf8 -m pytest review/M3_M4_M5/backend_repro.py -q --tb=short -p no:cacheprovider --basetemp (Join-Path $env:TEMP ('clipforge-m345-review-' + [guid]::NewGuid().ToString('N')))
node.exe review/M3_M4_M5/frontend_repro.cjs
```

[Kết quả harness frontend](C:/Users/PC/Documents/clipforge-source/clipforge/review/M3_M4_M5/frontend_results.json).
