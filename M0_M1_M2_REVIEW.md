**Review nghiệm thu M0, M1, M2 — 30/09/2026**

Kết luận: **chưa đạt đầy đủ để đóng M1/M2 hoặc bàn giao bản sửa lỗi cho mọi dự án cũ**. Bảy ca hồi quy từ review ban đầu đã qua. Tuy nhiên, năm ca bổ sung tái hiện bốn vấn đề dưới đây, gồm lỗi tương thích dữ liệu trước nâng cấp và thiếu bản gốc phục vụ rollback.

Review này chỉ thêm báo cáo; không sửa logic ứng dụng và không dùng dữ liệu dự án thật. Kiểm thử và build dùng các thư mục tạm.

| Mốc | Đánh giá | Phần đã xác nhận | Phần chưa đủ |
|---|---|---|---|
| M0 | Đạt baseline và bộ thử tổng hợp; còn phần nghiệm thu media thật | Test hồi quy, fixture, corpus generator, benchmark và build có thể chạy lại | Chưa có kết quả trên video thực/voice tiếng Việt tự nhiên; chưa có nghiệm thu trực tiếp bằng trình duyệt trong đợt review này |
| M1 | Chưa đạt đầy đủ | Validate trước lưu, gộp 101 cảnh, cô lập/phục hồi JSON lỗi, undo asset xóa mềm, không ghi key môi trường, khóa giữa tiến trình | Migration chưa xử lý một số trạng thái hợp lệ của bản cũ; không giữ bản gốc trước nâng schema để rollback |
| M2 | Chưa đạt đầy đủ | Gain audio, giữ cue khi replan, template dùng ảnh overlay, preflight dùng chung | Lưu gói template thất bại với ảnh source mà UI cho chọn làm logo; sửa cue không xóa được trạng thái stale |

**Bằng chứng chạy lại**

- `pytest tests -q`: **44 passed, 1 warning**, 46,71 giây. Không có skip/xfail. Warning là deprecation của Starlette/httpx trong TestClient, không phải test chức năng thất bại.
- Build frontend production thành công với Vite 7.3.6; kết quả ghi vào thư mục tạm.
- Corpus generator chạy thành công, tạo 8 file; script tự kiểm tra MOV xoay 90° và VFR có nhiều khoảng frame.
- Benchmark cùng workload M0: phân tích video tổng hợp 60 giây mất **5,22 giây**, tạo 20 cảnh/thumbnail, RAM peak **126,8 MB**; render preview 5 giây mất **1,96 giây**, RAM peak **286,5 MB**.
- Các giá trị gần baseline đã ghi nhận. Đây là kiểm tra khả năng lặp lại, chưa phải chứng minh tối ưu hay chất lượng trên video thật.
- Năm ca bổ sung dùng dữ liệu tạm: hai ca mở dự án legacy, một ca migration snapshot, một ca lưu template source image và một ca xác nhận chỉnh sửa cue stale. Các HTTP/status trong phần findings đều được tái hiện qua API.

**[P1] F1 — Validator mới làm một số dự án bản cũ không mở được**

Vị trí: [project_validation.py:79](C:/Users/PC/Documents/clipforge-source/clipforge/backend/project_validation.py:79), [project_validation.py:52](C:/Users/PC/Documents/clipforge-source/clipforge/backend/project_validation.py:52), [store.py:70](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:70).

Hai trạng thái từng được bản 1.0 chấp nhận bị từ chối khi đọc bằng bản mới:

1. Asset music là video có audio. `media.register()` bản cũ cho nhập video vào vai trò music nếu có audio, và renderer lấy luồng audio của nó. Validator mới chỉ cho phép music có `media == 'audio'`. Ca legacy này mở lại trả HTTP 400: “music_id: phải trỏ tới tư liệu nhạc audio”.
2. Lớp từng là image, sau đó đổi thành text/rect/circle nhưng còn asset_id. LayerEditor bản cũ chỉ đổi kind, không xóa asset_id; renderer bỏ qua tham chiếu đó với lớp không phải ảnh. Validator mới từ chối. Ca legacy trả HTTP 400: “chỉ lớp ảnh mới được tham chiếu tư liệu”.

Migration hiện chỉ thêm deleted/schema_version/các cờ cue, rồi chạy validator mới. Nếu lịch sử cũng chứa các trạng thái này thì cơ chế phục hồi không tìm được snapshot hợp lệ, dù file media và JSON còn nguyên.

Hướng sửa: bảo toàn hỗ trợ music từ video có audio, hoặc chuyển đổi có backup và trích audio theo một bước migration riêng. Xóa asset_id không có tác dụng của lớp không phải image trong migration cũ. Áp dụng xử lý này cho cả project hiện tại và lịch sử; vẫn giữ kiểm tra tham chiếu cho dữ liệu mới.

Test cần bổ sung: đọc/migrate/lưu lại/render music video legacy; lớp đổi từ image sang text/rect; phục hồi từ lịch sử có hai kiểu dữ liệu đó. Fixture legacy hiện quá đơn giản để bao phủ ràng buộc mới.

**[P1] F2 — Snapshot trước lần ghi nâng schema đã bị migrate, không còn bản gốc để rollback**

Vị trí: [store.py:116](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:116).

