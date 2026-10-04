# Review M0–M6 và kế hoạch sửa lỗi còn sót

Ngày review: 03/10/2026. Phạm vi: thay đổi chưa commit của ClipForge so với HEAD và kế hoạch `PLAN_RELIABILITY_1_2_3.md`.

## Kết luận

Cập nhật triển khai 04/10/2026: [R5](R5.md) đã đóng F8 bằng regression; [R6](R6.md) ghi nghiệm thu tổng hợp, gói ZIP và các phần hoãn có chủ đích. F1–F9 không còn known red trên source hiện tại. Nội dung review và số liệu các đợt cũ phía dưới được giữ như bằng chứng lịch sử; kết quả hiện tại được chốt riêng ở R5_BASELINE/R6_BASELINE. Không tuyên bố hoàn tất toàn bộ M0–M6.

Chưa nên nghiệm thu đầy đủ M0–M6. Nền tảng snapshot, manifest render, provider intent, scheduler, Task Center và diagnostic ZIP đã có, nhưng còn lỗi trong các luồng phục hồi thực tế. Review xác nhận 8 vấn đề đầu bằng thực thi cô lập hoặc kiểm tra dependency; vấn đề thứ 9 được xác định qua điều kiện hiển thị UI và chính sách pin backend. Không sửa backend/frontend trong lượt review này.

Mức ưu tiên: P1 cần sửa trước bàn giao vì có thể mất kết quả, mất dữ liệu phục hồi hoặc để tiến trình cũ tiếp tục ghi; P2 cần sửa để hành vi đáp ứng đúng hợp đồng phục hồi và kiểm soát tài nguyên.

## Phát hiện đã xác nhận

### F1 — P1: Startup xóa staging của upload đang được giữ để phục hồi

- Vị trí: `backend/jobs.py:1005–1021`, đối chiếu layout được tạo tại `backend/main.py:783`.
- `pinned` chứa job ID, nhưng `staging.iterdir()` trả thư mục project ID. Điều kiện `path.name in pinned` không giữ được thư mục `staging/<pid>/<jid>` thực tế.
- Tái hiện: tạo upload có file nhận đủ và checkpoint, chạy `initialize()` rồi `cleanup_staging()`; job vẫn có `resume_capability=checkpointed` nhưng file đã mất.
- Ảnh hưởng: “Tiếp tục” sau restart không thể xử lý file đã tải xong; người dùng phải nhập lại. Có thể xóa toàn bộ staging dưới cùng project.
- Sửa: duyệt đúng hai cấp, xác định tập đường dẫn staging được pin theo `(pid, jid)` và trạng thái; chỉ xóa thư mục job không được tham chiếu, rồi dọn thư mục project rỗng. Xác minh resolved path, không đi theo symlink/junction ngoài vùng staging.

### F2 — P1: Hủy job lỗi có thể xóa bản xuất đã commit

- Vị trí: `backend/jobs.py:1165–1185`, liên quan `Job.commit()` tại dòng 391–426.
- Sau khi `store.save()` đã ghi export và commit token, lỗi ghi receipt khiến job thành `error`, `_commit_done` vẫn false. Nhánh hủy job lỗi xóa thư mục export trước khi kiểm tra trạng thái commit bền.
- Tái hiện: fault `ENOSPC` tại `write_receipt`; project đã có export/token. Bấm hủy làm mất `video.mp4` nhưng metadata export vẫn nằm trong project.
- Sửa: đối chiếu intent/token/receipt trước mọi resume/retry/cancel/cleanup, không chỉ lúc startup. Phân biệt “kết quả đã commit, metadata job đang cần phục hồi” với lỗi thao tác chưa commit. Không xóa artifact đã được project công bố dù status job đang error. Xử lý một lần dưới khóa và ghi trạng thái phục hồi bền.
- Phạm vi kiểm tra thêm: lỗi sau project write nhưng trước cập nhật history/redo; callback upload đang xóa file đã di chuyển khi save ném lỗi. Đây là ranh giới cần thêm fault test, chưa được tính là phát hiện tái hiện riêng trong báo cáo này.

