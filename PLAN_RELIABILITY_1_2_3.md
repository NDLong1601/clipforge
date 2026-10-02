# Kế hoạch nâng cấp độ ổn định ClipForge — mục 1, 2, 3

Ngày lập: 02/10/2026. Trạng thái: đề xuất triển khai, chưa thay đổi runtime.

## 1. Kết quả cần đạt

1. Tiếp tục tác vụ gián đoạn từ bước chưa hoàn thành, tái sử dụng kết quả đã kiểm chứng.
2. Xếp hàng tác vụ nhiều dự án, điều tiết công việc nặng theo tài nguyên máy và dung lượng ổ đĩa.
3. Hiển thị lỗi có nguyên nhân, hành động khắc phục và báo cáo chẩn đoán đã loại dữ liệu nhạy cảm.

Giữ mô hình ứng dụng Windows cục bộ, một người dùng. Giai đoạn này dùng kho JSON ghi nguyên tử hiện có; chưa cần triển khai Redis, dịch vụ hàng đợi ngoài hay cơ sở dữ liệu mới. Khi quy mô job tăng, đo chi phí đọc/lọc trước khi quyết định đổi kho lưu trữ.

## 2. Hiện trạng đã xác minh từ mã nguồn

| Thành phần | Hiện có | Khoảng trống cần xử lý |
|---|---|---|
| `backend/jobs.py` | JSON lưu job; executor 3 worker; khóa dự án; hủy; khôi phục trạng thái interrupted | Callable/closure không được lưu; thiếu payload, snapshot, checkpoint, bộ điều phối tài nguyên và sổ commit bền |
| `backend/main.py` | Các endpoint phân tích, dựng, TTS, ASR, nhập tư liệu; helper `task()` | Đọc dự án lúc worker bắt đầu; tham số thao tác nằm trong closure; startup dọn staging trước khi đọc job |
| `backend/render.py` | Cache từng clip; CPU fallback; kiểm tra đồng hồ hình/âm thanh | Thiếu manifest để tiếp tục từng công đoạn; dọn file trung gian trước khi kết quả được lưu vào dự án |
| `backend/providers.py` | Retry hữu hạn, fallback, cache nhãn cảnh, metadata lời gọi | Chưa có nhật ký yêu cầu bền cho TTS/ASR và tình huống provider đã xử lý nhưng máy chưa lưu kết quả |
| `backend/store.py` | Ghi nguyên tử, revision, history | `operation_id` đang phục vụ gộp history, không phải cơ chế chống commit trùng; `_commit_done` của job chỉ ở RAM |
| `backend/portable.py` | Dọn cache, backup, thùng rác | Cần nhận diện dữ liệu còn được job gián đoạn/hàng đợi tham chiếu |
| `frontend/src/hooks/useJobs.js` | Polling 1 giây, giữ trạng thái cũ khi lỗi | Chưa trả trạng thái mất kết nối, lần đồng bộ cuối; `active` chỉ lấy một job |
| `frontend/src/api/client.js`, `App.jsx` | Error message/toast, thanh tiến độ và hủy | Thiếu lỗi có cấu trúc, lịch sử lỗi, hành động khắc phục và trung tâm hàng đợi |

Các nhận định trên dựa vào việc đọc mã; không phải kết quả benchmark hay kiểm thử mới.

## 3. Quy tắc thiết kế phải giữ

- Mỗi job được gắn snapshot đầu vào và phiên bản thao tác. Khởi động lại không lấy âm thầm nội dung mới để chạy tác vụ cũ.
- Tiếp tục từ ranh giới bước đã hoàn tất. FFmpeg bị ngắt giữa một clip sẽ dựng lại clip đó; chưa hỗ trợ tiếp tục giữa frame hoặc tiếp tục byte upload đang dở.
- Kết quả chỉ được sử dụng sau khi validate và ghi nguyên tử. File tồn tại hoặc có kích thước khác 0 chưa đủ để công nhận checkpoint.
- Đổi nguồn/cấu hình làm vô hiệu đúng bước phụ thuộc và các bước phía sau; không chỉ kiểm tra tên file.
- Sau khi khởi động lại, chờ người dùng chọn tiếp tục. Không tự phát lại yêu cầu AI/TTS có thể tính phí.
- Không tự ghi đè chỉnh sửa mới của dự án; không công bố MP4 chưa kiểm tra; không thêm trùng asset/export khi phục hồi.
- Không giữ khóa toàn cục trong lúc chạy FFmpeg, gọi mạng, hash file lớn hoặc sao chép media. Quy định thứ tự lấy khóa và kiểm thử deadlock.
- Hủy và tiếp tục là thao tác có phiên bản; request lặp hoặc từ hai tab không tạo hai lần chạy song song.

