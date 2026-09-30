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

## Nhiều API key, model và chuyển dự phòng

1. Mở chương trình rồi bấm **Cài đặt API** ở góc trên bên phải. Chọn **Gemini**, **OpenAI** hoặc **API tương thích OpenAI**, rồi bấm **Thêm API**. Có thể thêm nhiều khóa, kể cả nhiều khóa cùng một nhà cung cấp.
2. Chọn một cấu hình trong danh sách, sửa tên/model và dán khóa vào ô **API key**. Với API tương thích OpenAI, nhập thêm **API base URL**. Bấm **Lưu và kiểm tra API này** để xác nhận riêng cấu hình đó; thao tác này gọi dịch vụ thật và có thể tính phí.
3. Bấm **Dùng chính** để chọn API/model ưu tiên. Nút ↑/↓ sắp xếp thứ tự các cấu hình dự phòng. Bật **Tự chuyển API khi lỗi** để chương trình thử lần lượt các cấu hình đang bật sau khi API chính lỗi; tắt để chỉ dùng API chính. Cuối cùng bấm **Lưu cài đặt**.
4. Chuyển dự phòng áp dụng cho phân tích cảnh/mẫu, lập kế hoạch bằng AI và trợ lý. Tạo giọng/căn phụ đề dùng cấu hình OpenAI hoặc API tương thích hỗ trợ Speech, Azure Speech hoặc nhận dạng cục bộ; khóa Gemini ở danh sách AI không dùng cho các endpoint Speech này.