### F3 — P1: FFmpeg sống tiếp khi backend bị kết thúc đột ngột

- Vị trí: `backend/media.py:110–122`.
- `CREATE_NEW_PROCESS_GROUP` và `taskkill` trong nhánh exception chỉ xử lý khi Python còn chạy. Không có cơ chế ràng buộc vòng đời tiến trình con với backend khi backend bị kill/crash.
- Tái hiện trên Windows: chạy FFmpeg nguồn tổng hợp ra null output trong subprocess Python riêng, kill Python, kiểm tra process handle; FFmpeg vẫn chạy. Tiến trình thử đã được dừng bằng đúng PID do script tạo.
- Ảnh hưởng: tốn tài nguyên sau crash; restart/resume có thể tạo worker mới cùng ghi artifact với tiến trình cũ.
- Sửa: cơ chế sở hữu cây tiến trình bằng Windows Job Object với kill-on-close hoặc supervisor tương đương, bảo đảm không có khoảng trống giữa tạo và gán ownership. Kiểm tra lỗi gán ownership, nested-job constraints và shutdown có timeout. Startup phải không coi artifact đang bị owner cũ ghi là checkpoint hoàn chỉnh. Không quét/kill FFmpeg theo tên hoặc PID không xác thực.

### F4 — P2: Job chưa chạy bị mất khả năng chạy tiếp sau restart

- Vị trí: `backend/jobs.py:968–973`, `1247–1248`.
- Job queued có snapshot/payload đầy đủ nhưng chưa có checkpoint. Startup đổi thành interrupted; resume yêu cầu checkpointed nên từ chối.
- Tái hiện: reserve + launch render khi queue tạm dừng, initialize, resume → “Job này chưa có checkpoint hợp lệ; hãy tạo tác vụ mới”.
- Sửa: phân biệt khả năng “bắt đầu từ snapshot đã lưu” và “tiếp tục checkpoint”. Job chưa chạy có handler/payload/snapshot hợp lệ được người dùng đưa lại vào queue mà không tạo ID mới. Vẫn không tự chạy sau restart. Legacy hoặc assistant thiếu dữ liệu phải có hướng dẫn riêng.

### F5 — P2: UI bỏ qua kết quả hoàn thành sau khi resume

- Vị trí: `frontend/src/App.jsx:303–309`, và lúc mở project dòng 192.
- `jobSeen` chỉ lưu job ID. Sau khi đã nhìn thấy error/interrupted ở attempt 1, trạng thái done của attempt 2 cùng ID bị bỏ qua.
- Tái hiện bằng thực thi callback effect trích trực tiếp từ source: `error#1 → queued#2 → running#2 → done#2`; số lần tải lại project bằng 0.
- Ảnh hưởng: render/TTS/ASR đã phục hồi thành công nhưng editor vẫn giữ dữ liệu cũ, không tự mở preview mới; upload có thể không chạy bước auto-assemble.
- Sửa: khóa sự kiện theo `(job_id, attempt, terminal_status)` hoặc event ID bền. Không dùng mọi state_version làm event vì progress cũng tăng version. Khi done tới lúc có draft chưa lưu, giữ một sự kiện chờ đồng bộ metadata an toàn; không đánh dấu đã xử lý rồi bỏ mất. Preview chọn theo export ID của job thay vì `exports.at(-1)` nếu nhiều job hoàn thành.

### F6 — P2: Resume dự án đã đổi vẫn gọi provider rồi mới báo xung đột