## 4. Mục 1 — Phục hồi và tiếp tục tác vụ

### 4.1. Hồ sơ job và kho kết quả

Bổ sung schema riêng cho job: `schema_version`, `kind`, `operation_version`, `payload`, `input_revision`, `input_fingerprint`, `snapshot_ref`, `attempt`, `state_version`, `steps`, `blocked_reason`, `error`, `commit_token`, `parent_job_id`, thời điểm và các số liệu.

Lưu cấu hình có ảnh hưởng kết quả: preview/final, kích thước, model, giọng, prompt version, cấu hình encode và tham số phân tích. Chỉ lưu tham chiếu profile và dấu nhận diện cấu hình; không sao chép API key vào job/snapshot/log. Khóa API luôn lấy từ kho cài đặt lúc thực thi. Nếu profile/model đổi thì bước AI liên quan cần đánh giá lại trước khi chạy.

Thư mục đề xuất: `data/jobs/<id>.json` cho trạng thái; `data/job_runs/<id>/` cho snapshot, checkpoint manifest và kết quả trung gian. Manifest dùng đường dẫn tương đối được kiểm tra nằm trong vùng cho phép. Media lớn tham chiếu bằng asset ID/hash và được bảo vệ khỏi xóa; không nhân bản toàn bộ media mỗi lần xếp hàng.

Thay callback closure bằng registry `kind -> handler` và payload có schema. Migrate toàn bộ điểm tạo job để cùng đi qua scheduler; loại chưa hỗ trợ resume phải trả rõ khả năng, không hiển thị nút tiếp tục giả.

### 4.2. Chia bước có thể phục hồi

| Luồng | Ranh giới checkpoint | Đơn vị chạy lại |
|---|---|---|
| Preview/xuất video | Snapshot + preflight → proxy → từng clip → ghép hình → âm thanh nguồn → phụ đề/âm thanh trộn → encode → mux → kiểm tra → commit | Công đoạn hỏng và các công đoạn phụ thuộc |
| Phân tích tư liệu | Metadata → phân cảnh từng nguồn → thumbnail → từng batch nhãn AI → commit | Nguồn hoặc batch còn thiếu |
| Dựng từ audio | Chuẩn hóa audio → ASR → phân tích nguồn → nhãn AI → lập timeline → kiểm tra → commit | Bước thiếu; giữ bản ASR hợp lệ |
| Tạo giọng | Từng câu TTS → chuẩn hóa/đo thời lượng → ghép voice → cues → kiểm tra → commit | Câu chưa có kết quả hợp lệ |
| Nhập tư liệu | Nhận đủ file → manifest upload → probe/thumbnail từng file → commit | File đã nhận đủ nhưng chưa xử lý; upload chưa nhận đủ yêu cầu chọn lại file |
| Plan/template/lọc mặt/trợ lý | Snapshot → các bước cục bộ/AI tương ứng → validate → commit hoặc trả proposal | Triển khai adapter theo cùng hợp đồng; proposal và token áp dụng AI vẫn tuân thủ revision/expiry hiện có |

Mỗi checkpoint ghi: ID bước, trạng thái, dấu đầu vào, phiên bản thuật toán, đường dẫn kết quả, hash, thông số kiểm tra, thời gian. Hash media được tính/cache lúc nhập hoặc xác thực nền, tránh hash toàn bộ trên mỗi lần polling.

### 4.3. Resume và chống ghi kết quả trùng

