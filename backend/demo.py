import math, wave
import numpy as np
from PIL import Image, ImageDraw
from . import media, planner, store


def build_demo(p, job):
    d = store.project_dir(p.id) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    themes = [
        ("Đồi xanh", "#c5d4c6", "#174c43", "núi đồi thiên nhiên chuyến đi bình yên"),
        ("Biển sớm", "#f3bda2", "#226b80", "biển sóng nước bầu trời"),
        ("Phố nhỏ", "#e9cda7", "#a76345", "phố nhà đường đi du lịch"),
        ("Hoàng hôn", "#e9a18f", "#563e69", "hoàng hôn chiều trời mặt trời"),
        ("Rừng yên", "#d7dfb3", "#325c42", "rừng cây xanh khoảng lặng"),
    ]
    for i, (name, sky, ground, tags) in enumerate(themes):
        job.update(5 + i * 14, "Tạo tư liệu minh họa: " + name)
        im = Image.new("RGB", (720, 1280), sky)
        dr = ImageDraw.Draw(im)
        dr.ellipse((420, 140, 600, 320), fill="#fff0cf")
        for k in range(4):
            y = 550 + k * 140
            points = (
                [(0, 1280), (0, y)]
                + [(x, y - 90 * math.sin(x / 150 + k)) for x in range(0, 721, 10)]
                + [(720, 1280)]
            )
            dr.polygon(points, fill=ground if k % 2 == 0 else "#83a59a")
        if i == 2:
            for k in range(5):
                x = 50 + k * 135
                dr.rectangle((x, 500 - k % 2 * 100, x + 100, 960), fill="#edc59a")
                dr.polygon(
                    [
                        (x - 10, 500 - k % 2 * 100),
                        (x + 50, 400 - k % 2 * 100),
                        (x + 110, 500 - k % 2 * 100),
                    ],
                    fill="#9a4938",
                )
        if i == 4:
            for k in range(7):
                x = k * 110
                dr.rectangle((x + 35, 750, x + 45, 1150), fill="#30402e")
                dr.polygon(
                    [(x - 25, 850), (x + 40, 570), (x + 105, 850)], fill="#255143"
                )
        image = d / f"demo_{i}.png"
        im.save(image)
        video = d / f"demo_{i}.mp4"
        media.run_ff(
            [
                "-loop",
                "1",
                "-i",
                image,
                "-vf",
                "zoompan=z=1.04+0.0005*on:x=iw/2-iw/zoom/2:y=ih/2-ih/zoom/2:d=180:s=360x640:fps=30",
                "-t",
                "6",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                video,
            ],
            job=job,
        )
        a = media.register(p.id, video, name + ".mp4", "source", job)
        a.tags = tags
        media.analyze(p.id, a, job)
        p.assets.append(a)
    sr = 48000
    t = np.arange(sr * 40) / sr
    sound = sum(
        np.sin(2 * np.pi * f * t) * 0.024 for f in [130.81, 164.81, 196, 261.63]
    )
    sound *= (
        np.minimum(1, t)
        * np.minimum(1, (40 - t))
        * (0.65 + 0.35 * np.sin(2 * np.pi * 0.2 * t) ** 2)
    )
    music = d / "demo_music.wav"
    with wave.open(str(music), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes((sound * 32767).astype("<i2").tobytes())
    a = media.register(p.id, music, "Nhạc minh họa tổng hợp.wav", "music")
    p.assets.append(a)
    p.music_id = a.id
    p = planner.plan(p, False, job)
    p.warnings.append(
        "Dự án mẫu dùng hình minh họa và nhạc tổng hợp. Chưa có giọng đọc; bạn có thể nhập voice hoặc tạo giọng trong tab Âm thanh."
    )
    return p
