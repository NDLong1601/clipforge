import {
  FileArchive,
  ImagePlus,
  PenTool,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  UserRound,
  Wand2,
} from 'lucide-react';
import { useRef } from 'react';
import { aid, fmt, thumbUrl } from '../api/client.js';
import { Button, Empty, Field } from './common.jsx';

// Drawing treatments from the kit's story-character-presets.json.
export const STYLES = [
  ['editorial-ink', 'Biếm họa mực'],
  ['bold-doodle', 'Doodle marker đậm'],
  ['retro-pencil', 'Phác bút chì'],
  ['comic-flat', 'Comic màu phẳng'],
  ['minimal-sketch', 'Sketch tối giản'],
];
const MIN_SCENE = 0.8;

const newId = () => 's' + crypto.randomUUID().replaceAll('-', '').slice(0, 12);

export function sceneEnd(p, index) {
  const scenes = p.whiteboard.scenes;
  if (index + 1 < scenes.length) return scenes[index + 1].start;
  const voice = aid(p, p.voice_id);
  return Math.max(scenes[index].end, voice?.duration || 0);
}

// Keep the stored end of every scene equal to the next scene's start.
function relink(board, duration) {
  board.scenes.forEach((scene, index) => {
    if (index === 0) scene.start = 0;
    const next = board.scenes[index + 1];
    scene.end = Number(
      (next ? next.start : Math.max(duration, scene.start + MIN_SCENE)).toFixed(3),
    );
  });
}

function images(p, roles = ['illustration']) {
  return p.assets.filter((a) => roles.includes(a.role) && a.media === 'image' && !a.deleted);
}

function AssetSelect({ p, value, roles, onChange, disabled, empty = 'Chưa chọn' }) {
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {images(p, roles).map((a) => (
        <option key={a.id} value={a.id}>
          {a.name}
        </option>
      ))}
    </select>
  );
}