1. Người dùng mở chi tiết job; hệ thống trả kế hoạch tiếp tục: bước tái sử dụng, bước chạy lại, tài nguyên thiếu và lời gọi AI có thể phát sinh.
2. Server kiểm tra lại kế hoạch khi nhận lệnh thực thi, đối chiếu `state_version`, snapshot, file và phiên bản xử lý; phản hồi cũ không được dùng để vượt kiểm tra mới.
3. Job tăng `attempt` nhưng giữ định danh logic và lịch sử lỗi. Cùng một job chỉ có một lease thực thi.
4. Với job sửa nội dung: đối chiếu revision khi bắt đầu và ngay trước commit. Nếu dự án đã đổi, giữ kết quả cũ và chuyển `blocked`, cho tạo job mới từ bản hiện tại; không tự merge timeline/cues.
5. Với render: dựng snapshot cũ vẫn hợp lệ. Khi hoàn thành, chỉ thêm metadata export vào bản dự án mới nhất dưới khóa ngắn; tuyệt đối không lưu toàn bộ snapshot cũ đè dự án đang chỉnh.
6. Ghi commit intent trước thay đổi; lưu `commit_token` cùng dữ liệu dự án trong một lần thay thế nguyên tử. Sau đó ghi receipt/job done. Startup đối chiếu token với intent/receipt để biết thay đổi đã thực hiện hay chưa. Hoàn thiện bước history/redo còn dở theo journal.
7. Trường hợp chết sau khi dự án được ghi nhưng trước `job=done` phải chỉ hoàn tất receipt, không chạy lại AI, tăng revision hoặc thêm export lần nữa. Receipt cần tồn tại độc lập với undo/redo để undo của người dùng không khiến recovery áp dụng lại kết quả.

Tách `source_revision`/dấu nội dung được render khỏi revision do thêm metadata export. Cập nhật cách xác định “Bản xem thử cũ” ở UI; giữ fallback cho export cũ chỉ có `project_revision`. Nghiệm thu cả cảnh thêm một export khác nhưng nội dung biên tập không đổi.

### 4.4. Yêu cầu AI có kết quả không rõ

Trước khi gửi, ghi call intent/fingerprint. Sau khi nhận, validate và lưu kết quả trước khi đánh dấu bước xong. Nếu provider có cơ chế idempotency hoặc truy vấn request được hỗ trợ thì tích hợp theo adapter.

Nếu timeout/ngắt điện sau khi gửi và chưa lưu được kết quả, chuyển bước sang `outcome_unknown`, job sang `blocked`: “Yêu cầu trước có thể đã được xử lý. Gửi lại có thể tính phí thêm.” Cho người dùng chủ động gửi lại. Không hứa bảo đảm provider chỉ tính phí một lần khi dịch vụ không hỗ trợ cơ chế đó.

### 4.5. Startup, dọn dữ liệu và tương thích

- Đọc job/commit journal trước khi dọn staging. Giữ file upload đã nhận đủ và checkpoint còn tham chiếu; dọn file rác theo manifest và thời hạn lưu.
- Chỉ tự đánh dấu interrupted sau khi xác minh không còn worker cũ hợp lệ. Trên Windows quản lý vòng đời subprocess để FFmpeg không tiếp tục ghi cùng file sau khi backend bị dừng đột ngột.
- Job JSON cũ vẫn xem được; job thiếu payload/snapshot được ghi rõ “Chạy lại từ đầu”, không gán giả khả năng resume.
- Dọn cache bỏ qua artifact được pin bởi job có thể tiếp tục. UI cho thấy dung lượng đang giữ để phục hồi và thao tác bỏ dữ liệu phục hồi riêng.
- Backup/thùng rác phải kiểm tra tham chiếu job. Phiên bản này chặn đưa dự án vào thùng rác khi còn job chưa kết thúc; người dùng hủy/bỏ job trước. Không xuất bí mật trong backup.

## 5. Mục 2 — Hàng đợi và kiểm soát tài nguyên

### 5.1. Bộ điều phối