Khóa chỉ lưu tại máy trong `data/settings.json`; API đọc cài đặt chỉ trả dấu che khóa. Cấu hình một khóa từ bản cũ được chuyển vào danh sách khi mở lại. Gemini dùng [endpoint tương thích OpenAI của Google](https://ai.google.dev/gemini-api/docs/openai).

## Quy trình tái biên tập

1. Tạo dự án, chọn **Tái biên tập**.
2. Trong **Tư liệu**, nhập 5–10 video ở vai trò **Nguồn dựng**. Có thể nhập thêm ảnh. Mỗi lần tối đa 10 file, mỗi file tối đa 2 GB và 120 phút.
3. Thêm nhãn cho tư liệu hoặc bật **Dùng AI để hiểu nội dung từng cảnh**, rồi bấm **Phân tích**. Công cụ phát hiện điểm đổi cảnh bằng histogram màu và chia cảnh thành các cửa sổ thường dài 2–3 giây. Không cắt cửa sổ qua điểm đổi cảnh đã phát hiện.
4. Dán nội dung vào **Kịch bản**, chọn thời lượng (mặc định 36 giây; hỗ trợ 5–180 giây).
5. Trong **Âm thanh**, nhập voice có sẵn hoặc tạo voice từ kịch bản. Chọn **Căn phụ đề từ voice** nếu cần mốc từng từ chính xác hơn.
6. Bấm **Tự động dựng**. Chế độ cơ bản chọn theo nhãn/từ khóa, độ đa dạng và chất lượng. Chế độ AI sử dụng mô tả hình ảnh để đề xuất cảnh phù hợp từng ý.
7. Chọn cảnh trên timeline để đổi nguồn, mốc cắt, độ dài, tiêu đề, phụ đề, tốc độ, vị trí crop hoặc kiểu chuyển cảnh. Kéo thả cảnh để đổi thứ tự.
8. **Dựng xem thử** xuất MP4 720p. **Xuất video** dùng độ phân giải đã chọn. Tải MP4/SRT từ **Bản xuất gần đây**.

## Quy trình theo format mẫu

1. Nhập video tham chiếu ở vai trò **Video mẫu**; logo/biểu tượng PNG/JPG ở vai trò **Logo / biểu tượng**.
2. Mở **Mẫu dựng**, chọn video và **Phân tích mẫu**.
3. Không bật AI: lấy nhịp cắt/thời lượng các ô, rồi chỉnh bố cục bằng giao diện.
4. Bật AI: gửi tối đa 6 ảnh đại diện tới model để đề xuất khung hình, lớp chữ, hình khối, màu, phụ đề và bố cục theo ô.
5. Duyệt/chỉnh các lớp; dùng `{title}`, `{project}`, `{caption}` để thay nội dung tự động. Có lớp chữ, hình chữ nhật, hình tròn, ảnh/logo; tọa độ tương đối 0–1; hiệu ứng tĩnh, hiện dần, trượt vào.
6. Chọn nguồn và kịch bản mới, tạo voice, **Tự động dựng**. Nhịp các ô được co theo tổng thời lượng. **Lưu mẫu** để tái sử dụng ở dự án khác.

Template JSON có `viewport`, `layers`, `slot_durations`, `slot_layers`, `caption`, `transition`. `slot_layers` cho phép mỗi ô có nhóm lớp riêng. Các preset Toàn khung, Khung biên tập và Nhịp nhanh có sẵn.

## Voice, phụ đề và nhạc

- **OpenAI / API tương thích:** cấu hình nhiều profile cho hình ảnh/JSON; model tạo giọng đặt riêng. Model mặc định là cấu hình có thể thay đổi, không đảm bảo mọi tài khoản đều có quyền truy cập.
- **Azure Speech:** nhập Speech key, region và chọn giọng tiếng Việt Hoài My hoặc Nam Minh.
- **Windows:** sử dụng SAPI và các giọng đã cài. Danh sách hiển thị ngôn ngữ thực tế; máy có thể chỉ có tiếng Anh.
- **Nhận dạng cục bộ:** cài `.venv\Scripts\python.exe -m pip install -r requirements-local-ai.txt`, chọn Faster Whisper. Model được tải ở lần đầu; tốc độ phụ thuộc máy. Chức năng tùy chọn này chưa được kiểm tra bằng model tải thực tế trong bản bàn giao.
- **Nhận dạng qua API:** `whisper-1` với `verbose_json` và mốc từng từ. Khi thay model, model đó phải hỗ trợ cùng định dạng phản hồi.
- Voice tổng hợp được tạo theo từng câu: mốc câu lấy từ audio thực, mốc từng từ ban đầu là ước lượng. Căn bằng nhận dạng để cải thiện karaoke.
- Voice nhập sẵn chưa nhận dạng: phụ đề phân bổ theo kịch bản, giao diện báo “mốc ước lượng”. Có thể sửa mốc trong **Kịch bản → Chỉnh mốc phụ đề**.
- Chỉnh kịch bản sẽ bỏ chọn voice cũ để tránh đọc sai nội dung. Chọn lại voice hoặc tạo voice mới. Timeline ngắn hơn voice sẽ bị chặn khi xuất để không cắt mất lời.
- Nhạc tự lặp đủ thời lượng, fade out ở cuối, có điều chỉnh mức âm và ducking theo voice. Âm thanh gốc của nguồn dựng không đưa vào thành phẩm.

## Trợ lý AI

Nhập yêu cầu trong tab **Trợ lý AI**. Model trả đề xuất đổi kịch bản, chữ/cảnh, thời lượng hoặc âm lượng. Xem đề xuất rồi bấm **Áp dụng**; có **Hoàn tác**. Chỉ các trường đã cho phép mới được áp dụng; model không chạy mã hay lệnh hệ thống. Đề xuất cho phiên bản cũ của dự án bị từ chối để tránh sửa sai cảnh.

Các nút AI chạy dịch vụ thật sau khi cấu hình key. Không có key thì giao diện báo lỗi rõ ràng; không sinh kết quả AI giả. **Bản bàn giao chưa kiểm tra trực tiếp OpenAI/Azure do không có API key**; adapter và logic áp dụng được kiểm thử bằng phản hồi mô phỏng. Các lời gọi dịch vụ có thể tính phí độc lập với gói ChatGPT/Codex.

## Phạm vi thực tế của bản 1.0

- Đã triển khai hai luồng dựng, lưu/mở dự án, thư viện cảnh và template, nhập voice/nhạc, TTS, ASR, phụ đề SRT/ASS, chỉnh timeline, AI hỗ trợ và xuất MP4 H.264/AAC.
- Dựng lại từ MP4 là **ước lượng bố cục**. Video đã xuất không còn các lớp gốc; logo phức tạp cần file ảnh riêng, font mẫu có thể khác. Bản hiện tại dùng Arial cho bản render và không phục hồi tự động motion graphics phức tạp/3D.
- Cảnh được lấy mẫu hình ảnh và nhận diện bằng histogram; có thể bỏ sót chuyển cảnh mềm hoặc hành động ngắn. AI chọn cảnh cần duyệt nội dung bằng mắt, nhất là khi nguồn chứa đối tượng giống nhau.
- Chuyển cảnh hỗ trợ cắt thẳng hoặc mờ qua màu nền; lớp chữ/ảnh hỗ trợ hiện dần và trượt vào. Chưa có chồng hình crossfade hoặc bám chủ thể tự động.
- Khi ô mẫu dài hơn cảnh nguồn, render giữ khung cuối để đủ thời lượng và planner đưa cảnh báo. Có thể thay nguồn, rút ô hoặc chỉnh tốc độ.
- Xem nhanh trong trình duyệt minh họa bố cục và phát media; **MP4 xem thử** là kết quả chính xác của font, âm thanh, hiệu ứng, karaoke. Xem nhanh không mô phỏng ducking hay toàn bộ hiệu ứng.
- Ứng dụng một người dùng, bind `127.0.0.1`. Chưa có tài khoản, chia sẻ nhóm, xuất bản mạng xã hội hoặc hạ tầng server nhiều người dùng.

## Dữ liệu và bảo mật cục bộ

`data/projects/<id>/` chứa tư liệu gốc, ảnh xem trước, dự án, lịch sử và các bản xuất. Đóng/mở chương trình không mất dự án. Không mở trực tiếp thư mục data qua static web server. API chỉ phục vụ file thuộc dự án theo ID, có kiểm tra origin/host và không thực thi chuỗi shell từ nội dung người dùng.

`data/settings.json` lưu key trên máy; API đọc cài đặt chỉ trả dấu che key. **Không chia sẻ thư mục data/settings.json**. Có thể dùng biến môi trường `OPENAI_API_KEY` và `AZURE_SPEECH_KEY` để không ghi key vào file. `CLIPFORGE_DATA` đổi nơi lưu dữ liệu; `FFMPEG_BINARY` chọn FFmpeg khác.

Khi gọi AI: mô tả/ảnh đại diện, kịch bản hoặc audio tương ứng được gửi đến nhà cung cấp đã cấu hình. Dựng cơ bản bằng media sẵn có hoạt động trên máy và không cần API. Tư liệu mẫu tích hợp là hình minh họa và nhạc tổng hợp do mã nguồn tạo.

Gỡ tư liệu là thao tác mềm; file còn trên đĩa để giữ lịch sử. Cache của các tác vụ và bản xuất cũ có thể tăng dung lượng. Khi cần dọn, đóng chương trình và sao lưu rồi xóa các thư mục dự án không còn dùng.

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
npm run build
```

Kiểm thử tự động bao gồm: lưu/hoàn tác và xung đột phiên bản; kiểm tra Host/Origin; che key; nhập file có tên bất thường; phân cảnh/lập timeline; template co thời lượng; chặn cắt voice; ghi đè phụ đề; xuất video thật có nhạc, voice tổng hợp tín hiệu, lớp chữ tiếng Việt và karaoke; hủy tác vụ; kiểm tra phản hồi AI.

## Tài liệu kỹ thuật tham khảo

- [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [OpenAI speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- [OpenAI transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [Azure Speech REST](https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech)

Chỉ dùng video, hình, nhạc và giọng mà bạn có quyền sử dụng. Cắt ngắn hay đổi giọng không tự tạo quyền sử dụng tư liệu.
