# ClipForge Local

Công cụ dựng video ngắn 30–45 giây, giao diện web chạy trên máy cá nhân. React + FastAPI + FFmpeg; có mã nguồn frontend, backend, bộ kiểm thử và bộ khởi động Windows.

## Chạy nhanh trên Windows

1. Cài **Python 3.12 trở lên** và chọn “Add Python to PATH”. Bản này đã kiểm tra bằng Python 3.13 trên Windows.
2. Trong File Explorer, **nhấp đúp `MO_CLIPFORGE.bat`**. File `START.bat` cũ cũng hoạt động. Lần đầu công cụ tạo `.venv` và cài các gói Python, cần Internet. FFmpeg đi kèm gói `imageio-ffmpeg`.
3. Khi server sẵn sàng, trình duyệt tự mở giao diện, thường tại **http://127.0.0.1:8765**. Nếu cổng này bận, chương trình chọn cổng trống tiếp theo. Giữ cửa sổ chạy chương trình; nhấn Ctrl+C hoặc đóng cửa sổ để dừng. Nhấp đúp file lần nữa khi server còn chạy sẽ mở lại trang mà không khởi động bản thứ hai.

Bản bàn giao có sẵn `frontend/dist`, nên **không cần Node.js để sử dụng**. Nếu sửa giao diện và dựng lại, cần Node.js 20.19+ hoặc 22.12+:

```powershell
cd frontend
npm ci
npm run build
cd ..
python launch.py
```

Đổi cổng: `python launch.py --port 8766`. Cài trước nhưng chưa chạy: `python launch.py --setup-only`. Không tự mở trình duyệt: thêm `--no-browser`.

Tạo gói mã nguồn phát hành (kèm `frontend/dist`, file khóa dependency và hướng dẫn chạy) bằng `python tools/package_release.py`. ZIP cùng tệp SHA-256 được đặt trong `release/`; dữ liệu cá nhân, API key và môi trường cài đặt cục bộ không được đưa vào gói.

## Nhiều API key, model và chuyển dự phòng

1. Mở chương trình rồi bấm **Cài đặt API** ở góc trên bên phải. Chọn **Gemini**, **OpenAI** hoặc **API tương thích OpenAI**, rồi bấm **Thêm API**. Có thể thêm nhiều khóa, kể cả nhiều khóa cùng một nhà cung cấp.
2. Chọn một cấu hình trong danh sách, sửa tên/model và dán khóa vào ô **API key**. Với API tương thích OpenAI, nhập thêm **API base URL**. Bấm **Lưu và kiểm tra API này** để xác nhận riêng cấu hình đó; thao tác này gọi dịch vụ thật và có thể tính phí.
3. Bấm **Dùng chính** để chọn API/model ưu tiên. Nút ↑/↓ sắp xếp thứ tự các cấu hình dự phòng. Bật **Tự chuyển API khi lỗi** để chương trình thử lần lượt các cấu hình đang bật sau khi API chính lỗi; tắt để chỉ dùng API chính. Cuối cùng bấm **Lưu cài đặt**.
4. Chuyển dự phòng áp dụng cho phân tích cảnh/mẫu, lập kế hoạch bằng AI và trợ lý. Tạo giọng/căn phụ đề dùng cấu hình OpenAI hoặc API tương thích hỗ trợ Speech, Azure Speech hoặc nhận dạng cục bộ; khóa Gemini ở danh sách AI không dùng cho các endpoint Speech này.