Tách worker pool khỏi scheduler. Scheduler chỉ cấp bước khi đủ tài nguyên; worker không ngồi chờ giữ slot cho job chưa đủ điều kiện.

Mỗi bước khai báo lớp tài nguyên: `cpu_heavy`, `gpu_encode`, `io_heavy`, `network_ai`, RAM/đĩa ước lượng và nhu cầu khóa dự án. Cấp phát ở ranh giới bước; bảo đảm nhả token khi thành công, lỗi, hủy hoặc khởi động lại. Theo dõi riêng giới hạn provider; không cho số profile API làm tăng vô hạn số request.

Thiết lập khởi đầu thận trọng để đo thực tế: một bước render/encode nặng cùng lúc, một bước phân tích nặng cùng lúc, tổng tối đa hai bước nặng và hai request AI. Chế độ “Máy ít tài nguyên” chỉ chạy một bước nặng. Đây là mặc định thử nghiệm, chưa phải mức phù hợp mọi máy.

FIFO mặc định, cho đưa job đang chờ lên đầu; thêm cơ chế tăng ưu tiên theo thời gian để job cũ không bị chờ mãi. Không ngắt encode đang chạy chỉ để đổi ưu tiên.

### 5.2. Trạng thái và thao tác

| Trạng thái | Ý nghĩa | Hành động chính |
|---|---|---|
| `queued` | Chờ lượt/tài nguyên | Đổi thứ tự, hủy |
| `running` | Đang thực thi | Hủy; yêu cầu dừng ở cuối bước nếu luồng hỗ trợ |
| `blocked` | Thiếu file/đĩa, revision đổi hoặc kết quả AI chưa rõ | Xem nguyên nhân, khắc phục rồi tiếp tục |
| `paused` | Đã dừng tại ranh giới bước hoặc hàng đợi được người dùng giữ lại | Tiếp tục, hủy |
| `interrupted` | Tiến trình cũ đã dừng ngoài dự kiến | Xem kế hoạch phục hồi, tiếp tục |
| `cancelling` | Đang ngừng tiến trình/đợi request đã gửi | Xem trạng thái, không nhận thêm lệnh chạy |
| `done`, `error`, `cancelled` | Kết thúc lượt chạy | Xem kết quả, chẩn đoán; retry có kiểm tra nếu phù hợp |

Phân biệt “Tạm dừng nhận tác vụ mới” ở cấp hàng đợi với “Dừng sau bước hiện tại” ở cấp job. Không mô tả thao tác dừng giữa FFmpeg là pause tức thời.

Hàng đợi nhận nhiều dự án. Job render lấy snapshot lúc gửi và không khóa chỉnh sửa suốt thời gian chờ/chạy. Job sửa dự án chỉ giữ quyền ghi khi thực sự chạy, kiểm tra lại revision trước đó. Nếu hai job sửa cùng revision được xếp hàng, job sau phải bị chặn nếu job trước thay nội dung; không âm thầm chuyển sang đầu vào mới.

Nút xuất hàng loạt cho phép chọn nhiều dự án đã lưu, xem tên/revision/cấu hình rồi xếp hàng. Một job lỗi không chặn các dự án khác. Nếu lô lớn hơn giới hạn hiện có của danh sách job, dùng phân trang; job hoạt động luôn có đường truy vấn riêng để không bị mất vì giới hạn 100 bản ghi.

### 5.3. Kiểm tra dung lượng và tài nguyên

