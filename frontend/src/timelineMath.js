export function overlapBefore(clips, index) {
  if (index <= 0 || index >= clips.length) return 0;
  const clip = clips[index];
  return clip.transition === 'crossfade' ? Number(clip.transition_duration || 0.4) : 0;
}

export function clipStarts(clips) {
  const starts = [];
  let end = 0;
  for (let index = 0; index < clips.length; index += 1) {
    const clip = clips[index];
    const start = index === 0 ? 0 : end - overlapBefore(clips, index);
    starts.push(Math.max(0, start));
    end = start + Number(clip.duration || 0);
  }
  return starts;
}

export function timelineDuration(clips) {
  if (!clips.length) return 0;
  const starts = clipStarts(clips);
  const last = clips.length - 1;
  return starts[last] + Number(clips[last].duration || 0);
}

export function clipAtTime(clips, time) {
  const starts = clipStarts(clips);
  for (let index = clips.length - 1; index >= 0; index -= 1) {
    if (time >= starts[index] && time < starts[index] + clips[index].duration) return index;
  }
  return Math.max(0, clips.length - 1);
}

export function captionWindows(clips) {
  const starts = clipStarts(clips);
  return clips.map((clip, index) => ({
    start: starts[index],
    end: Math.min(starts[index] + clip.duration, starts[index + 1] ?? Infinity),
  }));
}

// Match the renderer's override ranges, word timing and caption grouping.
export function timelineCaptionGroups(clips, cues, maxWords = 6) {
  const windows = captionWindows(clips);
  const overrides = clips.flatMap((clip, index) =>
    clip.caption && windows[index].end > windows[index].start
      ? [{ ...windows[index], text: clip.caption }]
      : [],
  );
  const base = cues.flatMap((cue) => {
    let ranges = [[cue.start, cue.end]];
    for (const override of overrides) {
      ranges = ranges.flatMap(([start, end]) =>
        [
          [start, Math.min(end, override.start)],
          [Math.max(start, override.end), end],
        ].filter(([left, right]) => right > left),
      );
    }
    return ranges.map(([start, end]) => ({ start, end, text: cue.text }));
  });
  base.push(...overrides);
  base.sort((left, right) => left.start - right.start);
  const boundaries = new Set(overrides.flatMap((cue) => [cue.start, cue.end]));
  const groups = [];
  let current = [];
  const flush = () => {
    if (!current.length) return;
    groups.push({
      start: current[0].start,
      end: current.at(-1).end,
      text: current.map((word) => word.text).join(' '),
      words: current,
    });
    current = [];
  };
  for (const cue of base) {
    if (boundaries.has(cue.start)) flush();
    const parts = cue.text.trim().split(/\s+/u).filter(Boolean);
    const weights = parts.map((word) => Math.max(1, Array.from(word).length));
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    let position = cue.start;
    for (let index = 0; index < parts.length; index += 1) {
      if (
        current.length &&
        (current.length >= maxWords ||
          position - current.at(-1).end > 0.45 ||
          /[.!?。]$/u.test(current.at(-1).text))
      )
        flush();
      const end = position + ((cue.end - cue.start) * weights[index]) / total;
      current.push({ start: position, end, text: parts[index] });
      position = end;
    }
  }
  flush();
  return groups;
}