export function WhiteboardPanel({
  p,
  edit,
  locked,
  run,
  upload,
  useAI,
  setUseAI,
  selected,
  setSelected,
  onImport,
}) {
  const board = p.whiteboard;
  const zip = useRef();
  const voice = aid(p, p.voice_id);
  const reference = aid(p, board.character_reference_id);
  const missing = board.scenes.filter((s) => !s.image_id).length;
  const setBoard = (fn) => edit((n) => fn(n.whiteboard));
  return (
    <>
      <div className="panel-title">
        <h2>Video vẽ tay</h2>
        <span>{board.scenes.length} cảnh</span>
      </div>
      {!voice && (
        <p className="wb-note">
          Cần giọng đọc trước: tạo giọng AI hoặc nhập file giọng ở tab Âm thanh. Cảnh và nét vẽ bám
          theo thời lượng giọng.
        </p>
      )}
      <div className="grid-two">
        <Field label="Tỷ lệ">
          <select
            value={board.aspect}
            disabled={locked}
            onChange={(e) => setBoard((b) => (b.aspect = e.target.value))}
          >
            <option value="16:9">16:9 ngang</option>
            <option value="9:16">9:16 dọc</option>
          </select>
        </Field>
        <Field label="Nhân vật">
          <select
            value={board.case}
            disabled={locked}
            onChange={(e) => setBoard((b) => (b.case = e.target.value))}
          >
            <option value="nam">Nam</option>
            <option value="nu">Nữ</option>
          </select>
        </Field>
      </div>
      <Field label="Nét vẽ">
        <select
          value={board.style}
          disabled={locked}
          onChange={(e) => setBoard((b) => (b.style = e.target.value))}
        >
          {STYLES.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Mô tả nhân vật (giữ cố định cho mọi cảnh)">
        <textarea
          rows={3}
          disabled={locked}
          value={board.character_description}
          placeholder="Tuổi, khuôn mặt, tóc, trang phục, tỉ lệ. Để trống để AI thiết kế theo giọng kể."
          onChange={(e) => setBoard((b) => (b.character_description = e.target.value))}
        />
      </Field>
      <div className="wb-reference">
        {reference ? (
          <img src={thumbUrl(p, reference.thumbnail)} alt="Nhân vật khóa" />
        ) : (
          <span>
            <UserRound size={22} />
          </span>
        )}
        <div>
          <Field label="Nhân vật khóa">
            <AssetSelect
              p={p}
              value={board.character_reference_id}
              roles={['illustration']}
              disabled={locked}
              empty="Chưa có (AI tạo khi vẽ cảnh)"
              onChange={(id) => setBoard((b) => (b.character_reference_id = id))}
            />
          </Field>
          <Button
            small
            icon={Wand2}
            disabled={locked}
            onClick={() => run('whiteboard/images', { character: true, only_character: true })}
          >
            {reference ? 'Tạo lại nhân vật' : 'Tạo nhân vật bằng AI'}
          </Button>
        </div>
      </div>
      <Field label="Ảnh sản phẩm thật (ghép vào cảnh có sản phẩm)">
        <AssetSelect
          p={p}
          value={board.product_id}
          roles={['product', 'illustration', 'overlay']}
          disabled={locked}
          onChange={(id) => setBoard((b) => (b.product_id = id))}
        />
      </Field>
      <div className="row">
        <label className="upload-inline">
          <ImagePlus size={14} /> Tải ảnh sản phẩm
          <input
            hidden
            type="file"
            accept="image/*"
            disabled={locked}
            onChange={(e) => {
              upload(e.target.files, 'product');
              e.target.value = '';
            }}
          />
        </label>
      </div>
      <Field label={`Âm lượng nhạc nền ${Math.round(board.music_volume * 100)}%`}>
        <input
          type="range"
          min="0"
          max="1"
          step="0.02"
          disabled={locked}
          value={board.music_volume}
          onChange={(e) => setBoard((b) => (b.music_volume = Number(e.target.value)))}
        />
      </Field>
      <div className="button-stack">
        <Button
          icon={Sparkles}
          primary
          disabled={locked || !voice}
          onClick={() => {
            if (
              board.scenes.length &&
              !window.confirm('Lập lại storyboard sẽ thay toàn bộ cảnh hiện có. Tiếp tục?')
            )
              return;
            run('whiteboard/plan', { use_ai: useAI });
          }}
        >
          Lập storyboard từ lời thoại
        </Button>
        <label className="ai-toggle">
          <input type="checkbox" checked={useAI} onChange={(e) => setUseAI(e.target.checked)} />
          Dùng AI chia cảnh, viết từ khóa và mô tả tranh
        </label>
        <Button
          icon={Wand2}
          disabled={locked || !missing}
          onClick={() => run('whiteboard/images', { missing_only: true })}
        >
          Vẽ tranh AI cho {missing} cảnh còn thiếu
        </Button>
        <Button icon={FileArchive} disabled={locked} onClick={() => zip.current.click()}>
          Nhập dự án từ bộ vẽ tay (ZIP)
        </Button>
        <input
          ref={zip}
          hidden
          type="file"
          accept=".zip,application/zip"
          onChange={(e) => {
            if (e.target.files[0]) onImport(e.target.files[0]);
            e.target.value = '';
          }}
        />
      </div>
      <div className="wb-scene-head">
        <h3>Cảnh</h3>
        <Button
          small
          icon={Plus}
          disabled={locked || board.scenes.length >= 80}
          onClick={() => {
            const index = Math.min(selected, board.scenes.length - 1);
            edit((n) => {
              const scenes = n.whiteboard.scenes;
              if (!scenes.length) {
                scenes.push({ ...blankScene(), start: 0, end: Math.max(voice?.duration || 3, 1) });
                return;
              }
              const start = scenes[index].start;
              const end = sceneEnd(n, index);
              scenes.splice(index + 1, 0, {
                ...blankScene(),
                start: Number(((start + end) / 2).toFixed(3)),
                end,
              });
              relink(n.whiteboard, voice?.duration || end);
            });
            setSelected(board.scenes.length ? index + 1 : 0);
          }}
        >
          Thêm cảnh
        </Button>
      </div>
      {board.scenes.length === 0 ? (
        <Empty icon={PenTool} title="Chưa có cảnh vẽ tay">
          Lập storyboard từ lời thoại, nhập ZIP từ bộ vẽ tay hoặc thêm cảnh thủ công.
        </Empty>
      ) : (
        <div className="wb-scenes">
          {board.scenes.map((scene, index) => {
            const image = aid(p, scene.image_id);
            return (
              <button
                key={scene.id}
                className={'wb-scene' + (index === selected ? ' active' : '')}
                onClick={() => setSelected(index)}
              >
                <span className="wb-thumb">
                  {image ? (
                    <img src={thumbUrl(p, image.thumbnail)} alt="" />
                  ) : (
                    <ImagePlus size={16} />
                  )}
                </span>
                <span className="wb-scene-text">
                  <b>
                    {String(index + 1).padStart(2, '0')} · {fmt(scene.start)}–
                    {fmt(sceneEnd(p, index))}
                  </b>
                  <small>
                    {scene.titles.map((t) => t.text).join(' · ') || scene.narration || '—'}
                  </small>
                </span>
                {!image && <em>Thiếu tranh</em>}
              </button>
            );
          })}
        </div>
      )}
    </>
  );
}

function blankScene() {
  return {
    id: newId(),
    narration: '',
    image_id: '',
    crop: null,
    image_prompt: '',
    titles: [],
    has_character: true,
    product: false,
    layout: null,
  };
}

export function WhiteboardInspector({ p, edit, locked, run, upload, selected, setSelected }) {
  const board = p.whiteboard;
  const scene = board.scenes[selected];
  if (!scene)
    return (
      <div className="inspector-fields">
        <p className="muted">Chọn một cảnh để chỉnh.</p>
      </div>
    );
  const voice = aid(p, p.voice_id);
  const end = sceneEnd(p, selected);
  const change = (fn) => edit((n) => fn(n.whiteboard.scenes[selected], n));
  const previousStart = selected ? board.scenes[selected - 1].start : 0;
  const nextStart = selected + 1 < board.scenes.length ? board.scenes[selected + 1].start : end;
  return (
    <div className="inspector-fields">
      <div className="grid-two">
        <Field
          label="Bắt đầu (giây)"
          hint={`Dài ${(end - scene.start).toFixed(1)}s, kết thúc ${end.toFixed(2)}s`}
        >
          <input
            type="number"
            step="0.05"
            disabled={locked || selected === 0}
            value={scene.start}
            min={previousStart + MIN_SCENE}
            max={nextStart - MIN_SCENE}
            onChange={(e) => {
              const value = Number(e.target.value);
              const clamped = Math.max(
                previousStart + MIN_SCENE,
                Math.min(nextStart - MIN_SCENE, value),
              );
              edit((n) => {
                n.whiteboard.scenes[selected].start = Number(clamped.toFixed(3));
                relink(n.whiteboard, voice?.duration || end);
              });
            }}
          />
        </Field>
        <Field label="Tranh">
          <AssetSelect
            p={p}
            value={scene.image_id}
            roles={['illustration']}
            disabled={locked}
            onChange={(id) =>
              change((s) => {
                s.image_id = id;
                s.crop = null;
                s.layout = null;
              })
            }
          />
        </Field>
      </div>
      <div className="row wb-actions">
        <label className="upload-inline">
          <ImagePlus size={14} /> Tải tranh
          <input
            hidden
            type="file"
            accept="image/*"
            disabled={locked}
            onChange={(e) => {
              upload(e.target.files, 'illustration');
              e.target.value = '';
            }}
          />
        </label>
        <Button
          small
          icon={RefreshCw}
          disabled={locked}
          onClick={() =>
            run('whiteboard/images', {
              scene_ids: [scene.id],
              missing_only: false,
            })
          }
        >
          {scene.image_id ? 'Vẽ lại bằng AI' : 'Vẽ bằng AI'}
        </Button>
      </div>
      <Field label="Lời thoại của cảnh">
        <textarea
          rows={2}
          disabled={locked}
          value={scene.narration}
          onChange={(e) => change((s) => (s.narration = e.target.value))}
        />
      </Field>
      <Field label="Mô tả tranh cho AI">
        <textarea
          rows={3}
          disabled={locked}
          value={scene.image_prompt}
          placeholder="Tư thế, biểu cảm, đồ vật. Để trống sẽ dùng lời thoại."
          onChange={(e) => change((s) => (s.image_prompt = e.target.value))}
        />
      </Field>
      <div className="wb-titles">
        <span>Từ khóa trên màn hình (tối đa 3)</span>
        {scene.titles.map((title, index) => (
          <div className="row" key={index}>
            <input
              value={title.text}
              maxLength={80}
              disabled={locked}
              aria-label={`Từ khóa ${index + 1}`}
              onChange={(e) => change((s) => (s.titles[index].text = e.target.value))}
            />
            <label className="ai-toggle" title="Tô màu nhấn">
              <input
                type="checkbox"
                disabled={locked}
                checked={title.accent}
                onChange={(e) => change((s) => (s.titles[index].accent = e.target.checked))}
              />
              Nhấn
            </label>
            <button
              className="icon-button"
              aria-label="Xóa từ khóa"
              disabled={locked}
              onClick={() => change((s) => s.titles.splice(index, 1))}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {scene.titles.length < 3 && (
          <Button
            small
            icon={Plus}
            disabled={locked}
            onClick={() =>
              change((s) => s.titles.push({ text: 'TỪ KHÓA', accent: false, start: null }))
            }
          >
            Thêm từ khóa
          </Button>
        )}
      </div>
      <label className="ai-toggle">
        <input
          type="checkbox"
          disabled={locked}
          checked={scene.has_character}
          onChange={(e) => change((s) => (s.has_character = e.target.checked))}
        />
        Cảnh có nhân vật chính (vẽ theo nhân vật khóa)
      </label>
      <label className="ai-toggle">
        <input
          type="checkbox"
          disabled={locked || !board.product_id}
          checked={scene.product}
          onChange={(e) => change((s) => (s.product = e.target.checked))}
        />
        Hiện ảnh sản phẩm thật
      </label>
      {scene.layout && (
        <p className="wb-note">
          Đang giữ khung tranh và vùng vẽ từ bộ vẽ tay.{' '}
          <button
            className="text-link"
            disabled={locked}
            onClick={() => change((s) => (s.layout = null))}
          >
            Dùng bố cục tự động
          </button>
        </p>
      )}
      <Button
        small
        icon={Trash2}
        disabled={locked}
        onClick={() => {
          edit((n) => {
            n.whiteboard.scenes.splice(selected, 1);
            relink(n.whiteboard, voice?.duration || end);
          });
          setSelected(Math.max(0, selected - 1));
        }}
      >
        Xóa cảnh
      </Button>
    </div>
  );
}

export function WhiteboardPreview({ p, selected, dirty }) {
  const scene = p.whiteboard.scenes[selected];
  if (!scene?.image_id)
    return (
      <div className="preview-stage">
        <Empty icon={PenTool} title={scene ? 'Cảnh chưa có tranh' : 'Chưa có cảnh'}>
          Xem trước hiện hình vẽ hoàn chỉnh, từ khóa và thứ tự vùng vẽ của cảnh.
        </Empty>
      </div>
    );
  return (
    <div className="preview-stage wb-preview">
      <img
        className={'aspect-' + p.whiteboard.aspect.replace(':', '-')}
        src={`/api/projects/${p.id}/whiteboard/scenes/${scene.id}/preview.png?r=${p.revision}`}
        alt={`Xem trước cảnh ${selected + 1}`}
      />
      {dirty && <small className="wb-stale">Lưu để cập nhật xem trước</small>}
    </div>
  );
}
