import {
  Copy,
  Layers,
  LockKeyhole,
  LockKeyholeOpen,
  Mic2,
  Music2,
  Scissors,
  Sparkles,
  Trash2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { aid, api, fmt, thumbUrl } from '../api/client.js';
import { clipAtTime, clipStarts, timelineDuration as measureTimeline } from '../timelineMath.js';

const LABEL_WIDTH = 42;
const MIN_CLIP_SECONDS = 0.2;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function waveformPath(peaks) {
  const maximum = Math.max(...peaks, 0.0001);
  return peaks
    .map((peak, index) => {
      const half = 2 + Math.sqrt(peak / maximum) * 17;
      return `M${index + 0.5} ${22 - half}V${22 + half}`;
    })
    .join('');
}

function Waveform({ project, asset, width, pps, repeat }) {
  const [peaks, setPeaks] = useState(null);
  const duration = Math.max(0.05, asset.duration || 0.05);
  useEffect(() => {
    const controller = new AbortController();
    setPeaks(null);
    api(
      `/projects/${project.id}/assets/${asset.id}/waveform?max_points=${Math.min(6000, Math.max(64, Math.ceil((duration * pps) / 2)))}`,
      undefined,
      'GET',
      { signal: controller.signal },
    )
      .then((data) => setPeaks(data.peaks))
      .catch((error) => {
        if (error.name !== 'AbortError') setPeaks([]);
      });
    return () => controller.abort();
  }, [project.id, asset.id, duration, pps]);

  if (!peaks?.length)
    return (
      <span className="waveform-loading">{peaks ? 'Không có waveform' : 'Đang phân tích…'}</span>
    );
  const cycleWidth = duration * pps;
  const path = waveformPath(peaks);
  if (repeat) {
    const patternId = `waveform-${asset.id}`;
    return (
      <svg
        aria-hidden="true"
        className="waveform-svg"
        style={{ left: 0, width }}
        viewBox={`0 0 ${width} 44`}
        preserveAspectRatio="none"
      >
        <defs>
          <pattern id={patternId} width={cycleWidth} height="44" patternUnits="userSpaceOnUse">
            <path d={path} transform={`scale(${cycleWidth / peaks.length}, 1)`} />
          </pattern>
        </defs>
        <rect width={width} height="44" fill={`url(#${patternId})`} />
      </svg>
    );
  }
  return (
    <svg
      aria-hidden="true"
      className="waveform-svg"
      style={{ left: 0, width: Math.min(width, cycleWidth) }}
      viewBox={`0 0 ${peaks.length} 44`}
      preserveAspectRatio="none"
    >
      <path d={path} />
    </svg>
  );
}

export function Timeline({
  p,
  selected,
  cursor,
  setCursor,
  onSelect,
  edit,
  locked,
  duration,
  onSplit,
  onDuplicate,
  onDelete,
  onToggleLock,
  useAI,
  setUseAI,
  onPlan,
  setPlay,
}) {
  const [pps, setPps] = useState(64);
  const [drag, setDragState] = useState(null);
  const [scrubbing, setScrubbing] = useState(false);
  const scrollRef = useRef(null);
  const dragRef = useRef(null);
  const currentRef = useRef(null);
  const editRef = useRef(edit);
  const selectRef = useRef(onSelect);
  const cursorRef = useRef(setCursor);
  const viewClips = drag?.clips || p.clips;
  const timelineDuration = Math.max(duration, p.target_duration || 0, 36);
  const totalWidth = Math.max(440, LABEL_WIDTH + timelineDuration * pps);
  const trackDuration = Math.max(duration, 0.1);
  const currentClip = p.clips[selected];
  const tick = pps >= 120 ? 1 : pps >= 78 ? 2 : pps >= 42 ? 5 : 10;
  const ticks = Array.from({ length: Math.ceil(timelineDuration / tick) + 1 }, (_, i) => i * tick);
  const starts = clipStarts(viewClips);

  currentRef.current = { p, pps, duration, viewClips, starts };
  editRef.current = edit;
  selectRef.current = onSelect;
  cursorRef.current = setCursor;

  useEffect(() => {
    const move = (event) => {
      const original = dragRef.current;
      const current = currentRef.current;
      if (!original || !current) return;
      const { p: project, pps: scale } = current;
      const clips = original.baseClips;
      const positions = original.starts;
      const clip = clips[original.index];
      if (!clip) return;
      let delta = (event.clientX - original.startX) / scale;
      delta = Math.round(delta * 30) / 30;
      const originalStart = original.starts[original.index];
      const originalEnd = originalStart + clip.duration;
      const asset = aid(project, clip.asset_id);
      const scene = asset?.scenes.find((item) => item.id === clip.scene_id);
      const snapPoints = [
        ...positions,
        ...positions.map((start, index) => start + clips[index].duration),
      ];
      for (const cue of project.cues) snapPoints.push(cue.start, cue.end);
      let nextClip;
      if (original.side === 'left') {
        const edge = originalStart + delta;
        const close = snapPoints
          .filter((value) => Math.abs(value - originalStart) > 0.001)
          .filter((value) => Math.abs(value - edge) * scale <= 8)
          .sort((a, b) => Math.abs(a - edge) - Math.abs(b - edge))[0];
        if (close !== undefined) delta = close - originalStart;
        const minimumDelta = Math.max(
          MIN_CLIP_SECONDS - clip.duration,
          ((scene?.start || 0) - clip.source_start) / Math.max(clip.speed, 0.01),
        );
        const maximumDelta = clip.duration - MIN_CLIP_SECONDS;
        delta = clamp(delta, minimumDelta, maximumDelta);
        nextClip = {
          ...clip,
          duration: Number((clip.duration - delta).toFixed(6)),
          source_start: Number((clip.source_start + delta * clip.speed).toFixed(6)),
        };
      } else {
        const edge = originalEnd + delta;
        const close = snapPoints
          .filter((value) => Math.abs(value - originalEnd) > 0.001)
          .filter((value) => Math.abs(value - edge) * scale <= 8)
          .sort((a, b) => Math.abs(a - edge) - Math.abs(b - edge))[0];
        if (close !== undefined) delta = close - originalEnd;
        const total = measureTimeline(clips);
        delta = clamp(delta, MIN_CLIP_SECONDS - clip.duration, 180 - total);
        nextClip = { ...clip, duration: Number((clip.duration + delta).toFixed(6)) };
      }
      const previewClips = original.clips.map((item, index) =>
        index === original.index ? nextClip : item,
      );
      const nextDrag = { ...original, clips: previewClips };
      dragRef.current = nextDrag;
      setDragState(nextDrag);
    };
    const finish = () => {
      const completed = dragRef.current;
      if (!completed) return;
      dragRef.current = null;
      setDragState(null);
      editRef.current((next) => {
        next.clips = completed.clips;
      });
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
    };
  }, []);

  const seek = (event) => {
    const lane = scrollRef.current;
    if (!lane) return;
    const time = clamp(
      (event.clientX - lane.getBoundingClientRect().left + lane.scrollLeft - LABEL_WIDTH) / pps,
      0,
      duration,
    );
    const boundaries = [
      ...starts,
      ...starts.map((start, index) => start + viewClips[index].duration),
    ];
    for (const cue of p.cues) boundaries.push(cue.start, cue.end);
    const snap = boundaries
      .filter((value) => Math.abs(value - time) * pps <= 7)
      .sort((a, b) => Math.abs(a - time) - Math.abs(b - time))[0];
    const snapped = snap === undefined ? time : snap;
    cursorRef.current(snapped);
    setPlay(false);
    const index = viewClips.length ? clipAtTime(viewClips, snapped) : -1;
    if (index >= 0) selectRef.current(index, true);
  };

  const beginScrub = (event) => {
    if (event.button !== 0 || event.target.closest('.timeline-clip, .trim-handle, button')) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setScrubbing(true);
    seek(event);
  };
  const updateScrub = (event) => {
    if (scrubbing) seek(event);
  };
  const beginTrim = (event, index, side) => {
    event.preventDefault();
    event.stopPropagation();
    const clip = p.clips[index];
    if (locked || clip?.locked || !clip) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      index,
      side,
      startX: event.clientX,
      baseClips: p.clips.map((item) => ({ ...item })),
      clips: p.clips.map((item) => ({ ...item })),
      starts: [...starts],
    };
    setDragState(dragRef.current);
  };
  const handleDrop = (event, targetIndex) => {
    event.preventDefault();
    if (locked) return;
    const from = Number(event.dataTransfer.getData('text/plain'));
    if (!Number.isInteger(from) || from < 0 || from >= p.clips.length || from === targetIndex)
      return;
    const insertion = from < targetIndex ? targetIndex - 1 : targetIndex;
    edit((next) => {
      const [moved] = next.clips.splice(from, 1);
      next.clips.splice(insertion, 0, moved);
    });
    onSelect(insertion);
  };

  return (
    <section className="timeline">
      <div className="timeline-heading">
        <div>
          <Layers size={17} />
          <b>Timeline</b>
          <span>
            {p.clips.length} cảnh · {fmt(duration)}
          </span>
        </div>
        <div className="timeline-tools" role="toolbar" aria-label="Công cụ timeline">
          <label className="ai-toggle">
            <input
              type="checkbox"
              checked={useAI}
              onChange={(event) => setUseAI(event.target.checked)}
            />
            AI chọn cảnh
          </label>
          <button
            className="primary small"
            title="Lập lại timeline, giữ nguyên các cảnh đã khóa"
            disabled={locked}
            onClick={onPlan}
          >
            <Sparkles size={14} />
            Tự động dựng
          </button>
          <button
            className="icon-button"
            title="Tách tại playhead · S"
            aria-label="Tách cảnh tại playhead"
            disabled={
              locked ||
              !currentClip ||
              currentClip.locked ||
              cursor < starts[selected] + MIN_CLIP_SECONDS ||
              cursor > starts[selected] + currentClip.duration - MIN_CLIP_SECONDS
            }
            onClick={onSplit}
          >
            <Scissors size={15} />
          </button>
          <button
            className="icon-button"
            title="Nhân đôi cảnh"
            aria-label="Nhân đôi cảnh đang chọn"
            disabled={
              locked ||
              !currentClip ||
              duration + (currentClip?.duration || 0) > 180 ||
              p.clips.length >= 150
            }
            onClick={onDuplicate}
          >
            <Copy size={15} />
          </button>
          <button
            className="icon-button"
            title={currentClip?.locked ? 'Mở khóa cảnh' : 'Khóa cảnh đã duyệt'}
            aria-label={currentClip?.locked ? 'Mở khóa cảnh' : 'Khóa cảnh đã duyệt'}
            disabled={locked || !currentClip}
            onClick={onToggleLock}
          >
            {currentClip?.locked ? <LockKeyhole size={15} /> : <LockKeyholeOpen size={15} />}
          </button>
          <button
            className="icon-button"
            title="Xóa cảnh"
            aria-label="Xóa cảnh đang chọn"
            disabled={locked || !currentClip || currentClip.locked}
            onClick={onDelete}
          >
            <Trash2 size={15} />
          </button>
          <span className="zoom-control">
            <button
              className="icon-button"
              title="Thu nhỏ timeline"
              aria-label="Thu nhỏ timeline"
              disabled={pps <= 24}
              onClick={() => setPps((value) => Math.max(24, value / 1.25))}
            >
              <ZoomOut size={15} />
            </button>
            <input
              aria-label="Mức phóng timeline"
              type="range"
              min="24"
              max="160"
              step="4"
              value={pps}
              onChange={(event) => setPps(Number(event.target.value))}
            />
            <button
              className="icon-button"
              title="Phóng to timeline"
              aria-label="Phóng to timeline"
              disabled={pps >= 160}
              onClick={() => setPps((value) => Math.min(160, value * 1.25))}
            >
              <ZoomIn size={15} />
            </button>
          </span>
        </div>
      </div>
      <div
        className={`timeline-scroll ${scrubbing ? 'scrubbing' : ''}`}
        ref={scrollRef}
        onPointerDown={beginScrub}
        onPointerMove={updateScrub}
        onPointerUp={() => setScrubbing(false)}
        onPointerCancel={() => setScrubbing(false)}
      >
        <div
          className="timeline-content"
          style={{ width: totalWidth, '--timeline-label-width': `${LABEL_WIDTH}px` }}
        >
          <div className="ruler">
            <span className="timeline-track-label">THỜI GIAN</span>
            {ticks.map((value) => (
              <span className="ruler-tick" key={value} style={{ left: LABEL_WIDTH + value * pps }}>
                {fmt(value)}
              </span>
            ))}
          </div>
          <div className="clip-track m4-clip-track">
            <span className="timeline-track-label">HÌNH</span>
            <div
              className="track-lane"
              style={{ left: LABEL_WIDTH, width: timelineDuration * pps }}
            >
              {p.clips.length ? (
                viewClips.map((clip, index) => {
                  const asset = aid(p, clip.asset_id);
                  const scene = asset?.scenes.find((item) => item.id === clip.scene_id);
                  const isSelected = index === selected;
                  return (
                    <button
                      className={`timeline-clip ${isSelected ? 'selected' : ''} ${clip.locked ? 'locked' : ''}`}
                      data-clip-id={clip.id}
                      draggable={!locked && !clip.locked}
                      key={clip.id}
                      onDragStart={(event) =>
                        event.dataTransfer.setData('text/plain', String(index))
                      }
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => handleDrop(event, index)}
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect(index);
                        setCursor(starts[index]);
                      }}
                      style={{
                        left: starts[index] * pps,
                        width: clip.duration * pps,
                      }}
                      title={`${asset?.name || 'Cảnh'} · ${fmt(starts[index])}–${fmt(starts[index] + clip.duration)}${clip.locked ? ' · Đã khóa' : ''}`}
                    >
                      <img src={thumbUrl(p, scene?.thumbnail || asset?.thumbnail)} alt="" />
                      <span className="clip-index">{String(index + 1).padStart(2, '0')}</span>
                      {clip.locked && (
                        <LockKeyhole className="clip-lock" size={13} aria-label="Đã khóa" />
                      )}
                      <span className="clip-duration">{clip.duration.toFixed(2)}s</span>
                      <small>{asset?.name}</small>
                      <span
                        className="trim-handle left"
                        role="slider"
                        aria-label={`Cắt đầu cảnh ${index + 1}`}
                        aria-valuemin={MIN_CLIP_SECONDS}
                        aria-valuenow={clip.duration}
                        tabIndex={locked || clip.locked ? -1 : 0}
                        onPointerDown={(event) => beginTrim(event, index, 'left')}
                      />
                      <span
                        className="trim-handle right"
                        role="slider"
                        aria-label={`Cắt cuối cảnh ${index + 1}`}
                        aria-valuemin={MIN_CLIP_SECONDS}
                        aria-valuenow={clip.duration}
                        tabIndex={locked || clip.locked ? -1 : 0}
                        onPointerDown={(event) => beginTrim(event, index, 'right')}
                      />
                    </button>
                  );
                })
              ) : (
                <div className="timeline-empty">Nhập tư liệu → Phân tích cảnh → Tự động dựng</div>
              )}
            </div>
          </div>
          <div className="audio-track m4-audio-track">
            <span className="timeline-track-label">
              <Mic2 size={13} /> VOICE
            </span>
            <div
              className="track-lane audio-lane voice"
              style={{ left: LABEL_WIDTH, width: timelineDuration * pps }}
            >
              {p.voice_id ? (
                <Waveform
                  project={p}
                  asset={aid(p, p.voice_id)}
                  width={trackDuration * pps}
                  pps={pps}
                  repeat={false}
                />
              ) : (
                <span className="waveform-empty">Thêm giọng đọc hoặc tạo từ kịch bản</span>
              )}
            </div>
          </div>
          <div className="audio-track m4-audio-track">
            <span className="timeline-track-label">
              <Music2 size={13} /> NHẠC
            </span>
            <div
              className="track-lane audio-lane music"
              style={{ left: LABEL_WIDTH, width: timelineDuration * pps }}
            >
              {p.music_id ? (
                <Waveform
                  project={p}
                  asset={aid(p, p.music_id)}
                  width={trackDuration * pps}
                  pps={pps}
                  repeat
                />
              ) : (
                <span className="waveform-empty">Thêm nhạc nền</span>
              )}
            </div>
          </div>
          {p.clips.length > 0 && (
            <div className="audio-track m4-audio-track">
              <span className="timeline-track-label">SFX</span>
              <div
                className="track-lane audio-lane sound-effects"
                style={{ left: LABEL_WIDTH, width: timelineDuration * pps }}
              >
                {(p.sound_effects || [])
                  .filter((event) => event.origin !== 'auto' || p.auto_sound_effects !== false)
                  .map((event) => (
                    <button
                      key={event.id}
                      className={`sfx-marker ${event.origin}`}
                      style={{ left: event.time * pps }}
                      title={`${event.effect} · ${event.time.toFixed(2)}s`}
                      onClick={() => {
                        setPlay(false);
                        setCursor(event.time);
                      }}
                    >
                      <Sparkles size={12} /> {event.effect}
                    </button>
                  ))}
              </div>
            </div>
          )}
          {p.clips.length > 0 && (
            <div
              className="timeline-playhead"
              style={{ left: LABEL_WIDTH + clamp(cursor, 0, duration) * pps }}
              aria-hidden="true"
            />
          )}
        </div>
      </div>
      <div className="timeline-help">
        Kéo mép cảnh để cắt · cảnh đã khóa được giữ nguyên khi dựng lại · snap theo cảnh và phụ đề
      </div>
    </section>
  );
}
