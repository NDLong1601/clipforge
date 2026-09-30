**Kế hoạch triển khai ClipForge Local — 30/09/2026**

Mục tiêu: sửa các lỗi đã xác nhận trong CODE_REVIEW.md, bảo vệ dữ liệu khi chỉnh sửa/nâng cấp, rồi hoàn thiện quy trình dựng video từ nhập tư liệu đến xuất thành phẩm.

Giả định để lập kế hoạch: ứng dụng Windows chạy cục bộ cho một người dùng; tiếp tục dùng React, FastAPI, FFmpeg và lưu dự án dạng JSON. Thời gian là ước lượng ngày công cho một lập trình viên hiểu source, đã gồm kiểm thử và sửa lỗi thông thường. Đây là dự toán, cần hiệu chỉnh sau mốc đầu tiên và benchmark video thực.

| Mốc | Phạm vi | Ngày công dự kiến | Phụ thuộc |
|---|---|---:|---|
| M0 | Baseline, dữ liệu thử, bộ kiểm tra hồi quy | 1–2 | Không |
| M1 | An toàn dữ liệu, hoàn tác, secret, khởi động một instance | 4–6 | M0 |
| M2 | Sửa âm lượng, phụ đề và template có logo; preflight cơ bản | 4–6 | M1 |
| M3 | Tách frontend, autosave, redo, tác vụ nền và upload | 6–9 | M1–M2 |
| M4 | Timeline, waveform và kiểm tra trước khi xuất | 5–8 | M3 |
| M5 | Backup/import ZIP, dọn dữ liệu, quản lý template | 4–6 | M1–M3; dùng lại preflight M4 |
| M6 | AI dễ duyệt, tối ưu phân tích/render, công cụ trình bày | 7–11 | M3–M5 |
| M7 | Nghiệm thu toàn luồng và đóng gói phát hành | 3–4 | Các mốc trong phạm vi bản phát hành |
| **Tổng** | **Toàn bộ phạm vi bên dưới** | **34–52** | **Khoảng 7–11 tuần làm việc** |

Nên bàn giao ba đợt: bản sửa lỗi sau M2; bản biên tập và quản lý dữ liệu sau M5; bản nâng cấp AI/hiệu năng sau M7. Mỗi đợt đều phải đạt các kiểm tra phát hành phù hợp, không đợi tới M7 mới kiểm thử. M6 có thể cắt nhỏ thành các bản bổ sung theo giá trị sử dụng và kết quả benchmark.

**M0 — Chốt baseline và chuẩn bị cách kiểm tra**

1. Ghi nhận phiên bản source, Python/Node/FFmpeg, dependency lock và kết quả kiểm thử hiện tại: 14 test đạt, frontend build được. Kiểm tra ranh giới Git trước khi tạo nhánh vì thư mục source hiện đang nằm trong một Git repository ở cấp cha, chứa cả nội dung ngoài dự án; chỉ quản lý thay đổi thuộc ClipForge.
2. Chuyển bảy ca tái hiện trong báo cáo thành test hồi quy độc lập. Các test ban đầu phải thể hiện đúng lỗi đã xác nhận, sau đó chuyển sang đạt khi sửa từng lỗi.
3. Tạo fixture cho dự án cũ, template có logo, tham chiếu 101 cảnh, voice/music, phụ đề sửa tay, file dự án hỏng và hai tab chỉnh cùng dự án.
4. Chuẩn bị bộ media tổng hợp cho test ổn định; chọn thêm video thực để nghiệm thu: video dọc/ngang, 30 fps và tốc độ khung hình biến đổi, MOV có metadata xoay, video dài, ảnh PNG trong suốt, voice tiếng Việt.
5. Đặt toàn bộ test data và kết quả render trong thư mục tạm; đo thời gian phân tích/render, RAM và dung lượng cache để làm baseline.

