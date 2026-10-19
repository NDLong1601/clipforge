import { Smartphone, X } from 'lucide-react';
import { useState } from 'react';
import { Button, Field } from './common.jsx';

const MAX_LINES = 3;
const MAX_CHARS = 60;

// Suggest the opening sentence of the script as the hook; the user edits it.
export function suggestedHook(script) {
  const first = (script || '').trim().split(/(?<=[.!?…])\s+|\n+/)[0] || '';
  return first.length > MAX_CHARS ? '' : first.toLocaleUpperCase('vi');
}

export function PortraitModal({ project, source, onClose, onSubmit }) {
  const [narrator, setNarrator] = useState('nam');
  const [hook, setHook] = useState(() => suggestedHook(project.script));
  const [music, setMusic] = useState(!project.music_id);
  const [rate, setRate] = useState(1.1);
  const lines = hook
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  const problem =
    lines.length > MAX_LINES
      ? `Hook có tối đa ${MAX_LINES} dòng.`
      : lines.some((line) => line.length > MAX_CHARS)
        ? `Mỗi dòng hook tối đa ${MAX_CHARS} ký tự.`
        : '';
  return (
    <div className="modal-backdrop">
      <div
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Xuất bản dọc vẽ tay"
      >
        <div className="modal-header">
          <div>
            <span className="eyebrow">BẢN XUẤT {source.duration}s</span>
            <h2>Xuất bản dọc vẽ tay</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Đóng">
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <p className="helper">
            Video đã dựng được đặt giữa khung 1080 × 1920, có nền lưới tím, người kể chuyện chuyển
            động theo giọng ở góc dưới trái và hook vàng phía trên. Bản xuất 16:9 cho bố cục đẹp
            nhất.
          </p>
          <Field label="Người kể chuyện">
            <select value={narrator} onChange={(e) => setNarrator(e.target.value)}>
              <option value="nam">Nam</option>
              <option value="nu">Nữ</option>
            </select>
          </Field>
          <Field label="Hook (mỗi dòng một câu, tối đa 3 dòng)" hint={problem}>
            <textarea
              rows={3}
              value={hook}
              placeholder="RỤNG TÓC, KHÓ NGỦ?"
              onChange={(e) => setHook(e.target.value)}
            />
          </Field>
          <Field label="Tốc độ xuất">
            <select value={rate} onChange={(e) => setRate(Number(e.target.value))}>
              <option value={1}>x1.0 (giữ nguyên)</option>
              <option value={1.1}>x1.1 (mặc định)</option>
              <option value={1.2}>x1.2</option>
            </select>
          </Field>
          <label className="ai-toggle">
            <input type="checkbox" checked={music} onChange={(e) => setMusic(e.target.checked)} />
            Thêm nhạc Relaxed, tự giảm khi có lời
          </label>
          {project.music_id && music && (
            <small className="muted">
              Bản xuất đã có nhạc nền; bật thêm sẽ chồng hai lớp nhạc.
            </small>
          )}
        </div>
        <div className="modal-footer">
          <Button onClick={onClose}>Hủy</Button>
          <Button
            primary
            icon={Smartphone}
            disabled={!!problem}
            onClick={() =>
              onSubmit({
                export_id: source.id,
                case: narrator,
                hook_lines: lines,
                music,
                playback_rate: rate,
              })
            }
          >
            Xuất bản dọc
          </Button>
        </div>
      </div>
    </div>
  );
}
