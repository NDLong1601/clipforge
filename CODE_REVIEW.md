**Rà soát ClipForge Local — 30/09/2026**

Đã đọc toàn bộ backend, frontend, bộ khởi động và kiểm thử. Chạy bằng Python 3.13 với đúng `requirements-lock.txt`, trong môi trường và thư mục dữ liệu tạm. Frontend cũng được cài/build từ bản sao trong thư mục tạm. Không thay đổi mã xử lý của ứng dụng.

Kết quả: **14/14 kiểm thử đạt; build production thành công**. Kiểm thử có xuất MP4 thật bằng FFmpeg. Các trường hợp bổ sung xác nhận những lỗi dưới đây mà bộ test hiện có chưa bao phủ. P1: cần sửa trước khi tăng tính năng vì ảnh hưởng khả năng mở/lưu dự án. P2: lỗi chức năng hoặc dữ liệu cần sửa trong đợt kế tiếp.

**1. P1 — Phân tích mẫu hơn 100 cảnh có thể làm dự án không mở lại được**

Vị trí: [main.py:218](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:218), [models.py:83](C:/Users/PC/Documents/clipforge-source/clipforge/backend/models.py:83), [store.py:28](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:28).

Luồng phân tích mẫu không dùng AI gán toàn bộ thời lượng cảnh vào `template.slot_durations`, trong khi schema giới hạn 100 phần tử. Pydantic không kiểm tra lại phép gán thuộc tính; `store.save()` ghi dữ liệu mà không xác thực toàn bộ dự án.

Đã kiểm tra với đầu ra bộ phân cảnh giả lập gồm 101 cảnh hợp lệ: tác vụ báo `done`, file dự án chứa 101 ô, nhưng GET dự án trả HTTP 400. Đây là kiểm tra ranh giới xử lý/lưu trữ; không dùng video thật có 101 cảnh. Một file như vậy cũng làm API danh sách dự án thất bại vì `store.projects()` đọc mọi dự án mà không cô lập file lỗi. Hoàn tác hiện cũng đọc bản hiện tại nên không tự cứu được trường hợp này.

Đề xuất: quy định rõ cách gộp hoặc giới hạn ô mẫu; báo cho người dùng trước khi lưu. Xác thực toàn bộ Project trước khi ghi và trước khi thay đổi lịch sử. Khi liệt kê, cô lập dự án hỏng và cung cấp phục hồi từ phiên bản hợp lệ gần nhất. Thêm test mẫu 100/101 cảnh và test một dự án lỗi không chặn các dự án khác.

**2. P2 — Hoàn tác sau khi gỡ voice có thể khôi phục tham chiếu bị thiếu**

Vị trí: [store.py:57](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:57), [main.py:151](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:151).

`undo()` lấy trạng thái chỉnh sửa cũ nhưng thay danh sách asset cũ bằng danh sách hiện tại. Nếu gỡ voice rồi hoàn tác, `voice_id` cũ được phục hồi nhưng voice không có trong `assets`. Đã tái hiện: hoàn tác trả HTTP 200, sau đó kiểm tra timeline lỗi “Không tìm thấy tư liệu”. Trường hợp xóa cảnh, gỡ nguồn rồi hoàn tác nhiều bước cũng có nguy cơ tương tự.

Đề xuất: giữ bản ghi asset với cờ xóa mềm, hoặc hợp nhất asset của lịch sử với asset mới nhập; kiểm tra tất cả tham chiếu trước khi lưu bản hoàn tác. Bao phủ cả voice, music, nguồn hình và lớp logo; kiểm tra hoàn tác nhiều bước.

**3. P2 — Thanh âm lượng không giữ được mức người dùng chọn khi chỉ có một track**

Vị trí: [render.py:164](C:/Users/PC/Documents/clipforge-source/clipforge/backend/render.py:164), [render.py:174](C:/Users/PC/Documents/clipforge-source/clipforge/backend/render.py:174).

Renderer áp dụng `volume` rồi `loudnorm=I=-16`. Với một track nhạc hoặc voice, chuẩn hóa cuối chuỗi gần như bù lại thay đổi âm lượng. Đã xuất hai MP4 thật có cùng nguồn nhạc: mức 10% cho RMS 0,17182; mức 80% cho RMS 0,17219. Chênh lệch chỉ **0,019 dB**, dù gain đặt chênh 8 lần.

