# M0 — Baseline và bộ kiểm tra hồi quy

Ngày ghi nhận: 30/09/2026 (Asia/Saigon).

## Ranh giới source

Source nằm tại `C:\Users\PC\Documents\clipforge-source\clipforge`, nhưng Git
nhận thư mục cha `C:\Users\PC` làm repository. ClipForge chưa được track trong
repository đó; `HEAD` của repo cha không phải phiên bản ClipForge. Không tạo
branch hoặc commit tại repo cha để tránh đưa thay đổi của ứng dụng vào lịch sử
cùng dữ liệu cá nhân khác. Fingerprint source sản phẩm ở JSON baseline nhận diện
phần backend/frontend thay cho một commit ClipForge.

## Môi trường đã đo

- Windows 11 build 26200; CPU `Intel64 Family 6 Model 141 Stepping 1, GenuineIntel`, 12 logical CPU, 15.8 GB RAM.
- Python 3.13.7; dependency từ `requirements-lock.txt`.
- Node.js 24.14.0, npm 11.9.0; lock có React 19.3.0, Vite 7.3.6, `@vitejs/plugin-react` 4.7.0.
- FFmpeg 7.1 từ `imageio-ffmpeg==0.6.0` trong `.venv`; không có FFmpeg riêng trong `PATH`.
- SHA-256 lock hiện tại: `requirements-lock.txt` — `91004176b86ee755c8d4a5b8302afcf0d1529bd82dfd2b8dc6df67ef1e966a13`; `frontend/package-lock.json` — `715367905fcf2208520a9ab710112fbdd4c5f8b91bff84b970b973fd2da0e559`.
- Fingerprint backend/frontend trong baseline: `fa1dfb6d2cdd7a0235b1fbe510a1705ebfedd13fe67fd6dbd44525e67d152e74`.

## Kết quả

- Trước khi thêm harness M0: `14 passed` trong 19.93 giây.
- Sau khi thêm fixture và hồi quy: `18 passed, 7 xfailed` trong 20.51 giây. Bảy `xfail` nghiêm ngặt tương ứng bảy lỗi đã xác nhận trong `CODE_REVIEW.md`; assertion đã mô tả kết quả mong muốn. Khi sửa lỗi ở M1/M2, bỏ marker `xfail` của ca đó. `strict=True` khiến XPASS không bị coi là đạt âm thầm.
- `npm ci`: 67 package cài, audit báo 0 lỗ hổng. `npm run build`: thành công với Vite 7.3.6 (9.26 giây).
- Bộ media tổng hợp sinh thành công; manifest xác nhận VFR có ba khoảng frame và MOV có display rotation 90°. WAV voice/music là tone kiểm thử ổn định, không phải giọng tiếng Việt tự nhiên.

| Đoạn việc | Thời gian | Peak RAM tiến trình Python + FFmpeg | Đầu ra |
| --- | ---: | ---: | --- |
| Sinh video stress tổng hợp 60 giây, 240×426, 15 fps | 0.61 s | 108.8 MB | H.264, ultrafast CRF 30 |
| Phân tích theo thiết lập hiện tại | 5.13 s | 129.3 MB | 20 cảnh, 20 thumbnail / 399,777 B |
| Render preview 5 giây, dọc 720p | 1.80 s | 283.7 MB | MP4 327,775 B; thư mục export sau cleanup 336,685 B |

`cache_bytes` đo được là 0; thumbnail hiện nằm trong `thumbs/`, được đo riêng.
RAM được lấy mẫu working set của process tree trên Windows, gồm Python và các
tiến trình FFmpeg còn sống. Các số đo chỉ so sánh được trên cùng máy, cùng cấu
hình input và lệnh bên dưới; đây chưa phải benchmark cho video dài thực tế.

## Fixture và phạm vi media

`tests/fixtures/` giữ JSON nhỏ cho dự án cũ, template có logo, phụ đề đã sửa,
JSON dự án hỏng và hai bản chỉnh sửa tab. Fixture 100/101 cảnh, source 30 fps,
voice/music và PNG trong suốt được dựng trong `tmp_path`. `tests/generate_corpus.py`
tạo thêm video dọc/ngang, VFR, MOV xoay, video stress 60 giây, WAV tone và logo
PNG tại thư mục output chỉ định. Không lưu file media hoặc kết quả render vào repo.

Workspace hiện không có video thực hoặc bản ghi voice tiếng Việt được duyệt để
dùng làm acceptance media. Khi có file phù hợp, đưa đường dẫn vào hai cờ
`--real-video` và `--vietnamese-voice`; script sẽ copy vào corpus tạm và thêm
probe/hash vào manifest. Điều này giữ bộ test tự động ổn định mà vẫn cho phép
nghiệm thu bằng media thật.

## Lệnh lặp lại

Từ thư mục `clipforge` trong PowerShell:

```powershell
python launch.py --setup-only
.venv\Scripts\python.exe -m pytest -q
Push-Location frontend
npm ci
npm run build
Pop-Location
$corpus = Join-Path $env:TEMP 'clipforge-m0-corpus'
.venv\Scripts\python.exe tests\generate_corpus.py --output $corpus
$report = Join-Path $env:TEMP 'clipforge-m0-baseline.json'
.venv\Scripts\python.exe tests\benchmark_baseline.py --output $report
```

Để thêm media thật đã được phép dùng, truyền `--real-video <đường-dẫn>` và/hoặc
`--vietnamese-voice <đường-dẫn>` vào lệnh tạo corpus. Báo cáo benchmark cùng
media/render do hai script tạo nằm ngoài source trong `%TEMP%`.