- Vị trí: `backend/main.py:166–215`, `backend/jobs.py:1234–1283`.
- Resume kiểm tra snapshot/config nhưng không đối chiếu revision hiện tại trước thực thi. Kiểm tra revision chỉ diễn ra lúc commit.
- Tái hiện: job voice lỗi có checkpoint, sửa script hiện tại, resume; synthesize mock được gọi một lần với script cũ, cuối cùng mới lỗi `REVISION_CONFLICT`.
- Ảnh hưởng: có thể tốn phí TTS/ASR/AI cho kết quả không thể áp dụng; giữ slot/RAM vô ích.
- Sửa: precheck trước enqueue và kiểm tra lại ngay sau cấp lease, trước mọi side effect. Vẫn kiểm tra ngay trước commit để chặn race. Ngoại lệ render snapshot được xử lý riêng. Khi xung đột chuyển blocked có hành động “Tạo tác vụ từ bản hiện tại”; giữ checkpoint cũ.
- Mở rộng cùng đợt: fingerprint phải nhận diện endpoint, phiên bản xử lý và cấu hình thực dùng; queued job cần phát hiện config đổi trước lần chạy đầu, không chỉ khi resume. Không đưa secret vào fingerprint public hoặc hồ sơ job.

### F7 — P2: Shortcut mux hoàn thành dùng lại kết quả cũ khi dependency thay đổi

- Vị trí: `backend/render.py:1096–1118`.
- `_checkpoint_valid()` nhận chính fingerprint đang lưu làm giá trị mong đợi. Nhánh return sớm bỏ qua tính lại dependency nguồn/font/renderer mà các bước phía dưới thực hiện.
- Tái hiện với MP4 thật: render hoàn thành trước commit, thay file nguồn cùng đường dẫn và thay `RENDER_VERSION`, gọi lại render. Output giữ nguyên hash, không có lệnh FFmpeg mới, metadata vẫn ghi renderer cũ.
- Sửa: lưu và đối chiếu fingerprint toàn bộ dependency cần cho final output trước return sớm, gồm media content, font, render version, encode/audio config. Thay đổi dependency phải invalidation đúng bước hoặc chặn resume nếu snapshot media không còn khớp. Không chỉ đối chiếu duration hoặc hash output. Kiểm tra lại kết quả dùng trong resume plan để không báo tái sử dụng sai.

### F8 — P2: Bản cài theo requirements không có kiểm soát RAM

- Vị trí: `backend/jobs.py:461–474`; `requirements.txt`, `requirements-lock.txt`.
- RAM/CPU dùng import `psutil`, nhưng không file requirements nào khai báo thư viện này. Khi thiếu, code trả None và scheduler bỏ qua giới hạn RAM.
- Xác nhận trong môi trường hiện tại: `psutil_installed=false`, `available_memory_bytes=null`; dependency không được khai báo.
- Sửa: khai báo dependency và khóa phiên bản đã kiểm tra hoặc dùng implementation hệ điều hành không cần thư viện. Setup sạch phải có số liệu RAM và thực sự trì hoãn job khi thiếu RAM. Mất telemetry phải có trạng thái rõ, không âm thầm coi tài nguyên là đủ.

### F9 — P2: Không có nút bỏ job lỗi/gián đoạn để giải phóng dữ liệu pin

- Vị trí: `frontend/src/components/TaskCenter.jsx:575–584`; cả nút hủy ở danh sách cũng chỉ hiện cho `ACTIVE`.
- Backend hỗ trợ hủy `error`, `interrupted`, `blocked` và chặn dọn cache/thùng rác khi các job này giữ checkpoint. Nhưng UI chỉ cho hủy queued/running/cancelling.
- Khi job lỗi không thể resume (ví dụ cấu hình đã đổi) hoặc người dùng muốn bỏ kết quả dở, họ không có thao tác UI để giải phóng pin; Dung lượng tiếp tục báo bị chặn dù không có job đang chạy. Phát hiện này dựa trên mã, chưa thao tác browser E2E riêng.
- Sửa: thêm “Bỏ tác vụ và dữ liệu phục hồi” cho các trạng thái được phép; hiển thị phần dữ liệu dự kiến bỏ. Backend phải reconcile commit theo F2 trước khi xóa. Sau thao tác tải lại hàng đợi và thống kê dung lượng.
- Nghiệm thu: tạo job lỗi có pin → Dung lượng bị chặn → bỏ job trên UI → dọn cache được; export đã commit và source gốc không bị xóa.