- Kiểm tra ổ chứa data/cache/export và ổ chứa staging/temp nếu khác nhau. Lấy dung lượng trống thực tế, trừ phần đang dự kiến dành cho các bước đã cấp phát.
- Ước lượng tổng file trung gian + output + khoảng dự phòng; không chỉ lấy bitrate MP4 cuối. Đếm đúng cache hit, file copy và audio PCM. Hiển thị “ước tính” vì không phải cấp phát đĩa thật.
- Tái kiểm tra trước bước nặng và định kỳ trong lúc chạy. Hết đĩa giữa bước phải dừng có kiểm soát, bỏ output chưa hoàn tất, giữ checkpoint trước đó và chặn job mới trên cùng ổ.
- Dùng telemetry RAM/CPU của cả backend và tiến trình con; giới hạn thread FFmpeg bằng cấu hình được đo. Có thể thêm `psutil` và cập nhật dependency lock sau khi kiểm tra tương thích.
- Khi RAM thiếu, hoãn cấp bước mới; nếu buộc phải dừng tiến trình hiện tại, chuyển blocked/error có thể phục hồi từ checkpoint trước. Đây là điều tiết, không cam kết hard limit RAM cho mọi codec.
- GPU: chỉ đo chỉ số có thể lấy đáng tin cậy; nếu không hỗ trợ thì hiển thị “không có số liệu”, vẫn giới hạn số encode GPU. Khi fallback CPU, chuyển token tài nguyên đúng cách, không lách giới hạn CPU.
- Đặt ngưỡng có vùng trễ để tránh liên tục chạy/dừng quanh một mức RAM/đĩa. Hiệu chỉnh bằng benchmark trên máy thật.

### 5.4. Tiến độ và giao diện

Tạo bảng “Tác vụ” toàn ứng dụng: dự án, thao tác, snapshot, trạng thái, bước hiện tại, tiến độ, thời gian chạy, thứ tự và lý do đang chờ. Chi tiết job hiển thị các checkpoint và lần thử.

Lấy tiến độ FFmpeg qua kênh progress riêng, tiếp tục thu stderr có giới hạn để không gây deadlock. Phân tích/TTS dùng số nguồn/câu/batch. ETA dựa trên số đo công việc tương tự và tốc độ hiện tại; chưa đủ dữ liệu hiển thị “Đang ước tính”. Chỉ hiện 100% sau validate và commit; phục hồi hiển thị rõ phần được tái sử dụng.

## 6. Mục 3 — Trung tâm lỗi và chẩn đoán

### 6.1. Hợp đồng lỗi dùng chung

Error envelope: `code`, `category`, `message`, `severity`, `job_id`, `step_id`, `retryable`, `actions`, `diagnostic_id`, `safe_details`, `occurred_at`. ID/action do backend whitelist; frontend chỉ ánh xạ hành động đã biết. Giữ `detail` tương thích trong giai đoạn chuyển đổi.

| Mã lỗi dự kiến | Xử lý cho người dùng |
|---|---|
| `DISK_SPACE_LOW` / `DISK_FULL` | Xem ổ thiếu dung lượng → mở Dung lượng → kiểm tra lại → tiếp tục |
| `MEDIA_MISSING` / `MEDIA_CHANGED` | Chọn tư liệu liên quan, hướng dẫn khôi phục đúng file hoặc tạo job mới; đánh giá lại checkpoint |
| `MEDIA_INVALID` | Chỉ đúng file không đọc được, hướng dẫn nhập file thay thế |
| `PROVIDER_AUTH` / `PROVIDER_QUOTA` | Mở đúng profile API; kiểm tra cấu hình/hạn mức trước khi retry |
| `PROVIDER_RATE_LIMIT` | Retry hữu hạn theo hướng dẫn provider nếu phân loại được; không coi mọi HTTP 429 là còn hạn mức |
| `PROVIDER_OUTCOME_UNKNOWN` | Hiển thị khả năng tính phí lặp; gửi lại chỉ sau hành động rõ ràng |
| `PROVIDER_TIMEOUT` / `NETWORK_UNAVAILABLE` | Kiểm tra kết nối; chính sách retry phụ thuộc việc request đã được gửi hay chưa |
| `ENCODER_UNAVAILABLE` | Thử CPU theo chính sách hiện có; ghi lại encoder thực dùng |
| `PROJECT_REVISION_CONFLICT` | Giữ kết quả cũ; mở bản hiện tại hoặc tạo tác vụ mới |
| `CHECKPOINT_INVALID` | Bỏ checkpoint lỗi và xác định bước phải chạy lại |
| `INTERNAL_ERROR` | Thông báo ngắn có mã tra cứu; tải báo cáo chẩn đoán |

