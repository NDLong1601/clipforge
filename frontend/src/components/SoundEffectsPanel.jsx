import { Plus, Sparkles, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { Button, Field, NumberField } from './common.jsx';

export function SoundEffectsPanel({ p, edit, locked, cursor, duration }) {
  const [catalog, setCatalog] = useState([]);
  const [chosen, setChosen] = useState('whoosh');
  const [error, setError] = useState('');
  const [at, setAt] = useState(cursor || 0);
  useEffect(() => {
    let active = true;
    api('/sound-effects')
      .then((items) => {
        if (active) setCatalog(items);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  const events = p.sound_effects || [];
  const manual = events.filter((event) => event.origin !== 'auto');
  const automatic = events.filter((event) => event.origin === 'auto');
  return (
    <div className="subsection sound-effects-panel">
      <h3>Sound effect · Hiệu ứng âm thanh</h3>
      <label className="ai-toggle">
        <input
          type="checkbox"
          disabled={locked}
          checked={p.auto_sound_effects !== false}
          onChange={(e) =>
            edit((next) => {
              next.auto_sound_effects = e.target.checked;
            })
          }
        />
        Tự chèn khi chuyển cảnh và nhấn mạnh lời đọc
      </label>
      <p className="helper">
        Tự cập nhật khi lưu hoặc tự động dựng; ưu tiên điểm nổi bật, ưu đãi và lời kêu gọi. Hiệu ứng
        cách nhau ít nhất 2 giây, có âm lượng riêng.
      </p>
      <Field label={`Âm lượng hiệu ứng · ${Math.round((p.sound_effect_volume ?? 0.35) * 100)}%`}>
        <input
          type="range"
          min="0"
          max="1"
          step=".01"
          disabled={locked}
          value={p.sound_effect_volume ?? 0.35}
          onChange={(e) =>
            edit((next) => {
              next.sound_effect_volume = Number(e.target.value);
            })
          }
        />
      </Field>
      <Field label="Thư viện · 10 hiệu ứng">
        <select disabled={locked} value={chosen} onChange={(e) => setChosen(e.target.value)}>
          {catalog.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Field>
      {catalog.length > 0 && (
        <audio key={chosen} controls preload="none" src={`/api/sound-effects/${chosen}.wav`} />
      )}
      {error && <p className="error">{error}</p>}
      <div className="grid-two">
        <NumberField
          disabled={locked}
          label="Chèn tại (giây)"
          value={at}
          min={0}
          max={Math.max(0, duration - 0.01)}
          step={0.01}
          onChange={setAt}
        />
        <Button small disabled={locked} onClick={() => setAt(cursor || 0)}>
          Dùng vị trí phát
        </Button>
      </div>
      <Button
        small
        icon={Plus}
        disabled={locked || !duration || events.length >= 200}
        onClick={() =>
          edit((next) => {
            next.sound_effects = [
              ...(next.sound_effects || []),
              {
                id: crypto.randomUUID(),
                effect: chosen,
                time: Math.min(Math.max(0, at), Math.max(0, duration - 0.01)),
                volume: 0.7,
                origin: 'manual',
              },
            ];
          })
        }
      >
        Thêm hiệu ứng
      </Button>
      {manual.map((event) => (
        <div className="sound-effect-row" key={event.id}>
          <b>{catalog.find((item) => item.id === event.effect)?.name || event.effect}</b>
          <NumberField
            disabled={locked}
            label="Mốc (s)"
            value={event.time}
            min={0}
            max={180}
            step={0.01}
            onChange={(value) =>
              edit((next) => {
                next.sound_effects.find((item) => item.id === event.id).time = value;
              })
            }
          />
          <input
            aria-label="Âm lượng hiệu ứng riêng"
            type="range"
            min="0"
            max="1"
            step=".05"
            disabled={locked}
            value={event.volume}
            onChange={(e) =>
              edit((next) => {
                next.sound_effects.find((item) => item.id === event.id).volume = Number(
                  e.target.value,
                );
              })
            }
          />
          <Button
            small
            icon={Trash2}
            disabled={locked}
            onClick={() =>
              edit((next) => {
                next.sound_effects = next.sound_effects.filter((item) => item.id !== event.id);
              })
            }
          >
            Xóa
          </Button>
        </div>
      ))}
      {p.auto_sound_effects !== false && automatic.length > 0 && (
        <div className="sound-effect-auto">
          <Sparkles size={14} /> {automatic.length} hiệu ứng tự động:{' '}
          {automatic.map((event) => `${event.effect} ${event.time.toFixed(1)}s`).join(' · ')}
        </div>
      )}
    </div>
  );
}