## Bằng chứng và cách tái hiện

Các file trong `review/reliability-review/`:

- `reproduce_findings.py`: xác nhận F1, F2, F4, F6, F7, F8; output `results.json`.
- `reproduce_orphan.py`: xác nhận F3 bằng tiến trình riêng và FFmpeg null output; output `orphan-result.json`.
- `reproduce_ui.cjs`: thực thi effect thật từ App.jsx để xác nhận F5; output `ui-result.json`.

Các script Python dùng `CLIPFORGE_DATA` ở thư mục TEMP riêng ngay trước import backend, không sửa project thật, không gọi provider thật. Assertion hiện tại xác nhận lỗi còn tồn tại; khi sửa cần chuyển thành regression test kỳ vọng hành vi đúng, không giữ assertion ngược trong suite chính.

## Khoảng trống so với kế hoạch, tách biệt khỏi 9 lỗi trên

1. Scheduler cấp tài nguyên theo toàn job, không theo ranh giới bước; chưa có pause từng job tại checkpoint. Một render giữ cả CPU/GPU token qua các công đoạn. Cần triển khai hoặc ghi rõ phạm vi đã hoãn, không gọi là hoàn thành đầy đủ thiết kế M4.
2. Chưa thấy kiểm tra dung lượng định kỳ trong encode, giới hạn thread FFmpeg, tiến độ FFmpeg thực và ETA. Tiến độ vẫn chủ yếu tăng theo mốc hàm.
3. Các lần gọi `job_error()` trong worker chưa truyền step_id, nên chi tiết lỗi thường không chỉ ra bước lỗi; diagnostic ID không có kho sự kiện tương ứng để tra cứu nguyên nhân nội bộ. Báo cáo whitelist tốt cho riêng tư nhưng chưa đủ để điều tra lỗi.
4. Chưa có frontend test cho TaskCenter/useJobs/resume trong 24 test hiện có; browser smoke theo tài liệu chỉ bao phủ một phần. Test tên “concurrent duplicate resume” gọi tuần tự, chưa kiểm chứng race hai request thật.
5. Bằng chứng M6 tự ghi rõ chưa nghiệm thu Windows VM sạch, thiếu RAM/volume đầy, provider thật hoặc voice tự nhiên. Các giới hạn đó cần tiếp tục giữ trong release notes.
6. Cần kiểm tra ranh giới commit/history/undo và shutdown request mạng quá 8 giây; không coi việc có receipt là đã bao phủ toàn bộ crash window.

## Kế hoạch sửa theo đợt

### R0 — Chuẩn bị regression và baseline (0,5–1 ngày công)

Trạng thái 03/10/2026: **R0 đã triển khai**, xem [R0.md](R0.md) và [R0_BASELINE.json](R0_BASELINE.json). Full baseline: 163 backend passed + 12 known red; 25 frontend passed + 9 known red; lint/build đạt. Tại thời điểm chốt R0, F1–F9 còn mở; trạng thái sau sửa ghi tại từng đợt R1–R5 bên dưới.

- Chụp trạng thái source hiện tại; chỉ tạo dữ liệu thử cô lập. Không reset các thay đổi người dùng đang có.
- Chuyển các tái hiện F1–F8 thành regression tests với đầu ra mong muốn và thêm component/browser test cho F9, đánh dấu red trước khi sửa.
- Thêm fixture dừng scheduler/join worker và khôi phục global state sau mỗi test; tránh thread sống sang test khác dùng ROOT mới.
- Ghi log test/build vào artifact cố định; kết quả không chỉ lưu trong session terminal.
- Đầu ra: checklist F1–F9, fixtures, baseline thực thi và phạm vi phát hành đã chốt.