Phân loại tại nơi biết nguyên nhân: mã HTTP/provider, errno hệ thống, kết quả probe, loại lỗi encoder. Chỉ dùng đối sánh stderr có phạm vi cho lỗi FFmpeg; không dựa toàn bộ vào tìm chuỗi thông báo.

### 6.2. Retry và lịch sử lỗi

Một tầng điều phối tổng ngân sách retry; tránh retry job nhân với retry provider và fallback thành nhiều lời gọi ngoài dự kiến. Retry có backoff, jitter và giới hạn tổng thời gian; ưu tiên `Retry-After` hợp lệ. Lỗi xác thực, file hỏng, xung đột revision và kết quả provider chưa rõ không tự retry mù.

Hủy phải ngắt được thời gian chờ backoff. Cùng nguyên nhân lỗi lặp được gom trong UI nhưng giữ attempt và timestamp. Job đang lỗi không biến mất khi đóng toast hoặc chuyển dự án.

### 6.3. Kết nối và báo cáo

Hook trả `connection_state`, `last_success_at`, số lần lỗi và job cũ kèm dấu “chưa cập nhật”. Poll có timeout/AbortController, chặn response cũ khi đổi project, backoff khi offline, giảm tần suất khi tab ẩn. Khi kết nối lại, đồng bộ theo attempt/state_version để không xử lý sự kiện done của lần chạy cũ.

API client xử lý response không phải JSON, 204, abort, lỗi mạng và lỗi HTTP riêng. Mất kết nối không được hiển thị như job đã thất bại hoặc phần trăm vẫn đang tăng.

Gói chẩn đoán gồm phiên bản ứng dụng/Python/FFmpeg, hệ điều hành, encoder, trạng thái job/bước, thống kê tài nguyên và sự kiện đã lọc. Tạo bằng whitelist trường; loại API key, Authorization, cookie, URL có token/query nhạy cảm, prompt, lời đọc, ảnh/audio/video, nội dung dự án và đường dẫn cá nhân. Stack trace/stderr chỉ đưa bản đã lọc theo chính sách, không đính toàn bộ file log/settings.

Người dùng xem trước danh mục rồi tải ZIP về máy. Không tự gửi báo cáo ra ngoài. Log có rotation, giới hạn kích thước/thời gian lưu; vẫn giữ metadata lỗi thiết yếu khi bỏ artifact lớn. Lỗi ghi log/đĩa đầy không được che mất lỗi gốc hoặc làm kẹt slot scheduler.

## 7. Phân chia mã nguồn dự kiến

| File/module | Trách nhiệm |
|---|---|
| `backend/jobs.py` | API nội bộ job, trạng thái, tương thích job cũ |
| `backend/job_store.py` mới | Schema job, snapshot, journal, ghi nguyên tử, migration |
| `backend/job_steps.py` mới | Registry handler, checkpoint, fingerprint, kế hoạch resume |
| `backend/job_scheduler.py` mới | Hàng đợi, thứ tự, lease, quota tài nguyên, pause/cancel |
| `backend/resource_monitor.py` mới | Dung lượng, reservation logic, RAM/CPU, telemetry |
| `backend/errors.py`, `backend/diagnostics.py` mới | Mã lỗi, chính sách retry, log an toàn, gói chẩn đoán |
| `backend/main.py`, `store.py`, `models.py`, `portable.py` | API, commit/revision, startup, bảo vệ dữ liệu job và migration |
| `backend/render.py`, `media.py`, `providers.py`, `audio_assembly.py`, `planner.py`, `face_filter.py` | Tách bước có checkpoint, tiến độ, adapter lỗi và lifecycle subprocess |
| `frontend/src/api/client.js`, `hooks/useJobs.js`, `App.jsx` | Lỗi có cấu trúc, kết nối, nhiều job, resume/retry, stale preview |
| `JobCenter.jsx`, `JobDetail.jsx`, `DiagnosticsPanel.jsx` mới | Hàng đợi toàn ứng dụng, bước xử lý, lỗi và tải chẩn đoán |
| `SettingsModal.jsx`, `ManagementPanels.jsx` | Chế độ tài nguyên, dung lượng checkpoint, giữ/xóa dữ liệu phục hồi |

