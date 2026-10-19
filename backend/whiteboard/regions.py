"""Drawing regions and reveal timing for one prepared scene canvas.

The kit leaves region authoring to Codex. Here regions are found from the
artwork itself: ink blobs are grouped into a few reading-order vignettes so
the hand draws separate objects one after another.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

MAX_REGIONS = 6
INK_GRAY = 140  # The kit's coverage check counts pixels darker than this as ink.
MAX_UNCOVERED = 0.005


def ink_mask(canvas_rgb, background):
    """Pixels that differ visibly from the flat canvas colour."""
    bg = np.array(background, dtype=np.int16)
    diff = np.abs(canvas_rgb.astype(np.int16) - bg).sum(axis=2)
    return diff > 60


def _gap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(0, max(ax0, bx0) - min(ax1, bx1))
    dy = max(0, max(ay0, by0) - min(ay1, by1))
    return math.hypot(dx, dy)


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def find(canvas_rgb, background, box):
    """Return pixel rectangles (x, y, w, h) inside ``box`` in drawing order."""
    height, width = canvas_rgb.shape[:2]
    bx, by, bw, bh = box
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[by:by + bh, bx:bx + bw] = ink_mask(canvas_rgb[by:by + bh, bx:bx + bw], background)
    if not mask.any():
        return [(bx, by, bw, bh)]
    # Close small gaps so strokes of one object form one blob.
    radius = max(5, round(min(bw, bh) * 0.025))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    blobs = cv2.dilate(mask, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
    boxes = [(int(x), int(y), int(x + w), int(y + h)) for x, y, w, h, _ in stats[1:count]]
    minimum = bw * bh * 0.004
    # Merge specks into their nearest neighbour, then merge closest pairs down to the cap.
    while len(boxes) > 1:
        small = [index for index, item in enumerate(boxes) if _area(item) < minimum]
        if len(boxes) <= MAX_REGIONS and not small:
            break
        if small:
            first = min(small, key=lambda index: _area(boxes[index]))
            second = min((index for index in range(len(boxes)) if index != first),
                         key=lambda index: _gap(boxes[first], boxes[index]))
        else:
            first, second = min(((i, j) for i in range(len(boxes)) for j in range(i + 1, len(boxes))),
                                key=lambda pair: (_gap(boxes[pair[0]], boxes[pair[1]]),
                                                  _area(_union(boxes[pair[0]], boxes[pair[1]]))))
        merged = _union(boxes[first], boxes[second])
        boxes = [item for index, item in enumerate(boxes) if index not in (first, second)] + [merged]
    # Reading order: rows top to bottom, then left to right inside a row.
    row = max(1, bh / 3)
    boxes.sort(key=lambda item: (round(((item[1] + item[3]) / 2 - by) / row), item[0]))
    pad = max(6, radius // 2)
    result = []
    for x0, y0, x1, y1 in boxes:
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(width, x1 + pad), min(height, y1 + pad)
        result.append((x0, y0, x1 - x0, y1 - y0))
    return result


def uncovered(canvas_rgb, rects):
    """Fraction of dark ink outside every region, and the box around it."""
    gray = cv2.cvtColor(canvas_rgb, cv2.COLOR_RGB2GRAY)
    ink = gray < INK_GRAY
    covered = np.zeros(ink.shape, dtype=bool)
    for x, y, w, h in rects:
        covered[y:y + h, x:x + w] = True
    outside = ink & ~covered
    total = int(np.count_nonzero(ink))
    missing = int(np.count_nonzero(outside))
    if not missing:
        return 0.0, None
    ys, xs = np.nonzero(outside)
    pad = 8
    x0, y0 = max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad)
    x1, y1 = min(ink.shape[1], int(xs.max()) + pad + 1), min(ink.shape[0], int(ys.max()) + pad + 1)
    return missing / max(1, total), (x0, y0, x1 - x0, y1 - y0)


def timing(rects, duration_ms):
    """Sequential reveals sized by sqrt(area), finishing ~0.5 s before the cut."""
    if not rects:
        return []
    hold = min(500, duration_ms * 0.25)
    budget = max(min(duration_ms - hold, max(600, duration_ms * 0.65)), duration_ms * 0.5)
    weights = [math.sqrt(max(1, w * h)) for _, _, w, h in rects]
    total = sum(weights)
    start, result = 0, []
    for index, weight in enumerate(weights):
        length = max(1, round(budget * weight / total))
        if index == len(weights) - 1:
            length = max(1, round(budget) - start)
        result.append((start, length))
        start += length
    return result