### R1 — Bảo toàn commit và dữ liệu phục hồi (2–3 ngày công)

Phạm vi F1 + F2; ưu tiên cao nhất.

Trạng thái 03/10/2026: **R1 đã triển khai**, F1/F2 chuyển sang regression green. Xem [R1.md](R1.md) và [R1_BASELINE.json](R1_BASELINE.json) cho fault matrix, bằng chứng và giới hạn. F3–F9 tiếp tục thuộc R2–R5.

1. Sửa traversal staging và pin artifact theo đường dẫn thực. Giữ cả upload đã nhận đủ chưa probe và upload lỗi giữa nhiều file.
2. Tách helper `reconcile_commit(job)` dùng chung cho startup, resume, cancel và cleanup. Đọc intent/receipt/project token, trả rõ committed/uncommitted/unknown; không xóa khi trạng thái chưa xác định.
3. Cho commit đã thành công nhưng receipt/log thất bại chuyển sang trạng thái chờ hoàn thiện bookkeeping. Retry chỉ hoàn tất receipt/history, không chạy lại callback/provider.
4. Khi rollback upload, chỉ hoàn trả/xóa file chắc chắn chưa được project công bố; nếu project đã lưu phải giữ file. File di chuyển dở nên có journal để phục hồi, không xóa mất nguồn đã tải đủ.
5. Kiểm tra thứ tự khóa; không giữ global lock lúc hash, dọn thư mục lớn hoặc tạo ZIP.

Nghiệm thu: restart giữ file upload pin; file rác vẫn dọn được; fault trước/sau project write/receipt/history không mất file; cancel sau commit không xóa export; undo không gây recovery áp dụng lại; lỗi ghi đĩa không làm mất lỗi gốc hoặc kẹt lease.

### R2 — Quản lý tiến trình và quyền chạy sau restart (1,5–2,5 ngày công)

Phạm vi F3 + F4.

Trạng thái 03/10/2026: **R2 đã triển khai**, F3/F4 chuyển sang regression green. Xem [R2.md](R2.md) và [R2_BASELINE.json](R2_BASELINE.json) cho ownership Windows, shutdown, queue/start và bằng chứng. F5–F9 tiếp tục thuộc R3–R5.

1. Bổ sung owner process-tree trên Windows; hỗ trợ graceful cancel và kill-on-owner-exit; đóng handle đúng cả đường lỗi tạo process.
2. Bảo đảm stop backend không thả instance ownership khi worker còn có thể ghi project. Request provider đang bay cần chuyển sang kết quả chưa rõ hoặc hoàn tất an toàn trước kết thúc theo chính sách.
3. Lưu khả năng bắt đầu lại từ snapshot cho job queued, không phụ thuộc đã có checkpoint; UI phân biệt “Bắt đầu tác vụ đã lưu” với “Tiếp tục từ checkpoint”.
4. Recovery chỉ chuyển về trạng thái chờ hành động, không tự gọi AI hoặc encode.

Nghiệm thu: kill backend thật giữa encode không còn FFmpeg con; mở lại chỉ có một writer; hàng 10 job gồm chưa chạy/đang chạy đều có đường xử lý rõ; assistant thiếu prompt không có nút resume gây hiểu nhầm; hai tab cùng start/resume chỉ tạo một attempt/lease.

### R3 — Xác thực đầu vào và tái sử dụng kết quả (1,5–2,5 ngày công)

Phạm vi F6 + F7.

Trạng thái 03/10/2026: **R3 đã triển khai**, F6/F7 chuyển sang regression green. Xem [R3.md](R3.md) và [R3_BASELINE.json](R3_BASELINE.json) cho revision/config guards, dependency/cache validation và bằng chứng. F5/F9 thuộc R4, F8 thuộc R5 còn mở.

