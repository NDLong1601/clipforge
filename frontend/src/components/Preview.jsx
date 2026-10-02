import { Clapperboard } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { aid, exportUrl, mediaUrl } from '../api/client.js';
import { maskGeometry, residentScenes, sourceMasks } from '../previewMedia.js';
import { layerText } from '../templateContent.js';
import { overlapBefore, timelineCaptionGroups } from '../timelineMath.js';

export function Preview({
  p,
  selected,
  preview,
  play,
  setPlay,
  cursor,
  setCursor,
  duration,
  onSelect,
  previewIsStale,
  onReturnLive,
}) {
  const videoRefs = useRef(new Map()),
    voice = useRef(),
    music = useRef(),
    effects = useRef();
  const [size, setSize] = useState(280);
  const canvas = useRef();
  const posRef = useRef(cursor);
  posRef.current = cursor;
  const { active: activeEntries, resident: residentEntries } = residentScenes(
    p.clips,
    cursor,
    selected,
  );
  const incoming = activeEntries.at(-1);
  const index = incoming?.index ?? selected;
  const c = incoming?.clip || p.clips[selected];
  const a = c && aid(p, c.asset_id);
  const transition = incoming ? overlapBefore(p.clips, incoming.index) : 0;
  const progress = transition
    ? Math.max(0, Math.min(1, (cursor - incoming.start) / transition))
    : 1;
  const entryGain = (entry) =>
    activeEntries.length > 1 ? (entry.index === incoming.index ? progress : 1 - progress) : 1;
  const sceneOpacity = (entry) =>
    activeEntries.length > 1 && entry.index === incoming.index ? progress : 1;
  const activeSignature = residentEntries
    .map((entry) => `${entry.clip.id}:${entry.index}`)
    .join('|');
  const t = p.template;
  useEffect(() => {
    const observer = new ResizeObserver((entries) => setSize(entries[0].contentRect.width));
    if (canvas.current) observer.observe(canvas.current);
    return () => observer.disconnect();
  }, [p.id, p.aspect, preview]);
  useEffect(() => {
    if (!play) return;
    let frame, last;
    const tick = (time) => {
      if (last) {
        const next = posRef.current + (time - last) / 1000;
        if (next >= duration) {
          setCursor(duration);
          setPlay(false);
          return;
        }
        setCursor(next);
      }
      last = time;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [play, duration]);
  useEffect(() => {
    if (play) onSelect(index);
  }, [index, play]);
  useEffect(() => {
    for (const [clipId, video] of videoRefs.current) {
      const entry = activeEntries.find((item) => item.clip.id === clipId);
      if (!entry || preview) {
        video.pause();
        const upcoming = residentEntries.find((item) => item.clip.id === clipId);
        if (
          upcoming &&
          video.readyState > 0 &&
          Math.abs(video.currentTime - upcoming.clip.source_start) > 0.03
        )
          video.currentTime = upcoming.clip.source_start;
        continue;
      }
      const asset = aid(p, entry.clip.asset_id);
      const clipLocal = cursor - entry.start;
      const wanted = entry.clip.source_start + Math.max(0, clipLocal) * entry.clip.speed;
      const scene = asset?.scenes.find((item) => item.id === entry.clip.scene_id);
      const end = scene?.end || asset?.duration || 999;
      video.playbackRate = entry.clip.speed;
      video.muted = p.source_volume <= 0;
      video.volume = Math.max(0, Math.min(1, p.source_volume * entryGain(entry)));
      if (Number.isFinite(video.duration) && video.readyState > 0) {
        const time = Math.max(0, Math.min(wanted, end - 0.04));
        if (Math.abs(video.currentTime - time) > (play ? 0.3 : 0.025)) video.currentTime = time;
      }
      if (play && wanted < end - 0.04 && !video.seeking) {
        if (video.paused) video.play().catch(() => {});
      } else video.pause();
    }
  }, [cursor, play, preview, activeSignature, p.source_volume, c?.source_start, c?.speed]);
  useEffect(() => {
    for (const [ref, id, volume] of [
      [voice, p.voice_id, p.voice_volume],
      [music, p.music_id, p.music_volume],
      [effects, 'effects', 1],
    ]) {
      const audio = ref.current;
      if (!audio || !id) continue;
      audio.volume = Math.min(1, volume);
      const time =
        ref === music && Number.isFinite(audio.duration) ? cursor % audio.duration : cursor;
      if (Math.abs(audio.currentTime - time) > 0.4) audio.currentTime = Math.max(0, time);
      if (play && !preview) {
        if (audio.paused) audio.play().catch(() => {});
      } else audio.pause();
    }
  }, [
    cursor,
    play,
    preview,
    p.voice_id,
    p.music_id,
    p.voice_volume,
    p.music_volume,
    p.revision,
    p.sound_effect_volume,
  ]);
  const captionGroups = useMemo(
    () => timelineCaptionGroups(p.clips, p.cues, t.caption.words_per_line),
    [p.clips, p.cues, t.caption.words_per_line],
  );
  const captionBottom = Math.max(
    t.caption.bottom,
    { standard: 0.06, reels: 0.16, cinematic: 0.1 }[t.caption.safe_area || 'standard'],
  );
  const text =
    captionGroups.find((group) => cursor >= group.start && cursor < group.end)?.text || '';
  return (
    <div className="preview-stage">
      {preview ? (
        <video className="rendered-video" controls autoPlay src={exportUrl(p, preview)} />
      ) : (
        <div
          ref={canvas}
          className={'video-canvas aspect-' + p.aspect.replace(':', '-')}
          style={{ background: t.background }}
        >
          {a ? (
            <>
              {residentEntries.map((entry) => {
                const asset = aid(p, entry.clip.asset_id);
                if (!asset) return null;
                const style = {
                  left: t.viewport.x * 100 + '%',
                  top: t.viewport.y * 100 + '%',
                  width: t.viewport.w * 100 + '%',
                  height: t.viewport.h * 100 + '%',
                };
                const clip = entry.clip;
                const local = Math.max(0, cursor - entry.start);
                const layers = [
                  ...t.layers,
                  ...(p.mode === 'template' && t.slot_layers?.length
                    ? t.slot_layers[entry.index % t.slot_layers.length]
                    : []),
                  ...(clip.layers || []),
                ];
                const mediaStyle = {
                  objectFit: entry.clip.fit,
                  objectPosition: `${entry.clip.crop_x * 100}% ${entry.clip.crop_y * 100}%`,
                };
                return (
                  <div
                    className="canvas-scene"
                    key={entry.clip.id}
                    style={{
                      background: t.background,
                      opacity: activeEntries.some((item) => item.index === entry.index)
                        ? sceneOpacity(entry)
                        : 0,
                      pointerEvents: 'none',
                    }}
                  >
                    <div className="video-viewport" style={style}>
                      {asset.media === 'image' ? (
                        <img src={mediaUrl(p, asset.id)} alt={asset.name} style={mediaStyle} />
                      ) : (
                        <video
                          ref={(node) => {
                            if (node) videoRefs.current.set(entry.clip.id, node);
                            else videoRefs.current.delete(entry.clip.id);
                          }}
                          src={mediaUrl(p, asset.id)}
                          muted={p.source_volume <= 0}
                          playsInline
                          preload="auto"
                          onLoadedMetadata={(event) => {
                            const time =
                              entry.clip.source_start +
                              Math.max(0, cursor - entry.start) * entry.clip.speed;
                            if (Number.isFinite(event.currentTarget.duration))
                              event.currentTarget.currentTime = Math.min(
                                time,
                                event.currentTarget.duration - 0.04,
                              );
                          }}
                          style={mediaStyle}
                        />
                      )}
                      {sourceMasks(p, clip, asset).map((region, regionIndex) => (
                        <div
                          key={`mask-${regionIndex}`}
                          className="source-text-mask"
                          style={{
                            ...maskGeometry(
                              region,
                              asset,
                              clip,
                              size * t.viewport.w,
                              size *
                                (p.aspect === '9:16' ? 16 / 9 : p.aspect === '16:9' ? 9 / 16 : 1) *
                                t.viewport.h,
                            ),
                            background: region.mode === 'cover' ? '#111111' : 'transparent',
                            backdropFilter: region.mode === 'blur' ? 'blur(18px)' : undefined,
                          }}
                        />
                      ))}
                    </div>
                    {layers.map((l, i) => (
                      <div
                        className="canvas-layer"
                        key={l.id + '-' + i}
                        style={{
                          left:
                            (l.x -
                              (l.animation === 'slide' ? l.w * Math.max(0, 1 - local / 0.25) : 0)) *
                              100 +
                            '%',
                          top: l.y * 100 + '%',
                          width: l.w * 100 + '%',
                          height: l.h * 100 + '%',
                          fontSize: Math.max(8, (l.size * size) / 1080),
                          fontFamily: l.font_family || 'Arial',
                          color: l.color,
                          opacity:
                            l.opacity * (l.animation === 'fade' ? Math.min(1, local / 0.25) : 1),
                          background:
                            l.kind === 'rect' || l.kind === 'circle' ? l.color : 'transparent',
                          borderRadius: l.kind === 'circle' ? '50%' : 0,
                        }}
                      >
                        {l.kind === 'text' ? (
                          layerText(p, l, clip)
                        ) : l.kind === 'image' && l.asset_id ? (
                          <img src={mediaUrl(p, l.asset_id)} />
                        ) : null}
                      </div>
                    ))}
                  </div>
                );
              })}
              {t.caption.enabled && text && (
                <div
                  className="canvas-caption"
                  style={{
                    bottom: captionBottom * 100 + '%',
                    fontFamily: t.caption.font_family || 'Arial',
                    fontSize: Math.max(9, (t.caption.font_size * size) / 1080),
                    color: t.caption.color,
                    textShadow:
                      [1, 3, 5].reduce(
                        (sum, i) => sum + parseInt(t.caption.color.slice(i, i + 2), 16),
                        0,
                      ) < 360
                        ? '0 1px 1px #ffffff'
                        : '0 1px 2px #000,1px 0 2px #000,-1px 0 2px #000',
                  }}
                >
                  {text}
                </div>
              )}
            </>
          ) : (
            <div className="canvas-placeholder">
              <Clapperboard size={36} />
              <span>
                Câu chuyện bắt đầu
                <br />
                từ cảnh đầu tiên.
              </span>
              <small>
                {p.aspect} · {p.target_duration}s
              </small>
            </div>
          )}
        </div>
      )}
      {preview && (
        <div className={`render-revision ${previewIsStale ? 'stale' : ''}`} role="status">
          <span>
            {previewIsStale ? 'Bản xem thử cũ' : 'Bản xem thử hiện tại'} · revision{' '}
            {preview.project_revision ?? 'không rõ'}
            {previewIsStale ? ` → dự án ${p.revision}` : ''}
          </span>
          {previewIsStale && <button onClick={onReturnLive}>Xem nhanh bản đang chỉnh</button>}
        </div>
      )}
      {p.voice_id && <audio ref={voice} src={mediaUrl(p, p.voice_id)} />}{' '}
      {p.music_id && <audio ref={music} loop src={mediaUrl(p, p.music_id)} />}
      {!!duration && (
        <audio
          ref={effects}
          preload="auto"
          src={`/api/projects/${p.id}/sound-effects/track.wav?revision=${p.revision}`}
        />
      )}
    </div>
  );
}