API dự kiến: GET chi tiết job và kế hoạch resume; POST resume/retry/cancel/pause; GET queue; POST reorder/pause/resume queue; GET resource status; POST tạo gói chẩn đoán và GET tải gói. Tên endpoint chính xác chốt ở M0. POST thay đổi trạng thái nhận idempotency key + state_version; mọi action vẫn kiểm tra Host/Origin và quyền truy cập file hiện có.

## 8. Thứ tự triển khai và đầu ra từng mốc

| Mốc | Công việc | Đầu ra để nghiệm thu | Ước lượng ngày công |
|---|---|---|---|
| M0 | Chụp baseline; lập sơ đồ job, trạng thái, fingerprint, API/error schema; thiết kế fault injection | Đặc tả thống nhất, fixtures và danh sách hành vi tương thích | 1–2 |
| M1 | Job schema, registry, snapshot, commit journal/receipt, migration; hợp đồng lỗi tối thiểu | Job mới có payload bền; recovery không commit trùng; job cũ mở được | 3–4 |
| M2 | Checkpoint render/preview, manifest artifact, startup/cleanup, lifecycle FFmpeg | Gián đoạn ở clip hoặc mux có thể tiếp tục; bản xuất không mất/trùng | 3–4 |
| M3 | Checkpoint phân tích, audio assembly, TTS/ASR/upload; provider intent và retry budget | Giữ lời đọc/kết quả AI hợp lệ; trường hợp kết quả chưa rõ được xử lý đúng | 3–5 |
| M4 | Scheduler, nhiều dự án, snapshot render không chặn edit, resource monitor, reservation, tiến độ | Queue bền, giới hạn tài nguyên, lỗi một job không chặn cả hàng | 3–4 |
| M5 | Job Center, chi tiết lỗi, kết nối, hành động sửa lỗi và diagnostic ZIP | Người dùng thao tác toàn luồng trong UI, có báo cáo đã lọc | 3–4 |
| M6 | Fault tests, browser E2E, benchmark, Windows sạch, nâng cấp/rollback, tài liệu | Bản ứng viên phát hành và biên bản nghiệm thu | 3–5 |

Tổng dự kiến: 19–28 ngày công cho một người triển khai và kiểm thử. Đây là ước lượng phạm vi đầy đủ, không phải cam kết lịch; hiệu chỉnh sau M0. Không cộng công việc tính năng mới ngoài ba mục vào cùng đợt.

M0 → M1 là nền tảng chung. M2 cung cấp bản resume render đầu tiên; M3 hoàn thiện các luồng AI/audio. M4 sử dụng hợp đồng bước của M1–M3. M5 có thể dựng UI sớm bằng fixture sau M1, nhưng nghiệm thu sau M4. M6 chỉ bắt đầu nghiệm thu tích hợp khi cả ba mục đã kết nối.

## 9. Bộ nghiệm thu bắt buộc

