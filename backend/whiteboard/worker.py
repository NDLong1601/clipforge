"""Render one hand-drawn scene in a child process.

Run as a script (``python worker.py job.json``) so a cancelled export can kill
the whole drawing process. The vendored renderer is unchanged; only its
cv2.VideoWriter is swapped for an ffmpeg pipe holding the exact frame budget.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"


def main(config_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    sys.path.insert(0, str(VENDOR))
    import cv2
    import render_stream_whiteboard as wb
    import stream_render as sr

    frames = int(config["frames"])
    width, height, fps = int(config["width"]), int(config["height"]), int(config["fps"])

    class Writer:
        def __init__(self, path, fourcc, rate, size):
            self.count = 0
            self.process = subprocess.Popen([
                config["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{size[0]}x{size[1]}", "-r", str(rate),
                "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "15",
                "-pix_fmt", "yuv420p", str(path),
            ], stdin=subprocess.PIPE)

        def isOpened(self):
            return self.process.poll() is None

        def write(self, frame):
            # Upstream adds a minimum final gaze; keep the scene's exact frame budget.
            if self.count < frames:
                self.process.stdin.write(frame.tobytes())
                self.count += 1

        def release(self):
            self.process.stdin.close()
            status = self.process.wait()
            if status or self.count != frames:
                raise RuntimeError(f"Scene encoder failed: status={status}, frames={self.count}/{frames}")

    cv2.setNumThreads(2)
    annotation = json.loads(Path(config["annotation"]).read_text(encoding="utf-8"))
    settings = sr.Config(
        fps=fps, cap_long_edge=max(width, height), grid_edge=12, sample_step=3, brush_radius=28,
        ink_reveal_radius=3, target_hand_height=round(min(width, height) * 320 / 1080),
        canvas_hex=config["background"], ink_path_mode="grid", color_fill="contour-wipe", pause_mode="light",
    )
    renderer = wb.RegionStreamRenderer(sr._imread_any(Path(config["image"])), annotation, settings,
                                       Path(config["hand"]), False)
    if (renderer.out_w, renderer.out_h) != (width, height):
        raise RuntimeError("Renderer changed the canvas size")
    cv2.VideoWriter = Writer
    renderer.render_to(Path(config["output"]), annotation["sceneDurationMs"])


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as exc:  # The parent shows the last lines of stderr.
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