Đầu ra: bộ kiểm tra nền, danh sách fixture và lệnh chạy có thể lặp lại. Nghiệm thu: tái hiện đủ bảy lỗi; bộ test cũ vẫn chạy được; số đo hiệu năng ghi rõ thiết bị, media và cấu hình.

**M1 — Bảo vệ dữ liệu dự án và sửa lỗi lưu trữ**

| ID | Công việc | Vị trí chính | Tiêu chí nghiệm thu |
|---|---|---|---|
| D1 | Xác thực toàn bộ Project và các liên kết asset/clip/voice/music/layer trước khi lưu; tách kiểm tra cấu trúc khỏi kiểm tra file vật lý | models.py, store.py; validator dùng chung mới | Dữ liệu không hợp lệ không thay bản đang dùng; lỗi cho biết trường/cảnh liên quan |
| D2 | Xử lý mẫu vượt giới hạn ô; bảo toàn tổng thời lượng và thư viện cảnh gốc | main.py, planner.py | Mẫu 100 và 101 cảnh xử lý có kiểm soát, lưu rồi mở lại được |
| D3 | Cô lập dự án hỏng khỏi API danh sách; phục hồi từ lịch sử hợp lệ mà không phụ thuộc đọc bản hiện tại | store.py, main.py, danh sách dự án frontend | Một file JSON hỏng không chặn dự án khác; bản hỏng được giữ để phục hồi thủ công |
| D4 | Sửa xóa mềm và undo để giữ đủ metadata/tư liệu; cập nhật lọc tư liệu trong planner và giao diện | models.py, store.py, main.py, frontend | Gỡ voice/music/nguồn/logo rồi hoàn tác đúng; nguồn mới nhập vẫn còn; không xuất hiện tham chiếu thiếu |
| D5 | Tách cấu hình trên đĩa, cấu hình hiệu lực và dữ liệu trả về giao diện | store.py, providers.py, request/response settings | Đổi trường không liên quan không ghi khóa từ môi trường ra file; khóa đã lưu được che và giữ đúng |
| D6 | Tìm instance hiện có trước khi chọn cổng; khóa theo thư mục dữ liệu ở vòng đời backend | launch.py, vòng đời app trong main.py | Hai lần mở gần đồng thời chỉ có một tiến trình ghi cùng data; cổng 8766 đang chạy vẫn được nhận diện khi 8765 trống |

Quyết định kỹ thuật cho D2: giữ toàn bộ scene đã phát hiện trong asset; khi tạo ô template mới, gộp các khoảng liên tiếp nếu cần để nằm trong giới hạn schema và thời lượng đầu ra. Giữ tổng thời lượng, thứ tự và lưu ghi chú rằng nhịp đã được đơn giản hóa. Không cắt bỏ im lặng phần đuôi video mẫu. Nếu chưa tìm được cách gộp hợp lệ, trả lỗi có hướng xử lý và giữ nguyên dự án trước tác vụ.

Quyết định kỹ thuật cho D4: thêm trạng thái xóa mềm cho asset, vẫn giữ ID, metadata và file. Snapshot lịch sử ghi nhận tập tư liệu đang hoạt động và trạng thái chỉnh sửa. Khi undo, phục hồi trạng thái hoạt động của tư liệu cũ, hợp nhất tư liệu mới nhập theo ID và giữ đường dẫn do server quản lý. Chỉ dọn file khi không còn được dự án, lịch sử hoặc template tham chiếu.

Bổ sung schema_version cho dữ liệu có thay đổi định dạng; đọc dự án cũ qua migration xác định. Việc ghi file phải validate trước, dùng file tạm riêng và thay thế nguyên tử. Chỉ xóa snapshot lịch sử/redo sau khi bản mới ghi thành công. Kiểm tra cả lỗi ghi đĩa và tiến trình bị ngắt để tránh mất đồng thời bản hiện tại lẫn bản phục hồi.