Đề xuất: chuẩn hóa nguồn trước khi áp dụng gain; sau đó trộn, ducking và giới hạn peak. Nếu có chuẩn hóa master, cần xác định rõ nó tương tác thế nào với gain người dùng. Thêm test đo âm lượng đầu ra cho nhạc riêng, voice riêng và hai track kết hợp.

**4. P2 — Dựng lại timeline làm mất phụ đề đã sửa tay**

Vị trí: [planner.py:61](C:/Users/PC/Documents/clipforge-source/clipforge/backend/planner.py:61), [main.jsx:103](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/main.jsx:103).

Giao diện sửa nội dung/mốc cue nhưng không đổi `cue_timing` khỏi `estimated`. Planner luôn sinh lại cues khi gặp trạng thái này. Đã xác nhận chỉnh phụ đề từ 1–4 giây được PUT lưu thành công, nhưng bấm dựng lại khiến nội dung và mốc trở về câu gốc 0–5 giây.

Đề xuất: thêm trạng thái hoặc cờ ghi đè thủ công; bảo toàn cue đã sửa khi lập timeline. Phân biệt thao tác đổi kịch bản, đổi thời lượng và chỉ đổi cảnh. Có lệnh riêng để tạo lại mốc ước lượng khi người dùng cần.

**5. P2 — Template có logo không dùng lại được đầy đủ ở dự án khác**

Vị trí: [main.py:202](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:202), [render.py:34](C:/Users/PC/Documents/clipforge-source/clipforge/backend/render.py:34).

Lưu template chỉ lưu JSON, bao gồm `asset_id` của logo thuộc dự án gốc. Dự án khác không có ID đó. Đã xác nhận API chấp nhận áp dụng mẫu vào dự án mới, nhưng render lỗi “Không tìm thấy tư liệu”.

Đề xuất: đóng gói ảnh/font cần thiết cùng template và ánh xạ ID khi áp dụng, hoặc cho người dùng chọn ảnh thay thế trước khi nhận mẫu. Kiểm tra mọi tham chiếu lớp khi lưu dự án để báo thiếu tài nguyên trước khi xuất.

**6. P2 — Khóa lấy từ biến môi trường vẫn bị ghi vào settings.json**

Vị trí: [store.py:63](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:63), [store.py:98](C:/Users/PC/Documents/clipforge-source/clipforge/backend/store.py:98).

`settings()` trộn khóa môi trường vào cấu hình đọc; `save_settings()` lấy cấu hình đã trộn làm dữ liệu để ghi. Chỉ đổi nhà cung cấp voice cũng có thể ghi khóa OpenAI/Azure từ môi trường ra file. Đã xác nhận bằng khóa giả, không gọi dịch vụ thật. Điều này trái với mô tả README rằng dùng biến môi trường có thể tránh ghi khóa vào file.

Đề xuất: tách cấu hình lưu trên đĩa và cấu hình hiệu lực khi chạy; chỉ ghi secret do người dùng nhập/lưu rõ ràng. Thêm test lưu các trường không liên quan mà vẫn giữ khóa môi trường ngoài file.

**7. P2 — Bộ khởi động có thể bỏ sót bản đang chạy ở cổng khác**

Vị trí: [launch.py:42](C:/Users/PC/Documents/clipforge-source/clipforge/launch.py:42).

`choose_port()` trả ngay khi gặp cổng trống đầu tiên, trước khi kiểm tra các cổng sau. Nếu lần trước 8765 bận nên ứng dụng chạy ở 8766, rồi 8765 được giải phóng, lần mở kế tiếp sẽ chạy thêm server trên 8765. Đã kiểm tra lựa chọn cổng bằng giả lập trạng thái mạng.

Đề xuất: khi không chỉ định cổng, quét toàn bộ dải để tìm instance đang chạy trước, sau đó mới chọn cổng trống. Nên thêm khóa single-instance theo thư mục dữ liệu vì các khóa thread hiện tại không bảo vệ giữa hai tiến trình.

**Các vấn đề xác định từ mã, chưa đo bằng benchmark hoặc tái hiện đầy đủ**