| Nhóm | Tình huống | Kết quả cần đạt |
|---|---|---|
| Crash/restart | Dừng backend giữa clip 7/20; giữa ghép âm; sau mux | Tái sử dụng bước hợp lệ, chạy lại phần dở; không công bố MP4 thiếu |
| Commit | Dừng trước/sau project write, trước job done, trong cập nhật history | Một kết quả, một commit logic; không lặp revision/asset/export |
| Race | Hai tab cùng resume/cancel, double click xuất, restart rồi thử lại | Một lease; POST trùng trả cùng kết quả hoặc xung đột có giải thích |
| Input đổi | Sửa timeline, thay media, đổi model/font/renderer trước resume | Không dùng checkpoint sai; giữ sửa mới; báo bước phải chạy lại |
| Provider | Timeout trước/sau gửi, response hợp lệ nhưng crash trước persist, HTTP 401/429/5xx | Không retry ngoài ngân sách; phân biệt quota/rate limit; unknown outcome cần thao tác rõ ràng |
| Disk/RAM | Thiếu dung lượng trước chạy/giữa encode, volume temp khác data, lỗi ghi job/log | Không kẹt trạng thái/slot; giữ project và checkpoint trước; chỉ rõ ổ bị thiếu |
| Queue | 10+ dự án, quá 100 bản ghi job, đổi ưu tiên, tạm dừng, lỗi một job | Job active không mất khỏi UI; giới hạn concurrency; job khác tiếp tục |
| Cancel | Hủy queued/running, đang backoff, đang commit, GPU fallback | Không chạy lại job đã hủy; subprocess được xử lý; token luôn được thu hồi |
| Cleanup | Dọn cache, trash/backup khi còn job chờ/gián đoạn; manifest hỏng | Không xóa dữ liệu được pin; lỗi được cô lập, không chặn startup toàn bộ |
| UI | Mất mạng/backend, response không JSON, đổi dự án khi request cũ trả về | Banner đúng, không lẫn project/attempt, khôi phục polling và draft |
| Chẩn đoán | Key/URL chứa token, prompt hoặc đường dẫn cá nhân trong exception giả lập | Diagnostic ZIP và API lỗi không làm lộ dữ liệu bị loại |
| Tương thích | Dự án/job/export cũ, migration, undo/redo sau recovery | Đọc được dữ liệu cũ, stale-preview đúng, undo không kích hoạt reapply |
| Media thật | MOV xoay, VFR, H.264/H.265 nếu decoder hỗ trợ, voice tiếng Việt, crossfade | MP4 qua kiểm tra hình/âm/thời lượng hiện có; giải mã và xem mẫu đạt |

Kiểm thử lỗi đĩa qua fault injection trước; chỉ thử hệ thống đầy đĩa trên volume thử nghiệm có giới hạn, không lấp ổ người dùng. Dịch vụ AI mặc định mock; kiểm thử thật riêng với nội dung được phép và giới hạn chi phí rõ ràng khi có cấu hình sử dụng.

Chạy backend suite, frontend test/lint/build, thêm browser E2E cho queue/resume/disconnect. Benchmark cold/warm/resume trên cùng máy và media. Mục tiêu ban đầu: health/job API p95 dưới 500 ms khi tải nặng trên máy nghiệm thu; hiển thị mất kết nối trong khoảng 5–10 giây; hủy FFmpeg cục bộ trong 3 giây ở tình huống bình thường. Đây là tiêu chí cần đo và hiệu chỉnh ở M0, không áp dụng thời gian hủy đó cho request đã gửi sang provider.

Resume phải chứng minh giảm số công đoạn/gọi dịch vụ thực hiện, không chỉ nhanh hơn do cache nóng. Đo thời gian, peak RAM cả process tree, đĩa tạm cực đại, cache hit, số request AI và số commit. Giữ kết quả baseline để đối chiếu.

## 10. Phát hành và giới hạn phạm vi

Trước triển khai chụp bản sao mã hiện tại và dữ liệu thử, vì workspace đang có nhiều thay đổi. Migrate có version và backup; không chạy migration thử trên dự án đang dùng. Lưu golden fixtures cũ để kiểm tra đọc ngược và bảo toàn dữ liệu.

Phát hành theo cổng tính năng: nền lỗi/job trước, resume render tiếp theo, rồi các pipeline còn lại và queue tài nguyên. Cổng tính năng chỉ chọn giao diện/luồng hỗ trợ; không để hai scheduler cùng điều khiển một job. Rollback runtime chỉ thực hiện với dữ liệu tương thích hoặc phục hồi snapshot trước migration; không để bản cũ dọn `job_runs` của bản mới.

Tài liệu cuối đợt: README thao tác tiếp tục/hàng đợi/lỗi, release notes, định nghĩa mã lỗi, chính sách giữ checkpoint, báo cáo test/benchmark và hướng dẫn rollback.

Chưa thuộc phạm vi: tiếp tục giữa frame FFmpeg, upload theo byte, phân phối render nhiều máy, tài khoản nhóm, hard limit GPU/RAM mọi thiết bị, tự xác minh hóa đơn provider. Các giới hạn này phải được diễn đạt đúng trên UI, không dùng nhãn “tiếp tục” cho thao tác chạy lại toàn bộ.