Với khóa môi trường đã bị ghi từ trước, không suy đoán để tự xóa secret. Cung cấp chuyển cấu hình về dùng môi trường theo lựa chọn rõ ràng của người dùng; bản sửa chỉ ngăn phát sinh việc ghi ngoài ý muốn.

**M2 — Sửa kết quả dựng video và hoàn tất bảy lỗi**

1. **Âm lượng:** chuẩn hóa đầu vào, áp dụng gain người dùng, ducking/trộn, cuối cùng giới hạn peak. Cache kết quả chuẩn hóa theo hash nội dung và cấu hình. Với nguồn thử không chạm limiter, mức 80% phải lớn hơn 10% khoảng 18 dB, sai số dự kiến không quá 1 dB; mức 0 phải tắt track đó. Kiểm tra voice riêng, music riêng và hai track cùng phát. Không dùng loudnorm cuối chuỗi để vô tình xóa gain đã chọn.
2. **Phụ đề thủ công:** tách nguồn mốc thời gian và trạng thái người dùng đã sửa. Thay cảnh/lập lại timeline phải giữ nội dung và mốc đã chỉnh. Đổi kịch bản đánh dấu phụ đề cần kiểm tra và có thao tác riêng để tạo lại mốc. Nếu timeline ngắn hơn cue, báo lỗi/cảnh báo cụ thể thay vì tự sửa mất nội dung.
3. **Template có tài nguyên:** thêm gói template gồm manifest, JSON và ảnh cần dùng. Áp dụng mẫu qua thao tác backend để sao chép/ánh xạ tài nguyên sang dự án mới, cập nhật mọi tham chiếu trong layers và slot_layers. Với template cũ chỉ có ID logo, dùng metadata dự án gốc nếu còn tìm được; nếu thiếu thì yêu cầu chọn ảnh thay thế trong giao diện.
4. **Preflight cơ bản:** một bộ kiểm tra dùng chung cho API và renderer, trả danh sách issue có code, mức độ, đối tượng liên quan và hướng xử lý. Bao gồm file thiếu, loại media sai, mốc nguồn vượt giới hạn, voice dài hơn timeline, cue không hợp lệ, lớp ảnh thiếu nguồn và tổng thời lượng vượt giới hạn. Kiểm tra lại phía backend khi thực sự render.

Nghiệm thu: bảy test hồi quy đều đạt; bộ test cũ đạt; đo âm lượng MP4 thật; sửa phụ đề → dựng lại → tải lại vẫn giữ chỉnh sửa; template có logo dùng được giữa hai dự án. Bổ sung test template cũ thiếu tài nguyên và trường hợp nhập lại cùng một gói.

Mốc bàn giao đầu tiên: bản sửa lỗi dùng được với dữ liệu cũ, có hướng phục hồi dự án và tài liệu thay đổi.

**M3 — Autosave, redo và xử lý tác vụ ổn định**