- [main.py:117](C:/Users/PC/Documents/clipforge-source/clipforge/backend/main.py:117): upload là endpoint async nhưng đọc/ghi file, probe và tạo thumbnail đều đồng bộ. Công việc này chặn event loop; đồng thời giữ khóa chung của jobs/store. Chuyển xử lý nặng sang thread hoặc job nền, dùng khóa theo dự án và đưa tiến độ upload/nhập file lên giao diện. Giới hạn dung lượng hiện được kiểm tra trong endpoint, sau bước xử lý multipart của framework; cần thêm giới hạn phù hợp trước khi nhận toàn bộ file.
- [media.py:106](C:/Users/PC/Documents/clipforge-source/clipforge/backend/media.py:106): phân cảnh seek mỗi 0,4 giây, rồi chạy một FFmpeg riêng cho từng thumbnail. Theo mã, video 120 phút có khoảng 18.000 lần seek và, nếu chia cửa sổ 3 giây, khoảng 2.400 lần tạo thumbnail. Nên decode tuần tự ở độ phân giải thấp và tạo thumbnail theo batch; cần benchmark với video thực trước khi chọn tối ưu.
- [jobs.py:7](C:/Users/PC/Documents/clipforge-source/clipforge/backend/jobs.py:7): job chỉ được nạp vào RAM; file kết quả được ghi khi kết thúc nhưng không được đọc lại khi khởi động. Danh sách trong RAM cũng không được giới hạn dù API chỉ trả 30 job cuối. Cần tải lịch sử, đánh dấu job bị gián đoạn và dọn theo chính sách lưu giữ. Khi gọi AI, hủy chỉ có hiệu lực ở lần kiểm tra tiếp theo; lời gọi HTTP đang chờ chưa được ngắt.

**Chức năng nên cải tiến sau khi sửa các lỗi trên**

| Ưu tiên | Chức năng | Giá trị sử dụng |
|---|---|---|
| Cao | Autosave có kiểm tra revision, phục hồi bản nháp, undo/redo nhiều bước | Giảm mất công chỉnh khi đóng tab, mất điện hoặc xung đột phiên bản |
| Cao | Waveform âm thanh thật, split/trim, snapping, phím tắt và khóa cảnh đã duyệt | Chỉnh nhịp dựng dễ hơn; giữ các cảnh đã chỉnh khi chạy tự động dựng |
| Cao | Kiểm tra trước khi xuất | Báo ngay tài nguyên thiếu, cảnh sẽ giữ khung cuối, timeline ngắn hơn voice, mốc phụ đề sai và lớp nằm ngoài vùng mong muốn |
| Cao | Backup/import ZIP gồm JSON và media; quản lý dung lượng | JSON hiện chỉ mô tả dự án. ZIP giúp chuyển máy/phục hồi; dọn cache và bản xuất giúp tránh đầy ổ đĩa |
| Trung bình | Thư viện template có tài nguyên, thumbnail, sửa/xóa và ánh xạ theo tỷ lệ khung | Tái sử dụng mẫu/logo thuận tiện và giảm lỗi khi chuyển dự án |
| Trung bình | AI cho xem phần thay đổi, áp dụng từng phần, hiển thị API/model thực dùng | Người dùng duyệt đúng cảnh/chữ và hiểu khi dịch vụ chuyển dự phòng |
| Trung bình | Phân tích cảnh theo batch, proxy media, cache và lựa chọn render theo phần cứng | Giảm chờ khi thư viện có nhiều video dài; cần benchmark trước khi triển khai |
| Trung bình | Chọn font, preset phụ đề/vùng an toàn, crossfade và mức âm thanh nguồn | Tăng khả năng kiểm soát chất lượng thành phẩm |

**Cấu trúc mã và kiểm thử nên bổ sung**

Frontend hiện dồn API, quản lý dự án/job, timeline, preview và cài đặt vào [main.jsx](C:/Users/PC/Documents/clipforge-source/clipforge/frontend/src/main.jsx). Nên tách thành API client, hook quản lý dự án/job và các component panel; thêm định dạng/lint để diff dễ đọc. Backend nên gom xác thực liên kết asset/clip/layer/voice vào một nơi, dùng schema riêng cho request thay vì các `dict` tự do ở một số endpoint.

Bổ sung kiểm thử hồi quy cho bảy lỗi đã xác nhận; thêm test giao diện cho lưu/mở lại, sửa phụ đề rồi dựng lại, áp dụng template có logo, đổi dự án và polling job. Bộ test hiện tại đã có giá trị vì chạy FFmpeg thật, nhưng chưa có kiểm thử frontend tự động trong package.json.

Giới hạn của đợt rà soát: chưa gọi OpenAI/Gemini/Azure thật, chưa tải/chạy Faster Whisper và chưa chạy kiểm thử thao tác bằng trình duyệt. Các phép thử lỗi mẫu nhiều cảnh và chọn cổng sử dụng giả lập có kiểm soát; phép thử âm lượng và template thiếu logo chạy renderer thật. Không đánh giá chất lượng AI hoặc hiệu năng trên bộ video thật của người dùng trong đợt này.