`save()` gọi `read()` để tạo snapshot lịch sử. `read()` đã migrate dữ liệu lên schema 2, nên snapshot và project.json sau lần lưu đầu đều là schema 2. Trong ca tái hiện từ JSON bản 1.0 không có schema_version, sau lần lưu có hai file JSON và cả hai đều mang schema 2; không file nào giữ bytes gốc.

Snapshot vẫn hữu ích để phục hồi bằng backend hiện tại, nhưng không đáp ứng phương án rollback về backend cũ vốn dùng `extra='forbid'` và không biết các trường mới. Sau nhiều lần lưu, lịch sử còn bị giới hạn 25 bản nên cũng không phù hợp làm nơi giữ bản nâng cấp đầu tiên.

Hướng sửa: trước lần ghi đổi schema, giữ bản sao nguyên bản theo phiên bản/thời điểm, tách khỏi lịch sử chỉnh sửa có giới hạn. Xác nhận backup hoàn thành rồi mới thay project.json. Quy định giữ các file media mà backup tham chiếu, cung cấp đường phục hồi rõ ràng và kiểm tra migration chạy lặp không tạo hỏng dữ liệu.

Test cần bổ sung: giữ đúng bytes/schema cũ trước nâng cấp; backup thất bại không được thay project.json; đọc bản rollback bằng schema cũ; bản gốc vẫn tồn tại sau hơn 25 lần lưu; kiểm tra tiến trình bị ngắt ở ranh giới backup/replace.

**[P2] F3 — Ảnh nguồn được UI cho chọn làm logo nhưng không lưu được gói template**

Vị trí: [template_packages.py:128](C:/Users/PC/Documents/clipforge-source/clipforge/backend/template_packages.py:128), [main.jsx:119](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/main.jsx:119).

LayerEditor cho chọn mọi asset image đang hoạt động, bao gồm role source. Project validator cũng chấp nhận lớp ảnh dùng loại này. Tuy nhiên `save_package()` bắt buộc role overlay, và bộ tìm ảnh cho template legacy cũng chỉ lập index ảnh overlay.

Đã tái hiện: dự án có source.png, lớp image dùng source đó lưu dự án thành công; API lưu gói template trả HTTP 400: “không trỏ tới logo/ảnh đang hoạt động”. Mẫu legacy dùng ảnh source còn có thể bị báo thiếu tài nguyên dù ảnh vẫn tồn tại.

Hướng sửa: thống nhất quy tắc cho ảnh nguồn/overlay. Khuyến nghị cho đóng gói mọi ảnh hợp lệ đã dùng trong lớp để tương thích dữ liệu cũ; tài nguyên sao chép sang dự án đích có thể đăng ký với role overlay. Cập nhật index, lựa chọn thay thế và kiểm thử cùng quy tắc.

Test cần bổ sung: lưu, áp dụng và render template dùng source image; mẫu legacy có ảnh source; layers và slot_layers đều được remap.

**[P2] F4 — Sửa phụ đề thủ công không xác nhận được đã xử lý trạng thái stale**

Vị trí: [main.py:134](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:134), [main.jsx:108](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/main.jsx:108).

Khi người dùng sửa cue, frontend gửi `cues_edited=true` và `cues_stale=false`. Nếu script/voice không đổi ở request đó, backend lại gán `p.cues_stale=old.cues_stale`, nên trạng thái đã stale luôn quay trở lại true sau khi lưu.

Đã tái hiện: dự án stale có cue cũ, sửa text cue và gửi cờ false; PUT trả HTTP 200 nhưng response vẫn `cues_stale=true`. Người dùng đã sửa tay tiếp tục thấy nhắc tạo lại cue, thao tác đó có thể thay nội dung/mốc mà họ muốn giữ.

Ngoài ra nút “Tạo lại mốc phụ đề” chỉ xuất hiện khi stale. Dự án cũ được migration đánh dấu cues_edited=true để bảo toàn dữ liệu; nếu người dùng chỉ đổi thời lượng, không đổi script, UI không cung cấp nút này để chủ động tạo lại mốc. Đây là kết luận từ mã giao diện, chưa tái hiện bằng trình duyệt.

Hướng sửa: thêm thao tác xác nhận đã kiểm tra hoặc cho phép cờ xác nhận được áp dụng với revision hợp lệ; làm rõ khi nào sửa một cue có thể kết thúc trạng thái cần kiểm tra. Cho truy cập lệnh tạo lại cue độc lập với cờ stale và báo tác động trước khi thay cue thủ công.

Test cần bổ sung: đổi script → sửa/xác nhận cue → lưu/mở lại không còn stale; sửa một phần không làm mất các cue khác; đổi thời lượng → tạo lại cue chủ động; bảo toàn thao tác ghi đè phụ đề cảnh.

**Điều kiện đóng mốc**

M1: xử lý F1/F2 và bổ sung kiểm thử legacy có media/layer thực tế, kiểm tra rollback. M2: xử lý F3/F4 và kiểm tra luồng người dùng trên trình duyệt, đồng thời giữ 44 test hiện tại đạt. M0: phần baseline/hồi quy đã xác nhận; trước khi nghiệm thu thành phẩm cần ghi nhận kết quả trên bộ video thực và voice tiếng Việt tự nhiên đã được phép dùng.

Các phần autosave, redo, tối ưu upload/scene detection và quản lý dung lượng thuộc các mốc sau; chúng không được dùng làm lý do từ chối riêng M0–M2 trong review này.