1. Tách main.jsx thành API client, các hook quản lý project/jobs/preview và component panel. Thực hiện từng phần, giữ được các luồng kiểm thử qua mỗi bước; thêm định dạng và lint cho mã mới/chỉnh sửa.
2. Autosave sau khoảng 1–2 giây ngừng chỉnh, tối đa một lần lưu đang chạy cho mỗi dự án. Gắn mỗi request với project ID, revision nền và thế hệ chỉnh sửa; response cũ không được ghi đè chỉnh sửa mới. Khi đổi dự án hoặc bắt đầu job, flush bản cần lưu và xử lý lỗi rõ ràng.
3. Lưu bản nháp cục bộ gồm trạng thái chỉnh sửa và revision nền; phục hồi sau đóng tab/crash. Nếu server trả 409, giữ bản nháp, đưa lựa chọn tải bản mới hoặc xem khác biệt. Không tự thử ghi đè bản server.
4. Lịch sử undo/redo theo thao tác có ý nghĩa: nhập chữ, kéo trim, thay nguồn, chỉnh template, áp dụng AI. Gộp thay đổi liên tiếp; autosave không tạo một bước undo cho từng phím. Chỉnh mới sau undo tạo nhánh lịch sử mới và xóa redo tương ứng.
5. Job có trạng thái lưu bền: queued/running/cancelling/done/error/cancelled/interrupted. Khi mở lại ứng dụng, job dở được đánh dấu interrupted; người dùng chủ động chạy lại. Không tự lặp lời gọi AI/TTS đã có thể phát sinh phí.
6. Chuyển probe, thumbnail và thao tác file nặng khỏi event loop. Nhập file vào staging, validate rồi commit vào dự án; khóa theo dự án thay vì giữ khóa toàn cục trong suốt quá trình nhập. Giới hạn số file/dung lượng trong lúc nhận dữ liệu, dọn staging khi lỗi hoặc hủy.
7. Tiến độ phân biệt upload và xử lý file. Hủy queued job ngay; hủy FFmpeg bằng cơ chế dừng subprocess; giữa các bước AI phải kiểm tra yêu cầu hủy, ngừng retry/fallback tiếp theo. Với request đã gửi tới nhà cung cấp, hiển thị trạng thái đang hủy và không cam kết đảo ngược xử lý bên dịch vụ.

Nghiệm thu: gõ liên tục không mất ký tự khi save trả về; hai tab không ghi đè nhau; đổi dự án không lẫn response; phục hồi bản nháp đúng; undo/redo qua cả autosave; restart hiển thị đúng job dở; upload lớn không chặn health/job polling; hủy không lưu kết quả dở vào dự án.

**M4 — Timeline và kiểm tra trước khi xuất**

1. Tính waveform thật từ audio và cache dữ liệu peak để frontend đọc nhanh; timeline dùng cùng một trục thời gian cho clip, waveform, ruler và playhead.
2. Thêm split tại playhead, trim hai đầu, nhân đôi/xóa, zoom timeline và snapping vào ranh giới cảnh/phụ đề. Split giữ đúng source_start và speed; cấm đoạn dưới độ dài tối thiểu.
3. Thêm phím tắt cho play/pause, split, undo/redo và điều hướng cảnh; không kích hoạt phím tắt chỉnh timeline khi đang gõ trong input/textarea.
4. Khóa cảnh người dùng đã duyệt. Planner giữ nguyên các cảnh khóa và vị trí/thời lượng của chúng; chỉ lấp những vùng còn lại. Báo rõ khi thời lượng đích hoặc mẫu không thể đáp ứng các ràng buộc đã khóa.
5. Hiển thị preflight trong giao diện: lỗi chặn xuất, cảnh báo cho phép tiếp tục, nút chọn đúng cảnh/lớp cần chỉnh. Cảnh bị giữ khung cuối được tính theo cả nguồn, giới hạn scene và tốc độ, đồng nhất với renderer.
6. Đồng bộ scrub, chuyển clip và audio khi xem nhanh. Bản MP4 xem thử hiển thị revision đã render; nếu dự án đã đổi, báo bản xem thử cũ.

Nghiệm thu: cắt một clip đang chạy tốc độ khác 1 vẫn giữ đúng đoạn hình; tổng thời lượng không trôi quá một frame do thao tác cắt/ghép; dựng lại giữ nguyên cảnh khóa; lỗi preflight trên UI khớp lỗi backend. Kiểm tra thao tác trên trình duyệt ở màn hình desktop và chiều rộng nhỏ.

**M5 — Backup, quản lý dung lượng và thư viện mẫu**

