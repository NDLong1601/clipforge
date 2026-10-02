import { clipStarts } from './timelineMath.js';

export function residentScenes(clips, cursor, selected) {
  const starts = clipStarts(clips);
  const entries = clips.map((clip, index) => ({ clip, index, start: starts[index] }));
  let active = entries.filter(
    ({ clip, start }) => cursor >= start && cursor < start + clip.duration,
  );
  if (!active.length && entries.length)
    active = [entries[Math.max(0, Math.min(selected, entries.length - 1))]];
  const next = entries[(active.at(-1)?.index ?? -1) + 1];
  return { active, resident: next ? [...active, next] : active };
}

export function sourceMasks(project, clip, asset) {
  const mode =
    clip.text_mode && clip.text_mode !== 'inherit' ? clip.text_mode : project.source_text_mode;
  if (!mode || mode === 'off') return [];
  const scene = asset.scenes?.find((item) => item.id === clip.scene_id);
  const regions =
    clip.text_regions_override || clip.text_regions?.length
      ? clip.text_regions || []
      : scene?.text_regions || [];
  return regions.map((region) => ({ ...region, mode }));
}

export function maskGeometry(region, asset, clip, viewportWidth, viewportHeight) {
  const scale = (clip.fit === 'contain' ? Math.min : Math.max)(
    viewportWidth / Math.max(1, asset.width),
    viewportHeight / Math.max(1, asset.height),
  );
  const width = asset.width * scale,
    height = asset.height * scale;
  const x = (viewportWidth - width) * (clip.fit === 'contain' ? 0.5 : clip.crop_x);
  const y = (viewportHeight - height) * (clip.fit === 'contain' ? 0.5 : clip.crop_y);
  return {
    left: `${((x + region.x * width) / viewportWidth) * 100}%`,
    top: `${((y + region.y * height) / viewportHeight) * 100}%`,
    width: `${((region.w * width) / viewportWidth) * 100}%`,
    height: `${((region.h * height) / viewportHeight) * 100}%`,
  };
}