1. Precheck revision cho job sửa project trước enqueue và trước chạy; commit vẫn dùng compare-and-swap.
2. Snapshot cấu hình hiệu lực hoặc chặn job khi config khác lúc submit; hash endpoint không chứa key, ghi operation/render/analysis version rõ ràng.
3. Fingerprint final output phải được tính từ dependency hiện tại đã xác thực, không dùng fingerprint lưu làm expected value của chính nó.
4. Validate cache project-result/ASR/analysis với media hash và schema tương ứng; không chỉ kiểm tra JSON đọc được.
5. Reconcile commit trước chạy lại để tránh chạy pipeline của job đã lưu kết quả.

Nghiệm thu: script/revision đã đổi thì số lời gọi provider bằng 0; source/font/render version đổi không dùng final cũ; key đổi nhưng endpoint/model không đổi không vô hiệu kết quả tính toán vô lý; checkpoint không bị ảnh hưởng vẫn được tái sử dụng; MP4 đúng clock hình/âm và crossfade như suite trước.

### R4 — Đồng bộ giao diện theo từng lần chạy (1–1,5 ngày công)

Phạm vi F5, F9 và race UI liên quan.

Trạng thái 03/10/2026: **R4 đã triển khai**, F5/F9 chuyển sang regression green. Full baseline: 347 backend passed + 2 xfailed F8; 61 frontend passed, không TODO; 9 E2E Chrome passed, lint/build đạt. Xem [R4.md](R4.md) và [R4_BASELINE.json](R4_BASELINE.json). Trong F1–F9 chỉ còn F8 thuộc R5; cổng phát hành rộng hơn thuộc R6 vẫn chưa nghiệm thu.

1. Dùng terminal event key theo job/attempt/status trong jobSeen; xử lý đúng cả dữ liệu cũ khi mở project.
2. Khi done, tải lại project nếu không có draft; nếu có draft, giữ pending event để hòa giải metadata và revision mà không làm mất nội dung đang gõ.
3. Mở preview theo job export ID; auto-assemble chỉ chạy một lần cho upload thành công tương ứng, kể cả sau reconnect.
4. Chặn response cũ của chi tiết job/refresh khi đổi selectedId hoặc đóng TaskCenter. Không hiện connected chỉ vì một request cũ vừa trả về.
5. Bổ sung hook/component tests và browser E2E thật cho resume/error/done, hai tab, mất backend, đổi project và dirty draft.
6. Bổ sung thao tác bỏ job lỗi/gián đoạn/bị chặn để giải phóng pin, dùng API đã có sau khi đóng F2; xác minh storage phản ánh kết quả ngay.

Nghiệm thu: attempt 2 done cập nhật editor đúng một lần; lỗi attempt 1 không che kết quả attempt 2; không lẫn project; draft giữ nguyên; preview mới thuộc đúng job; phản hồi cũ không ghi đè chi tiết job mới.

### R5 — Tài nguyên và chẩn đoán đúng cam kết (2–3 ngày công)

Phạm vi F8 và các phần M4/M5 còn thiếu.

1. Khai báo/khóa dependency telemetry; kiểm tra setup sạch và hành vi thiếu telemetry.
2. Kiểm tra tài nguyên trước từng bước nặng; theo dõi đúng ổ data/temp và reservation riêng từng volume, tránh trừ cùng một tổng cho mọi ổ. Điều tiết có hysteresis.
3. Bổ sung kiểm tra đĩa định kỳ trong encode, dừng an toàn khi vượt ngưỡng; giữ checkpoint trước đó và chặn cấp công việc cùng volume.
4. Chuyển lease theo bước hoặc ghi rõ giới hạn còn lại nếu cần chia release. CPU fallback phải xin/cập nhật token phù hợp.
5. Thu FFmpeg progress với kênh được drain liên tục, stderr giới hạn; không làm deadlock. ETA có nhãn ước lượng, không đủ dữ liệu thì không đưa số.
6. Gắn current_step vào worker/error; lưu sự kiện allowlist có rotation và diagnostic ID liên kết thật. Không lưu prompt, media, header, secret hay raw provider body. Retry budget và unknown outcome được kiểm thử riêng.

