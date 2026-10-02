import { Check, ClipboardPaste, Eye, EyeOff, KeyRound, Plus, Save, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, clone } from '../api/client.js';
import { Button, Field, NumberField } from './common.jsx';

export function SettingsModal({ settings, onClose, onSave, onTest, onUseEnvironment }) {
  const [s, setS] = useState(() => clone(settings));
  const [selectedId, setSelectedId] = useState(
    settings.active_ai_profile_id || settings.ai_profiles?.[0]?.id || '',
  );
  const [newProvider, setNewProvider] = useState('gemini');
  const [showKey, setShowKey] = useState(false);
  const [note, setNote] = useState('');
  const [testing, setTesting] = useState(false);
  const [voices, setVoices] = useState([]);
  const keyInput = useRef(null);
  const switchToEnvironment = async (secret) => {
    setNote('Đang chuyển cấu hình…');
    const next = await onUseEnvironment(secret, s);
    if (next) {
      setS(next);
      setSelectedId(next.active_ai_profile_id || next.ai_profiles?.[0]?.id || '');
      setNote('Đã chọn dùng khóa từ biến môi trường.');
    }
  };
  useEffect(() => {
    api('/voices/windows')
      .then(setVoices)
      .catch(() => {});
  }, []);
  const field = (key, value) => setS((old) => ({ ...old, [key]: value }));
  const profiles = s.ai_profiles || [];
  const profile = profiles.find((x) => x.id === selectedId) || profiles[0];
  const initialProfile = settings.ai_profiles?.find((x) => x.id === profile?.id);
  const savedKey = initialProfile?.api_key === '••••••••';
  const changedKey = profile && profile.api_key !== initialProfile?.api_key;
  const editProfile = (id, patch) =>
    setS((old) => ({
      ...old,
      ai_profiles: old.ai_profiles.map((x) => (x.id === id ? { ...x, ...patch } : x)),
    }));
  const addProfile = () => {
    const names = { openai: 'OpenAI', gemini: 'Gemini', compatible: 'API tương thích' };
    const defaults = {
      openai: 'gpt-4o-mini',
      gemini: 'gemini-2.5-flash',
      compatible: 'model-name',
    };
    const item = {
      id: crypto.randomUUID(),
      name: names[newProvider] + ' ' + (profiles.length + 1),
      provider: newProvider,
      api_key: '',
      model: defaults[newProvider],
      base_url: '',
      enabled: true,
    };
    setS((old) => ({
      ...old,
      ai_profiles: [...old.ai_profiles, item],
      active_ai_profile_id: old.active_ai_profile_id || item.id,
    }));
    setSelectedId(item.id);
    setShowKey(false);
    setNote('');
  };
  const removeProfile = (id) => {
    const remaining = profiles.filter((x) => x.id !== id);
    setS((old) => ({
      ...old,
      ai_profiles: remaining,
      active_ai_profile_id:
        old.active_ai_profile_id === id ? remaining[0]?.id || '' : old.active_ai_profile_id,
    }));
    setSelectedId(remaining[0]?.id || '');
    setNote('');
  };
  const moveProfile = (id, delta) => {
    const index = profiles.findIndex((x) => x.id === id),
      target = index + delta;
    if (target < 0 || target >= profiles.length) return;
    const reordered = [...profiles];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    field('ai_profiles', reordered);
  };
  const pasteKey = async () => {
    if (!profile) return;
    try {
      const value = (await navigator.clipboard.readText()).trim();
      if (!value) {
        setNote('Clipboard đang trống. Bạn có thể nhấn Ctrl+V vào ô khóa.');
        return;
      }
      editProfile(profile.id, { api_key: value });
      setNote('Đã dán khóa. Nhấn “Lưu cài đặt” để hoàn tất.');
    } catch {
      keyInput.current?.focus();
      keyInput.current?.select();
      setNote('Trình duyệt không cho đọc clipboard. Nhấn Ctrl+V vào ô đã chọn.');
    }
  };
  const testConnection = async () => {
    if (!profile) return;
    setTesting(true);
    setNote('');
    try {
      const result = await onTest(s, profile.id);
      if (result) {
        setS(result.settings);
        setNote(
          (result.message || 'Kết nối AI thành công') +
            ' · ' +
            result.route.name +
            ' / ' +
            result.route.model,
        );
      }
    } finally {
      setTesting(false);
    }
  };
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        className="settings-modal multi-api-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Cài đặt nhiều API"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <span className="eyebrow">CLIPFORGE LOCAL</span>
            <h2>API, model và giọng đọc</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Đóng cài đặt">
            <X />
          </button>
        </div>
        <div className="modal-body">
          <div className="api-section-header">
            <div>
              <h3>Các cấu hình AI</h3>
              <p>
                Chọn API chính. Khi lỗi, chương trình thử các cấu hình còn lại theo thứ tự trong
                danh sách.
              </p>
            </div>
          </div>
          <div className="api-add-row">
            <select
              aria-label="Loại API mới"
              value={newProvider}
              onChange={(e) => setNewProvider(e.target.value)}
            >
              <option value="gemini">Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="compatible">API tương thích OpenAI</option>
            </select>
            <Button icon={Plus} onClick={addProfile}>
              Thêm API
            </Button>
          </div>
          {!profiles.length && (
            <div className="api-empty">Chưa có API. Chọn loại ở trên và bấm “Thêm API”.</div>
          )}
          <div className="api-profile-list">
            {profiles.map((item, index) => (
              <div
                key={item.id}
                className={'api-profile-row ' + (profile?.id === item.id ? 'selected' : '')}
              >
                <button
                  className="api-profile-select"
                  onClick={() => {
                    setSelectedId(item.id);
                    setShowKey(false);
                    setNote('');
                  }}
                >
                  <strong>{item.name}</strong>
                  <small>
                    {item.provider === 'gemini'
                      ? 'Gemini'
                      : item.provider === 'openai'
                        ? 'OpenAI'
                        : 'API tương thích'}{' '}
                    · {item.model} · {item.api_key ? 'Có khóa' : 'Chưa có khóa'}
                    {!item.enabled ? ' · Đang tắt' : ''}
                  </small>
                </button>
                <button
                  className={
                    'profile-priority ' + (s.active_ai_profile_id === item.id ? 'active' : '')
                  }
                  onClick={() => field('active_ai_profile_id', item.id)}
                  aria-label={'Đặt ' + item.name + ' làm API chính'}
                  title="Đặt làm API chính"
                >
                  {s.active_ai_profile_id === item.id ? 'Chính' : 'Dùng chính'}
                </button>
                <button
                  className="profile-move"
                  disabled={index === 0}
                  onClick={() => moveProfile(item.id, -1)}
                  aria-label={'Tăng ưu tiên ' + item.name}
                >
                  ↑
                </button>
                <button
                  className="profile-move"
                  disabled={index === profiles.length - 1}
                  onClick={() => moveProfile(item.id, 1)}
                  aria-label={'Giảm ưu tiên ' + item.name}
                >
                  ↓
                </button>
              </div>
            ))}
          </div>
          {profile && (
            <div className="api-key-card profile-editor" key={profile.id}>
              <div className="api-key-heading">
                <div className="api-key-icon">
                  <KeyRound size={20} />
                </div>
                <div>
                  <h3>Chỉnh {profile.name}</h3>
                  <p>
                    {s.active_ai_profile_id === profile.id ? 'API chính' : 'API dự phòng'} ·{' '}
                    {profile.enabled ? 'Đang bật' : 'Đang tắt'}
                  </p>
                </div>
                <button
                  className="profile-remove"
                  onClick={() => removeProfile(profile.id)}
                  aria-label={'Xóa ' + profile.name}
                >
                  Xóa
                </button>
              </div>
              <div className="grid-two">
                <Field label="Tên cấu hình">
                  <input
                    value={profile.name}
                    maxLength={80}
                    onChange={(e) => editProfile(profile.id, { name: e.target.value })}
                  />
                </Field>
                <Field label="Loại API">
                  <select
                    value={profile.provider}
                    onChange={(e) => editProfile(profile.id, { provider: e.target.value })}
                  >
                    <option value="gemini">Gemini</option>
                    <option value="openai">OpenAI</option>
                    <option value="compatible">API tương thích OpenAI</option>
                  </select>
                </Field>
              </div>
              <div className="grid-two">
                <Field label="Model">
                  <input
                    value={profile.model}
                    onChange={(e) => editProfile(profile.id, { model: e.target.value })}
                    placeholder="Tên model của nhà cung cấp"
                  />
                </Field>
                {profile.provider === 'compatible' ? (
                  <Field label="API base URL">
                    <input
                      value={profile.base_url}
                      onChange={(e) => editProfile(profile.id, { base_url: e.target.value })}
                      placeholder="https://.../v1"
                    />
                  </Field>
                ) : (
                  <div className="profile-endpoint">
                    <span>Endpoint</span>
                    <small>
                      {profile.provider === 'gemini' ? 'Gemini API (Google)' : 'OpenAI API'}
                    </small>
                  </div>
                )}
              </div>
              <label className="field" htmlFor="profile-api-key">
                <span>API key {savedKey && !changedKey ? '· đã lưu' : ''}</span>
              </label>
              <div className="secret-input">
                <input
                  id="profile-api-key"
                  ref={keyInput}
                  type={showKey ? 'text' : 'password'}
                  autoComplete="off"
                  spellCheck="false"
                  value={profile.api_key}
                  placeholder="Dán API key vào đây"
                  onFocus={(e) => {
                    if (savedKey && !changedKey) e.target.select();
                  }}
                  onChange={(e) => {
                    editProfile(profile.id, { api_key: e.target.value });
                    setNote('');
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowKey(!showKey)}
                  aria-label={showKey ? 'Ẩn API key' : 'Hiện API key'}
                >
                  {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <div className="key-actions">
                <Button small icon={ClipboardPaste} onClick={pasteKey}>
                  Dán từ clipboard
                </Button>
                <Button
                  small
                  icon={Check}
                  disabled={!profile.api_key || !profile.enabled || testing}
                  onClick={testConnection}
                >
                  {testing ? 'Đang kiểm tra…' : 'Lưu và kiểm tra API này'}
                </Button>
              </div>
              <label className="ai-toggle">
                <input
                  type="checkbox"
                  checked={profile.enabled}
                  onChange={(e) => editProfile(profile.id, { enabled: e.target.checked })}
                />
                Bật cấu hình này
              </label>
              {note && (
                <p className="key-note" role="status">
                  {note}
                </p>
              )}
            </div>
          )}
          <label className="fallback-toggle">
            <input
              type="checkbox"
              checked={s.ai_auto_fallback}
              onChange={(e) => field('ai_auto_fallback', e.target.checked)}
            />
            <span>
              <b>Tự chuyển API khi lỗi</b>
              <small>
                Thử cấu hình chính trước, rồi lần lượt các cấu hình đang bật còn lại. Áp dụng cho
                phân tích cảnh, dựng bằng AI và trợ lý.
              </small>
            </span>
          </label>
          {settings.environment_available?.ai && (
            <div className="subsection">
              <Button small icon={KeyRound} onClick={() => switchToEnvironment('ai')}>
                Dùng OPENAI_API_KEY từ môi trường
              </Button>
              <p className="helper">
                Thao tác này đặt cấu hình API môi trường làm cấu hình chính. Khóa đã lưu khác vẫn
                được giữ.
              </p>
            </div>
          )}
          <p className="helper">
            Khóa lưu trong <code>data/settings.json</code> trên máy; mở lại chỉ thấy ký hiệu che
            khóa. Chọn model có hỗ trợ ảnh và trả JSON cho tính năng phân tích mẫu/cảnh.
          </p>
          <h3>Tạo giọng đọc</h3>
          <p className="helper">
            OpenAI Speech dùng khóa OpenAI hoặc API tương thích đang bật. Gemini trong danh sách
            trên dùng cho tác vụ biên tập, không dùng cho Speech.
          </p>
          <Field label="Nhà cung cấp">
            <select value={s.tts_provider} onChange={(e) => field('tts_provider', e.target.value)}>
              <option value="openai">OpenAI / API tương thích</option>
              <option value="azure">Azure Speech · Tiếng Việt</option>
              <option value="windows">Giọng cài trên Windows</option>
            </select>
          </Field>
          {s.tts_provider === 'openai' ? (
            <div className="grid-two">
              <Field label="Model tạo giọng">
                <input value={s.tts_model} onChange={(e) => field('tts_model', e.target.value)} />
              </Field>
              <Field label="Tên giọng">
                <input value={s.tts_voice} onChange={(e) => field('tts_voice', e.target.value)} />
              </Field>
            </div>
          ) : s.tts_provider === 'azure' ? (
            <>
              <Field label="Azure Speech key">
                <input
                  type="password"
                  autoComplete="off"
                  spellCheck="false"
                  value={s.azure_key}
                  onChange={(e) => field('azure_key', e.target.value)}
                />
              </Field>
              {settings.environment_available?.azure && (
                <Button small icon={KeyRound} onClick={() => switchToEnvironment('azure')}>
                  Dùng AZURE_SPEECH_KEY từ môi trường
                </Button>
              )}
              <div className="grid-two">
                <Field label="Region">
                  <input
                    value={s.azure_region}
                    onChange={(e) => field('azure_region', e.target.value)}
                  />
                </Field>
                <Field label="Giọng">
                  <select
                    value={s.azure_voice}
                    onChange={(e) => field('azure_voice', e.target.value)}
                  >
                    <option>vi-VN-HoaiMyNeural</option>
                    <option>vi-VN-NamMinhNeural</option>
                  </select>
                </Field>
              </div>
            </>
          ) : (
            <Field label="Giọng đã cài">
              <select
                value={s.windows_voice}
                onChange={(e) => field('windows_voice', e.target.value)}
              >
                <option value="">Giọng mặc định</option>
                {voices.map((v) => (
                  <option key={v.name} value={v.name}>
                    {v.name} · {v.culture}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <NumberField
            label="Tốc độ đọc"
            value={s.tts_speed}
            min={0.5}
            max={2}
            step={0.05}
            onChange={(v) => field('tts_speed', v)}
          />
          <h3>Căn phụ đề từ audio</h3>
          <Field label="Bộ nhận dạng">
            <select
              value={s.transcription_provider}
              onChange={(e) => field('transcription_provider', e.target.value)}
            >
              <option value="auto">Tự chọn · OpenAI hoặc Gemini</option>
              <option value="openai">API · word timestamps</option>
              <option value="gemini">Gemini · mốc theo câu/ý</option>
              <option value="local">Faster Whisper · trên máy</option>
            </select>
          </Field>
          {s.transcription_provider === 'gemini' && (
            <p className="helper">
              Dùng API/model Gemini đang bật trong danh sách. Mốc câu do AI ước lượng; có thể chỉnh
              lại sau khi dựng.
            </p>
          )}
          <div className="grid-two">
            <Field label="Model">
              <input
                value={
                  s.transcription_provider === 'local'
                    ? s.local_whisper_model
                    : s.transcription_model
                }
                onChange={(e) =>
                  field(
                    s.transcription_provider === 'local'
                      ? 'local_whisper_model'
                      : 'transcription_model',
                    e.target.value,
                  )
                }
              />
            </Field>
            <Field label="Mã ngôn ngữ">
              <input value={s.language} onChange={(e) => field('language', e.target.value)} />
            </Field>
          </div>
        </div>
        <div className="modal-footer">
          <Button onClick={onClose}>Đóng</Button>
          <Button primary icon={Save} onClick={() => onSave(s)}>
            Lưu cài đặt
          </Button>
        </div>
      </section>
    </div>
  );
}