1. Export ZIP từ snapshot nhất quán của dự án: project JSON, manifest có schema_version/hash và toàn bộ media cần thiết. Cho chọn kèm bản xuất/lịch sử; không kèm settings hoặc API key. Xử lý theo stream để tránh nạp toàn bộ archive lớn vào RAM.
2. Import ZIP vào staging; kiểm tra đường dẫn, số file, dung lượng giải nén và hash; từ chối đường dẫn thoát khỏi thư mục đích. Tạo project ID mới, ánh xạ ID tham chiếu, xác thực rồi commit. Nếu thất bại, dự án hiện có không bị ảnh hưởng.
3. Trang dung lượng phân loại nguồn, cache, bản xuất và lịch sử; xem trước dung lượng sẽ giải phóng. Dọn cache có thể tái tạo; giữ mọi asset còn được tham chiếu và dữ liệu job đang chạy. Xóa dự án theo cơ chế thùng rác trước khi xóa hẳn.
4. Thư viện template có thumbnail, đổi tên, nhân bản, xóa và kiểm tra thiếu tài nguyên. Khi áp dụng khác tỷ lệ khung, cho xem preview và chỉnh bố cục; không tự kéo giãn logo/chữ ngoài ý muốn.

Nghiệm thu: export → import vào thư mục dữ liệu mới → render vẫn đủ hình, logo, âm thanh và phụ đề; ID giữa hai dự án không va chạm; dọn cache rồi vẫn render lại được; ZIP lỗi hoặc chứa đường dẫn bất thường bị từ chối có kiểm soát.

**M6 — AI, hiệu năng và công cụ trình bày**

| ID | Phạm vi | Chi tiết thực hiện | Cách nghiệm thu |
|---|---|---|---|
| A1 | Duyệt đề xuất AI | Response schema chặt chẽ; hiển thị trước/sau bằng tên cảnh và thumbnail; chọn từng thay đổi; revision bắt buộc; preview/preflight trước commit | Đề xuất sai schema/ID không làm đổi dự án; chỉ áp dụng mục được chọn; undo phục hồi đúng |
| A2 | Theo dõi provider | Hiển thị API/model thực dùng, chuyển dự phòng, thời gian và số lần gọi; cache nhãn theo hash ảnh/model/prompt; timeout/retry hữu hạn theo lỗi | Chuyển API có thể quan sát; hủy ngừng thử tiếp; không ghi key hoặc header nhạy cảm vào log |
| P1 | Phân tích cảnh | Decode tuần tự ở độ phân giải thấp, giữ timestamp chính xác, tạo thumbnail theo batch, cache theo file và cấu hình | So cùng baseline; cảnh không vượt ranh giới đã phát hiện; test MOV xoay và video VFR |
| P2 | Preview/render | Proxy cho media nặng; cache clip trung gian theo nguồn, trim, tốc độ, crop, lớp, font và cấu hình; kiểm tra encoder phần cứng trước khi bật; có CPU fallback | Đổi một cảnh làm mất hiệu lực đúng phần cache; bản xuất đúng kích thước/thời lượng và không thiếu lớp |
| E1 | Chữ và phụ đề | Chọn font, preset phụ đề/vùng an toàn, preview gần với renderer; đóng gói hoặc ánh xạ font thiếu | Tiếng Việt đủ dấu; text wrap/cỡ chữ được kiểm tra bằng MP4 thật |
| E2 | Crossfade và âm thanh nguồn | Thời lượng project được định nghĩa trên một trục thời gian chung có transition overlap; nguồn có đủ handle hoặc báo thiếu; có mức âm thanh nguồn riêng | Preview, render, caption và voice cùng mốc; không cộng/trừ overlap khác nhau ở các thành phần |

P1/P2 chỉ được xem là cải tiến khi có số đo trên cùng máy và cùng bộ media. Mục tiêu thử nghiệm ban đầu cho phân tích video dài là giảm ít nhất 30% thời gian, với độ chính xác/tài nguyên không suy giảm đáng kể; mục tiêu này chưa phải cam kết. Nếu không đạt, giữ lại thay đổi có lợi đã đo được và điều chỉnh phạm vi dựa trên bottleneck thực tế.