Khóa chỉ lưu tại máy trong `data/settings.json`; API đọc cài đặt chỉ trả dấu che khóa. Cấu hình một khóa từ bản cũ được chuyển vào danh sách khi mở lại. Gemini dùng [endpoint tương thích OpenAI của Google](https://ai.google.dev/gemini-api/docs/openai).

## Quy trình tái biên tập

1. Tạo dự án, chọn **Tái biên tập**.
2. Trong **Tư liệu**, nhập 5–10 video ở vai trò **Nguồn dựng**. Có thể nhập thêm ảnh. Mỗi lần tối đa 10 file, mỗi file tối đa 2 GB và 120 phút.
3. Thêm nhãn cho tư liệu hoặc bật **Dùng AI để hiểu nội dung từng cảnh**, rồi bấm **Phân tích**. Video được giải mã tuần tự; bộ dò lấy mẫu ở độ phân giải thấp và dùng timestamp của từng frame để phát hiện điểm đổi cảnh, sau đó tạo thumbnail theo batch. Kết quả được cache theo file và cấu hình phân tích.
4. Dán nội dung vào **Kịch bản**, chọn thời lượng (mặc định 36 giây; hỗ trợ 5–180 giây).
5. Trong **Âm thanh**, nhập voice có sẵn hoặc tạo voice từ kịch bản. Chọn **Căn phụ đề từ voice** nếu cần mốc từng từ chính xác hơn.
6. Bấm **Tự động dựng**. Chế độ cơ bản chọn theo nhãn/từ khóa, độ đa dạng và chất lượng. Chế độ AI sử dụng mô tả hình ảnh để đề xuất cảnh phù hợp từng ý.
7. Timeline dùng chung trục thời gian cho cảnh, ruler, playhead, waveform voice và nhạc. Kéo mép cảnh để trim; cảnh tối thiểu 0,2 giây và có snap theo ranh giới cảnh/phụ đề. Các nút cho phép split tại playhead, nhân đôi, xóa, khóa cảnh đã duyệt và zoom; kéo thả để đổi thứ tự.
8. Phím tắt: Space phát/tạm dừng, S split, Delete xóa, mũi tên trái/phải chuyển cảnh, Ctrl/Cmd+Z undo và Ctrl/Cmd+Shift+Z hoặc Y redo. Phím tắt không chạy khi đang gõ trong trường nhập.
9. **Dựng xem thử** xuất MP4 720p và lưu revision nguồn. Nếu dự án đổi, bản MP4 cũ có nhãn **Bản xem thử cũ**; quay lại xem nhanh bản đang chỉnh. Preflight chặn lỗi xuất, còn cảnh báo vẫn cho tiếp tục và có nút đi tới cảnh/tư liệu/cue cần kiểm tra.

Waveform được tính từ audio nguồn rồi cache theo asset và phiên bản file. Khóa một cảnh sẽ giữ nguyên nội dung, vị trí và thời lượng khi **Tự động dựng**; nếu thời lượng đích không thể chứa cảnh đã khóa, ClipForge báo lỗi để bạn chỉnh thời lượng hoặc mở khóa cảnh.

Crossfade dùng cùng trục thời gian cho timeline, preview, phụ đề, voice và âm thanh nguồn. Thời lượng project bằng tổng thời lượng clip trừ các đoạn overlap. Preflight chặn transition thiếu nguồn hình hoặc chồng lấn quá nhiều; voice dài hơn timeline cũng bị chặn để không mất lời.

Phụ đề ghi đè chuyển sang cảnh mới ngay khi crossfade bắt đầu; live preview, ASS và SRT dùng cùng quy tắc, không xếp hai caption theo cảnh trong overlap. Với dự án MOV đã phân tích bằng bản cũ, bấm **Phân tích** để sửa thumbnail xoay; ID cảnh và liên kết timeline được giữ nguyên.

Thay đổi được tự lưu sau khoảng 1,3 giây ngừng chỉnh; nút **Lưu** vẫn cho phép lưu ngay. **Hoàn tác / Làm lại** gom các lần gõ liên tiếp trên cùng trường thành một thao tác. Bản nháp được giữ trong bộ nhớ trình duyệt để phục hồi khi mở lại. Nếu một tab khác đã lưu revision mới, ClipForge giữ bản nháp và cho xem khác biệt hoặc tải bản server; nó không tự ghi đè revision mới.

Khi nhập file, tiến độ tải lên và tiến độ kiểm tra/tạo thumbnail hiển thị riêng. Job đã lưu trạng thái; sau khi mở lại, tác vụ dở dang hiện là **gián đoạn** và chỉ chạy lại khi bạn chủ động chọn thao tác tương ứng. Hủy FFmpeg dừng tiến trình cục bộ; yêu cầu đã gửi tới dịch vụ AI/TTS có thể vẫn được xử lý ở phía dịch vụ.

## Quy trình theo format mẫu

**Lọc khuôn mặt:** trong **Tư liệu**, tích **Hỗ trợ bản quyền · tránh cảnh có khuôn mặt**.
Công cụ kiểm tra nhiều khung hình bằng mô hình cục bộ, thay cảnh có mặt/chưa kiểm tra được và giữ
thời lượng, lời đọc, bố cục. Lựa chọn được lưu theo dự án và áp dụng khi tự dựng hoặc dựng theo audio.
Bấm **Lọc lại cảnh có mặt** sau khi nhập nguồn hoặc sửa mốc cắt/crop. Cảnh khóa cần được mở khóa
nếu có mặt; khi thiếu nguồn phù hợp công cụ báo lỗi và giữ timeline cũ. Bộ lọc không xác minh giấy
phép tư liệu, có thể bỏ sót hoặc báo nhầm; hãy duyệt bản xuất trước khi đăng.

1. Nhập video tham chiếu ở vai trò **Video mẫu**; logo/biểu tượng PNG/JPG ở vai trò **Logo / biểu tượng**.
2. Mở **Mẫu dựng**, chọn video và **Phân tích mẫu**.
3. Không bật AI: lấy nhịp cắt/thời lượng các ô, rồi chỉnh bố cục bằng giao diện.
4. Bật AI: gửi tối đa 6 ảnh đại diện tới model để đề xuất khung hình, lớp chữ, hình khối, màu, phụ đề và bố cục theo ô.
5. Duyệt/chỉnh các lớp; dùng `{title}`, `{project}`, `{caption}` để thay nội dung tự động. Có lớp chữ, hình chữ nhật, hình tròn, ảnh/logo; tọa độ tương đối 0–1; hiệu ứng tĩnh, hiện dần, trượt vào.
6. Chọn nguồn và kịch bản mới, tạo voice, **Tự động dựng**. Nhịp các ô được co theo tổng thời lượng. **Lưu mẫu** để tái sử dụng ở dự án khác.

Template JSON có `viewport`, `layers`, `slot_durations`, `slot_layers`, `caption`, `transition`. `slot_layers` cho phép mỗi ô có nhóm lớp riêng. Các preset Toàn khung, Khung biên tập và Nhịp nhanh có sẵn.

## Voice, phụ đề và nhạc

- **Sound effect:** tab **Âm thanh** có 10 hiệu ứng tổng hợp tích hợp: Whoosh, Pop, Click, Ding, Sparkle, Impact, Rise, Swish, Success và Camera. Nghe thử, chọn mốc chèn, chỉnh mức âm chung/riêng hoặc xóa cue thủ công. Track **SFX** hiển thị trên timeline và phát trong xem nhanh, MP4 xem thử và bản xuất chính. Khi bật tự chèn, công cụ ưu tiên các từ nhấn mạnh, ưu đãi, lời kêu gọi và chuyển cảnh, giãn cách ít nhất 2 giây; chạy lại tự dựng giữ cue thủ công. Tắt tự chèn chỉ tắt cue tự động.
- **Chữ sản phẩm trong template:** phần **Mẫu dựng → Nội dung sản phẩm** có tên, mô tả và lời kêu gọi. Để trống tên để lấy theo source mới nhất; mô tả dùng kịch bản/nhãn nguồn. Bật AI khi phân tích hoặc tự dựng để nhận diện từ tư liệu mới; chữ nhập tay được ưu tiên. Các mẫu cũ dùng “TÊN SẢN PHẨM”, “MÔ TẢ SẢN PHẨM” được tự liên kết; lớp tùy chỉnh có thể chọn **Nội dung lấy từ** hoặc dùng `{product_name}`, `{product_description}`, `{cta}`. Thông tin AI gắn với ID nguồn, không dùng lại tên đã nhận diện của nguồn cũ.
- **Xuất video ổn định:** các mốc hình dùng trục 30 fps và làm tròn theo ranh giới tuyệt đối, tránh tích lũy sai lệch ở project nhiều cảnh. Renderer giữ timestamp liên tục qua nguồn có metadata màu khác nhau, dùng đồng hồ âm thanh 48 kHz nguyên mẫu và kiểm tra lại MP4 sau mux. Cache cảnh từ renderer cũ tự hết hiệu lực; xuất lại project cũ để nhận bản sửa.

- **Dựng từ audio tải lên:** trong **Âm thanh**, bật **Tự phân tích và ghép cảnh khi tải audio lên** (mặc định bật). Nhập voice để tự nhận dạng lời đọc, phân tích video nguồn và dựng timeline theo từng ý. Nếu chưa có nguồn, công cụ giữ lời đã nhận dạng; nhập nguồn sau đó sẽ tự dựng tiếp. Nút **Phân tích audio & ghép cảnh** cho phép chạy lại khi API lỗi. File đã nhập vẫn được giữ nếu bước AI thất bại. Audio cho luồng này tối đa 180 giây.
- **Dựng mượt:** bật **Dựng mượt · hòa tan ngắn, tránh giữ khung cuối** trước khi dựng lại. Công cụ đặt hòa tan ngắn quanh mốc câu/khoảng nghỉ, ưu tiên nguồn đủ dài và giảm tốc cảnh ngắn thay vì giữ ảnh đứng. Xem nhanh tải trước cảnh kế tiếp. Cảnh đã khóa vẫn được giữ nguyên. Tùy chọn mặc định bật cho dự án tạo mới; dự án cũ giữ cài đặt cho đến khi bạn bật.
- **Che chữ gốc:** tại **Tư liệu → Chữ có sẵn trên video nguồn**, chọn **Che kín vùng chữ** hoặc **Làm mờ vùng chữ**. AI phân tích ảnh đại diện để tìm chữ/phụ đề chèn sẵn; dựng theo audio tự chạy bước này. Trong điều chỉnh cảnh, mở **Che chữ gốc** để sửa vị trí theo % video gốc, thêm/bỏ vùng, tắt riêng từng cảnh hoặc dùng lại vùng AI. Xử lý diễn ra trước crop và lớp phụ đề mới. Chữ di chuyển hoặc chỉ xuất hiện ngoài ảnh đại diện cần kiểm tra và chỉnh vùng thủ công; đây là che/làm mờ, không phục hồi nền gốc.
- **Gemini cho audio:** bộ nhận dạng **Tự chọn** ưu tiên API Speech OpenAI/tương thích nếu có, rồi dùng API/model Gemini đang bật. Bản cũ đặt OpenAI nhưng chỉ có khóa Gemini cũng dùng Gemini. Mốc Gemini là ước lượng theo câu/ý; dùng Whisper và chỉnh cue nếu cần mốc từng từ. Khóa Gemini được gửi bằng header đến endpoint chính thức, không đặt trong URL hay log. [Tài liệu audio Gemini](https://ai.google.dev/gemini-api/docs/generate-content/audio).

- **OpenAI / API tương thích:** cấu hình nhiều profile cho hình ảnh/JSON; model tạo giọng đặt riêng. Model mặc định là cấu hình có thể thay đổi, không đảm bảo mọi tài khoản đều có quyền truy cập.
- **Azure Speech:** nhập Speech key, region và chọn giọng tiếng Việt Hoài My hoặc Nam Minh.
- **Windows:** sử dụng SAPI và các giọng đã cài. Danh sách hiển thị ngôn ngữ thực tế; máy có thể chỉ có tiếng Anh.
- **Nhận dạng cục bộ:** cài `.venv\Scripts\python.exe -m pip install -r requirements-local-ai.txt`, chọn Faster Whisper. Model được tải ở lần đầu; tốc độ phụ thuộc máy. Chức năng tùy chọn này chưa được kiểm tra bằng model tải thực tế trong bản bàn giao.
- **Nhận dạng qua API:** `whisper-1` với `verbose_json` và mốc từng từ. Khi thay model, model đó phải hỗ trợ cùng định dạng phản hồi.
- Voice tổng hợp được tạo theo từng câu: mốc câu lấy từ audio thực, mốc từng từ ban đầu là ước lượng. Căn bằng nhận dạng để cải thiện karaoke.
- Voice nhập sẵn chưa nhận dạng: phụ đề phân bổ theo kịch bản, giao diện báo “mốc ước lượng”. Có thể sửa mốc trong **Kịch bản → Chỉnh mốc phụ đề**.
- Chỉnh kịch bản sẽ bỏ chọn voice cũ để tránh đọc sai nội dung. Chọn lại voice hoặc tạo voice mới. Timeline ngắn hơn voice sẽ bị chặn khi xuất để không cắt mất lời.
- Nhạc tự lặp đủ thời lượng, fade out ở cuối, có điều chỉnh mức âm và ducking theo voice. **Âm thanh từ video nguồn** có slider riêng và mặc định tắt.
- Trong **Mẫu dựng**, chọn font đã cài trên máy và vùng an toàn phụ đề tiêu chuẩn, Reels/Shorts hoặc điện ảnh. Nếu dự án mở trên máy thiếu font đã lưu, preflight nêu font được ánh xạ sang fallback hiện có.

## Trợ lý AI

Nhập yêu cầu trong tab **Trợ lý AI**. Model trả đề xuất đổi kịch bản, chữ/cảnh, thời lượng hoặc âm lượng. Bỏ chọn mục không muốn áp dụng; cảnh được so sánh bằng tên và thumbnail trước/sau. Bấm **Xem trước & preflight** trước khi commit. Áp dụng yêu cầu đúng revision và token preview còn hạn; sau đó có thể **Hoàn tác**. Response được xác thực bằng schema, ID lạ hoặc cảnh đã khóa bị từ chối.

Bản duyệt có preview phát/scrub cho các mục đã chọn. Khi đổi kịch bản, preview sẽ gỡ voice hiện tại và cảnh báo cue cần rà soát, giống trạng thái thực tế sau áp dụng. Hoàn tác phục hồi cả kịch bản và voice.

Panel đề xuất hiển thị API/model thực dùng, chuyển dự phòng, số lần gọi, thời gian và usage token nếu provider trả về. Provider call log chỉ lưu metadata không nhạy cảm. Timeout/network/HTTP lỗi tạm thời được retry hữu hạn; hủy ngừng gửi các request tiếp theo. Nhãn cảnh AI cache theo hash ảnh, provider, endpoint, model và prompt; kết quả từ API dự phòng được ghi theo endpoint thực dùng.

Các nút AI chạy dịch vụ thật sau khi cấu hình key. API tương thích tại localhost có thể để trống key nếu server không yêu cầu xác thực. Provider bên ngoài thiếu key sẽ báo lỗi rõ ràng. **Bản bàn giao chưa kiểm tra trực tiếp OpenAI/Azure do không có API key**; adapter và logic áp dụng được kiểm thử bằng phản hồi mô phỏng. Các lời gọi dịch vụ có thể tính phí độc lập với gói ChatGPT/Codex.

## Phạm vi thực tế của bản 1.0

- Đã triển khai hai luồng dựng, lưu/mở dự án, thư viện cảnh và template, nhập voice/nhạc, TTS, ASR, phụ đề SRT/ASS, chỉnh timeline, AI hỗ trợ và xuất MP4 H.264/AAC.
- Dựng lại từ MP4 là **ước lượng bố cục**. Video đã xuất không còn các lớp gốc; logo phức tạp cần file ảnh riêng và motion graphics phức tạp/3D không được phục hồi tự động. Font render lấy từ font đã cài; font bị thiếu sẽ dùng fallback và xuất cảnh báo preflight.
- Cảnh được lấy mẫu hình ảnh và nhận diện bằng histogram; có thể bỏ sót chuyển cảnh mềm hoặc hành động ngắn. AI chọn cảnh cần duyệt nội dung bằng mắt, nhất là khi nguồn chứa đối tượng giống nhau.
- Chuyển cảnh hỗ trợ cắt thẳng, mờ qua nền và crossfade có overlap; lớp chữ/ảnh hỗ trợ hiện dần và trượt vào. Chưa có bám chủ thể tự động.
- Preview dùng proxy 720p cho video lớn. Clip render trung gian được cache theo nguồn, trim, tốc độ, crop, lớp, font và cấu hình encode. Encoder phần cứng được probe trước; lỗi thiết bị tại encode clip, join hoặc encode cuối được thử lại một lần bằng CPU. Hủy và lỗi đầu vào không kích hoạt retry này; bản xuất ghi encoder thực dùng.
- Khi ô mẫu dài hơn cảnh nguồn, render giữ khung cuối để đủ thời lượng và planner đưa cảnh báo. Có thể thay nguồn, rút ô hoặc chỉnh tốc độ.
- Xem nhanh trong trình duyệt minh họa bố cục, crossfade và âm thanh nguồn; **MP4 xem thử** là kết quả chính xác của font, âm thanh trộn, hiệu ứng và karaoke. Xem nhanh không mô phỏng ducking hay toàn bộ hiệu ứng.
- Ứng dụng một người dùng, bind `127.0.0.1`. Chưa có tài khoản, chia sẻ nhóm, xuất bản mạng xã hội hoặc hạ tầng server nhiều người dùng.

## Dữ liệu và bảo mật cục bộ

`data/projects/<id>/` chứa tư liệu gốc, ảnh xem trước, dự án, lịch sử và các bản xuất; `data/jobs/` lưu trạng thái job. Đóng/mở chương trình không mất dự án. Không mở trực tiếp thư mục data qua static web server. API chỉ phục vụ file thuộc dự án theo ID, có kiểm tra origin/host và không thực thi chuỗi shell từ nội dung người dùng.

Bản nháp chỉnh sửa tạm được lưu trong local storage của trình duyệt đang dùng; media và trạng thái job lưu trong thư mục dữ liệu ClipForge.

`data/settings.json` lưu key trên máy; API đọc cài đặt chỉ trả dấu che key. **Không chia sẻ thư mục data/settings.json**. Có thể dùng biến môi trường `OPENAI_API_KEY` và `AZURE_SPEECH_KEY` để không ghi key vào file. `CLIPFORGE_DATA` đổi nơi lưu dữ liệu; `FFMPEG_BINARY` chọn FFmpeg khác.

Khi gọi AI: mô tả/ảnh đại diện, kịch bản hoặc audio tương ứng được gửi đến nhà cung cấp đã cấu hình. Dựng cơ bản bằng media sẵn có hoạt động trên máy và không cần API. Tư liệu mẫu tích hợp là hình minh họa và nhạc tổng hợp do mã nguồn tạo.

Gỡ tư liệu là thao tác mềm; file còn trên đĩa để giữ lịch sử. Mở **Dung lượng** để xem media, cache, bản xuất, lịch sử và template theo dung lượng. Có thể xem trước rồi dọn cache tái tạo; dự án được chuyển vào thùng rác trước khi xóa hẳn, và có thể phục hồi từ trang này.

## Backup và thư viện template

Trong thanh trên cùng, mở **Dung lượng** để tải backup ZIP của dự án hoặc nhập backup vào một dự án mới. ZIP gồm snapshot `project.json`, manifest schema và SHA-256 cùng media của dự án; có thể chọn kèm bản xuất và lịch sử undo/redo. Khi nhập, ClipForge giải nén vào staging, kiểm tra đường dẫn, giới hạn kích thước và checksum, rồi tạo project/asset/scene ID mới trước khi công bố dự án. Cài đặt và API key không nằm trong backup.

Mở **Template** để xem thumbnail theo tỷ lệ khung, xem trước rồi áp dụng template. Hình và logo trong preview giữ tỷ lệ; sau khi áp dụng, vào tab **Mẫu dựng** để chỉnh khung, chữ và lớp. Template đã lưu có thể đổi tên, nhân bản hoặc xóa; template thiếu ảnh sẽ yêu cầu ánh xạ ảnh thay thế trong dự án.

## Cấu trúc mã nguồn

```text
backend/
  main.py       REST API, nhập file, các thao tác dự án
  models.py     Schema và giới hạn dữ liệu
  store.py      Lưu nguyên tử, lịch sử, cài đặt
  jobs.py       Tác vụ nền, tiến độ, hủy
  media.py      Đọc metadata, phân cảnh, thumbnail
  planner.py    Phân bổ lời đọc và chọn cảnh
  providers.py  AI, TTS, ASR
  render.py     FFmpeg, lớp đồ họa, phụ đề, trộn âm
  demo.py       Tạo dự án mẫu trên máy
frontend/src/   Giao diện React
frontend/dist/  Giao diện đã build để chạy ngay
tests/          Kiểm thử API và xuất video thực
launch.py       Bộ khởi động/cài môi trường
```

API có tài liệu tại `/docs` khi chương trình chạy. Phát triển giao diện: `npm run dev` trong `frontend`; backend chạy cổng 8765. Build xong cần khởi động lại backend nếu trước đó chưa tồn tại `frontend/dist`.

## Kiểm thử

```powershell
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest tests -q
cd frontend
npm run test
npm run lint
npm run build
```

Kiểm thử tự động bao gồm: lưu/hoàn tác/làm lại và xung đột revision; phục hồi job gián đoạn; hủy job đang chờ; upload staging và probe chạy nền trong khi health/job polling vẫn đáp ứng; kiểm tra Host/Origin; che key; nhập file có tên bất thường; phân cảnh/lập timeline; template co thời lượng; chặn cắt voice; ghi đè phụ đề; xuất video thật có nhạc, voice tổng hợp tín hiệu, lớp chữ tiếng Việt và karaoke; kiểm tra phản hồi AI.

Hồi quy M3–M5 nằm trong `tests/test_m345_review.py`: lịch sử có tên snapshot cũ, lỗi/hủy upload và lỗi ghi đĩa, backup không chặn lưu/polling/cancel dự án khác, ZIP kèm exports/history, import/trash/template và tổng dung lượng. `frontend/tests` dùng Node test runner, fake timer và harness thực thi hook/component từ source để kiểm tra autosave/flush, dữ liệu server sau normalize, draft hai tab/reload/duplicate tab và trim nhiều pointermove. Đây là test logic frontend; nghiệm thu thao tác thực trên browser được ghi riêng trong `VALIDATION.md`.

## Tài liệu kỹ thuật tham khảo

- [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [OpenAI speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- [OpenAI transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [Azure Speech REST](https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech)

Chỉ dùng video, hình, nhạc và giọng mà bạn có quyền sử dụng. Cắt ngắn hay đổi giọng không tự tạo quyền sử dụng tư liệu.