Nghiệm thu: thiếu RAM không cấp job; thiếu đĩa giữa bước không hỏng project; lease trả lại sau mọi lỗi; lỗi UI chỉ đúng bước và hành động; diagnostics có dữ liệu điều tra hữu ích và vượt kiểm thử sentinel secret; đo p95 API khi công việc thật đang chạy, không chỉ queue đang chờ.

### R6 — Nghiệm thu tổng hợp và phát hành (1,5–2,5 ngày công)

- Chạy toàn bộ backend suite; frontend test/lint/build; render thật cold/warm/resume cùng FFmpeg và cùng media để so sánh.
- Fault matrix: trước/sau artifact rename, manifest persist, project write, history update, receipt write, done persist. Kết hợp restart và thao tác cancel/resume từ API.
- Race bằng barrier/two requests đồng thời: resume/resume, cancel/commit, autosave/render commit, cleanup/startup; không thay bằng hai lời gọi tuần tự.
- Windows: cài từ ZIP vào venv mới, VM sạch nếu có; thử kill thật, hết đĩa trên volume thử giới hạn và mock telemetry RAM. Không lấp ổ người dùng.
- Cập nhật README/M6/release notes và rollback; chỉ ghi “đạt” với bằng chứng thực thi. Nếu chưa có VM/provider thật, giữ giới hạn nghiệm thu hiện có.

Điều kiện phát hành: F1–F9 đóng bằng regression test; không còn lỗi P1; mọi phần thiếu so với M0–M6 được hoàn thiện hoặc ghi rõ là hoãn có chủ đích. Không tuyên bố hoàn thành toàn bộ plan nếu chỉ đóng các bug mà chưa hoàn thiện phạm vi M4/M5.

## Ước lượng và thứ tự

Tổng dự kiến 10–16,5 ngày công cho một người, bao gồm hoàn thiện các khoảng trống M4/M5 và nghiệm thu. Đây là ước lượng để lập kế hoạch; hiệu chỉnh sau R0. Phần chỉ sửa F1–F9 có thể ngắn hơn, nhưng không đủ để gọi toàn bộ M0–M6 hoàn tất.

Thứ tự: R0 → R1 → R2 → R3 → R4 → R5 → R6. Có thể xây fixture UI/tài nguyên sớm, nhưng không nghiệm thu trước nền commit/recovery. Mốc bàn giao sửa lỗi đầu tiên là sau R1–R2: dữ liệu phục hồi được giữ, commit không bị hủy mất kết quả, tiến trình cũ không ghi tiếp và job queued có đường khôi phục.

## Kiểm thử đã chạy trong review

- Frontend: 24/24 test đạt; lint đạt trên 20 file; production build đạt, xuất vào TEMP để không ghi đè bản dist đang dùng.
- Backend targeted ban đầu: 13 passed, 1 failed. Failure tại test hàng 105 job là `PermissionError [WinError 5]` ở `store.atomic()`/`os.replace`; chạy lại riêng test đó đạt (1 passed, 3,20 giây). Chưa đủ bằng chứng để quy kết nguyên nhân hoặc coi là lỗi logic độc lập.
- Tái hiện cô lập: F1–F8 đã xác nhận theo các artifact phía trên; không phát request provider thật.
- Full backend chạy lại: **157 passed, 1 warning trong 408,87 giây**; log `review/reliability-review/backend-tests.txt`. Cảnh báo deprecation Starlette/httpx. Build mới đạt; log `review/reliability-review/frontend-build.txt`.
- Suite hiện có đạt không phủ định F1–F9: các đường lỗi đã tái hiện chưa được suite này bao phủ đúng, vì vậy cần bổ sung regression theo R0–R6 trước nghiệm thu lại.