Chỉ hiển thị chi phí AI ước tính khi có đơn giá hợp lệ do cấu hình cung cấp; nếu chưa có thì hiển thị số lần gọi/usage đã biết. Với E2, phải chốt phép tính overlap trước khi viết hiệu ứng vì nó tác động trực tiếp tới thời lượng, phụ đề và giữ trọn voice.

Kiểm tra dịch vụ AI thật khi có cấu hình được phép dùng, giới hạn số lần gọi và nội dung fixture. Kiểm thử mô phỏng tiếp tục là mặc định cho bộ test tự động.

**M7 — Nghiệm thu và phát hành**

1. Chạy unit/API test, kiểm thử component và các luồng E2E quan trọng trên trình duyệt. Bộ kiểm tra FFmpeg thật xác nhận hình/âm/thời lượng; lỗi đã có test hồi quy không được tái xuất hiện.
2. Kiểm tra hai luồng đầy đủ: tái biên tập và dựng theo mẫu; với media nhập sẵn, TTS/ASR phù hợp môi trường, phụ đề chỉnh tay, logo và cả bản xem thử/xuất cuối.
3. Nghiệm thu trên Windows mới: setup lần đầu, đường dẫn có khoảng trắng/tiếng Việt, mở lại app, cổng bị chiếm, thiếu file nguồn, thiếu dung lượng, ngắt job và phục hồi dữ liệu cũ.
4. Đóng gói frontend/dist cùng source/runtime instructions, khóa dependency, cập nhật README/VALIDATION và release notes. Hướng dẫn backup, restore và dữ liệu nào được nâng schema.
5. Bản phát hành chỉ được đánh dấu đạt khi mọi lỗi chặn mở/lưu/xuất trong phạm vi đều được giải quyết; hạn chế còn lại phải có tình huống cụ thể và cách xử lý.

**Kế hoạch chuyển đổi dữ liệu và quay lại bản trước**

- Migration dùng schema_version, chạy lặp không làm biến đổi thêm dữ liệu. Có fixture từ bản 1.0; xác thực trước/sau và giữ ID ổn định trừ khi import thành dự án mới.
- Trước lần ghi theo schema mới, tạo bản sao dữ liệu cần chuyển trong thời điểm ứng dụng không có job đang ghi. Với media bất biến có thể dùng manifest/hash và giữ nguyên file; phải bảo đảm bản rollback có đủ file được tham chiếu.
- Nếu migration thất bại, giữ dữ liệu gốc và báo rõ dự án nào cần phục hồi. Không coi việc bỏ qua trường mới hoặc cắt list là migration hợp lệ.
- Quay lại phiên bản cũ phải khôi phục cả source/build và dữ liệu trước migration. Những chỉnh sửa sau nâng cấp được xuất/giữ riêng để tránh ghi đè mất chúng; không mở dữ liệu schema mới trực tiếp bằng backend cũ.

**Cách chia thay đổi để dễ kiểm tra**

Mỗi nhóm lỗi hoặc tính năng có commit/nhóm thay đổi riêng, kèm test và thay đổi tài liệu tương ứng. Thứ tự nên là: validator và migration → phục hồi dự án/undo → settings và single-instance → audio/caption/template → refactor frontend → autosave/redo → jobs/upload → timeline/preflight → backup/storage → AI/performance/effects. Không gom refactor toàn bộ frontend vào cùng thay đổi sửa thuật toán render.

Mỗi đầu việc hoàn tất cần có: hành vi trước/sau rõ ràng, test cho tình huống người dùng thực hiện được, kiểm tra dữ liệu cũ, và bằng chứng nghiệm thu phù hợp. Các mốc đầu giúp phát hành giá trị sử dụng sớm, trong khi các tính năng nặng như crossfade và render phần cứng có thể bàn giao ở các đợt tiếp theo.
