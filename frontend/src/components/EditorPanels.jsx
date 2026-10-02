import {
  ArrowRight,
  Check,
  Copy,
  FileText,
  FolderOpen,
  Mic2,
  Music2,
  Plus,
  RefreshCw,
  Save,
  Scissors,
  Send,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { aid, api, clone, fmt, mediaUrl, roleNames, thumbUrl } from '../api/client.js';
import { sourceMasks } from '../previewMedia.js';
import { productValues } from '../templateContent.js';
import { timelineDuration } from '../timelineMath.js';
import { AIProposalPreview } from './AIProposalPreview.jsx';
import { Button, Empty, Field, NumberField } from './common.jsx';
import { SoundEffectsPanel } from './SoundEffectsPanel.jsx';

function SmoothSetting({ p, edit, locked }) {
  return (
    <label className="ai-toggle">
      <input
        type="checkbox"
        disabled={locked}
        checked={!!p.smooth_transitions}
        onChange={(event) =>
          edit((next) => {
            next.smooth_transitions = event.target.checked;
          })
        }
      />
      Dựng mượt · hòa tan ngắn, tránh giữ khung cuối
    </label>
  );
}

export function MediaPanel({ p, upload, locked, run, useAI, setUseAI, edit, addScene, onDelete }) {
  const [role, setRole] = useState('source'),
    [view, setView] = useState('files'),
    [search, setSearch] = useState('');
  const input = useRef();
  const files = p.assets.filter((a) => a.role === role && !a.deleted);
  const scenes = p.assets
    .filter((a) => a.role === 'source' && !a.deleted)
    .flatMap((a) => a.scenes.map((sc) => ({ a, sc })))
    .filter(({ a, sc }) => (sc.tags + ' ' + a.name).toLowerCase().includes(search.toLowerCase()));
  return (
    <>
      <div className="panel-title">
        <h2>Thư viện tư liệu</h2>
        <span>{p.assets.filter((a) => !a.deleted).length} file</span>
      </div>
      <Field label="Nhập tư liệu vào">
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {Object.entries(roleNames).map(([k, v]) => (
            <option value={k} key={k}>
              {v}
            </option>
          ))}
        </select>
      </Field>
      <button
        className="dropzone"
        disabled={locked}
        onClick={() => input.current.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (!locked) upload(e.dataTransfer.files, role);
        }}
      >
        <Upload size={25} />
        <strong>Kéo thả hoặc chọn file</strong>
        <span>Tối đa 10 file mỗi lần · MP4, MOV, ảnh, audio</span>
      </button>
      <input
        ref={input}
        hidden
        type="file"
        multiple
        onChange={(e) => {
          upload(e.target.files, role);
          e.target.value = '';
        }}
        accept="video/*,audio/*,image/*"
      />
      <div className="row spread">
        <div className="segmented">
          <button className={view === 'files' ? 'active' : ''} onClick={() => setView('files')}>
            Tư liệu
          </button>
          <button className={view === 'scenes' ? 'active' : ''} onClick={() => setView('scenes')}>
            Cảnh ({scenes.length})
          </button>
        </div>
        <Button
          small
          icon={Scissors}
          disabled={locked}
          onClick={() => run('analyze', { use_ai: useAI })}
        >
          Phân tích
        </Button>
      </div>
      <label className="ai-toggle">
        <input type="checkbox" checked={useAI} onChange={(e) => setUseAI(e.target.checked)} />
        Dùng AI để hiểu nội dung từng cảnh
      </label>
      <label className="ai-toggle">
        <input
          type="checkbox"
          disabled={locked}
          checked={!!p.avoid_faces}
          onChange={(event) => {
            const enabled = event.target.checked;
            edit((next) => {
              next.avoid_faces = enabled;
            });
            if (enabled) run('filter-faces');
          }}
        />
        Hỗ trợ bản quyền · tránh cảnh có khuôn mặt
      </label>
      <p className="helper">
        Tích để thay cảnh có mặt trên timeline và lọc khi tự dựng, kể cả dựng theo audio. Giữ nguyên
        nhịp và lời đọc. Bộ lọc không xác minh quyền sử dụng tư liệu.
      </p>
      {p.avoid_faces && (
        <Button small disabled={locked} icon={RefreshCw} onClick={() => run('filter-faces')}>
          Lọc lại cảnh có mặt
        </Button>
      )}
      <Field label="Chữ có sẵn trên video nguồn">
        <select
          disabled={locked}
          value={p.source_text_mode || 'cover'}
          onChange={(event) =>
            edit((next) => {
              next.source_text_mode = event.target.value;
            })
          }
        >
          <option value="cover">Che kín vùng chữ</option>
          <option value="blur">Làm mờ vùng chữ</option>
          <option value="off">Giữ chữ gốc</option>
        </select>
      </Field>
      <p className="helper">
        Bật AI và Phân tích để tìm vùng chữ chèn trên video. Có thể chỉnh vùng che trong điều chỉnh
        cảnh; phụ đề mới vẫn được giữ.
      </p>
      {view === 'files' ? (
        <div className="asset-list">
          {!files.length && (
            <Empty icon={FolderOpen} title="Chưa có tư liệu">
              Thêm {roleNames[role].toLowerCase()} để bắt đầu.
            </Empty>
          )}
          {files.map((a) => (
            <article className="asset-card" data-asset-id={a.id} key={a.id}>
              <div className="asset-thumb">
                {a.thumbnail ? (
                  <img src={thumbUrl(p, a.thumbnail)} alt={a.name} />
                ) : (
                  <Music2 size={25} />
                )}
                <span>{a.duration ? a.duration.toFixed(1) + 's' : 'ẢNH'}</span>
              </div>
              <div className="asset-info">
                <strong title={a.name}>{a.name}</strong>
                <small>
                  {a.width ? `${a.width} × ${a.height} · ` : ''}
                  {a.scenes.length} cảnh
                </small>
                <input
                  placeholder="Nhãn: biển, núi, sản phẩm…"
                  aria-label={'Nhãn ' + a.name}
                  value={a.tags}
                  disabled={locked}
                  onChange={(e) =>
                    edit((n) => {
                      n.assets.find((x) => x.id === a.id).tags = e.target.value;
                    })
                  }
                />
              </div>
              <div className="asset-buttons">
                {a.role === 'source' && a.media !== 'audio' && (
                  <button
                    className="icon-button"
                    disabled={locked}
                    title="Thêm vào timeline"
                    onClick={() => addScene(a, a.scenes[0])}
                  >
                    <Plus size={15} />
                  </button>
                )}
                <button
                  className="icon-button"
                  disabled={locked}
                  title="Gỡ tư liệu"
                  onClick={() => onDelete(a.id)}
                >
                  <X size={14} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <>
          <input
            className="search"
            placeholder="Tìm theo nội dung cảnh…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="scene-grid">
            {scenes.map(({ a, sc }) => (
              <button
                key={sc.id}
                className="scene-card"
                disabled={locked}
                onClick={() => addScene(a, sc)}
                title={sc.tags}
              >
                <img src={thumbUrl(p, sc.thumbnail)} alt={sc.tags} />
                <span>
                  {fmt(sc.start)} · {(sc.end - sc.start).toFixed(1)}s
                </span>
                <small>{sc.tags}</small>
                <Plus size={14} />
              </button>
            ))}
          </div>
        </>
      )}
      <p className="helper">
        Phân tích một lần; các lần dựng sau dùng lại thư viện cảnh. Video nguồn được tắt tiếng khi
        ghép với voice và nhạc.
      </p>
    </>
  );
}

export function ScriptPanel({ p, edit, locked, run, useAI, onRegenerateCues, onConfirmCues }) {
  const regenerate = () => {
    if (
      (p.cues_edited || p.cues_stale) &&
      p.cues.length &&
      !window.confirm(
        'Tạo lại sẽ thay toàn bộ nội dung và thời gian của các cue hiện tại. Tiếp tục?',
      )
    )
      return;
    onRegenerateCues();
  };
  return (
    <>
      <div className="panel-title">
        <h2>Câu chuyện của bạn</h2>
        <FileText size={20} />
      </div>
      <p className="helper">
        Mỗi câu mô tả một ý. Timeline sẽ chọn hình phù hợp với từng đoạn lời đọc.
      </p>
      <textarea
        className="script-input"
        value={p.script}
        disabled={locked}
        placeholder="Dán kịch bản 30–45 giây tại đây…"
        onChange={(e) =>
          edit((n) => {
            n.script = e.target.value;
            if (n.cues.length) n.cues_stale = true;
          })
        }
      />
      <div className="row spread">
        <small className="muted">
          {p.script.trim().split(/\s+/).filter(Boolean).length} từ · {p.script.length} ký tự
        </small>
        <span className="count-pill">
          {p.cues_stale
            ? 'Kịch bản đổi · cần xem lại'
            : p.cues_edited
              ? 'Đã chỉnh thủ công'
              : p.cue_timing === 'estimated'
                ? 'Mốc ước lượng'
                : 'Đã có mốc voice'}
        </span>
      </div>
      <NumberField
        label="Thời lượng mong muốn (giây)"
        value={p.target_duration}
        min={5}
        max={180}
        step={1}
        onChange={(v) => edit((n) => (n.target_duration = v))}
      />
      <p className="helper">
        Mặc định 36 giây. Nếu voice dài hơn, công cụ giữ trọn lời đọc và kéo dài timeline.
      </p>
      <div className="button-stack">
        <Button icon={Mic2} disabled={locked || !p.script.trim()} onClick={() => run('voice')}>
          Tạo giọng từ kịch bản
        </Button>
        <Button
          primary
          icon={Sparkles}
          disabled={locked}
          onClick={() => run('plan', { use_ai: useAI })}
        >
          Ghép cảnh theo kịch bản
        </Button>
      </div>
      <SmoothSetting p={p} edit={edit} locked={locked} />
      {p.cues_stale && (
        <div className="cue-stale">
          <span>Kịch bản đã đổi. Có thể sửa cue hoặc xác nhận giữ nguyên các mốc hiện tại.</span>
          <div className="row">
            <Button small disabled={locked || !p.script.trim()} onClick={onConfirmCues}>
              Đã kiểm tra, giữ cue
            </Button>
            <Button small disabled={locked || !p.script.trim()} onClick={regenerate}>
              Tạo lại cue
            </Button>
          </div>
        </div>
      )}
      <details className="advanced" data-cue-editor>
        <summary>Chỉnh mốc phụ đề ({p.cues.length})</summary>
        <div className="cue-actions">
          <small>
            Mốc gốc: {p.cue_timing === 'estimated' ? 'ước lượng theo kịch bản' : p.cue_timing}.
            Chỉnh cue sẽ đánh dấu đã xem; thao tác tạo lại thay toàn bộ cue hiện tại.
          </small>
          <Button small icon={RefreshCw} disabled={locked || !p.script.trim()} onClick={regenerate}>
            Tạo lại mốc
          </Button>
        </div>
        <div className="cue-list">
          {p.cues.map((c, i) => (
            <div className="cue-row" data-cue-id={String(i + 1)} key={i}>
              <input
                aria-label="Bắt đầu phụ đề"
                type="number"
                step=".1"
                value={c.start.toFixed(2)}
                onChange={(e) =>
                  edit((n) => {
                    n.cues[i].start = Number(e.target.value);
                    n.cues_edited = true;
                    n.cues_stale = false;
                  })
                }
              />
              <input
                aria-label="Kết thúc phụ đề"
                type="number"
                step=".1"
                value={c.end.toFixed(2)}
                onChange={(e) =>
                  edit((n) => {
                    n.cues[i].end = Number(e.target.value);
                    n.cues_edited = true;
                    n.cues_stale = false;
                  })
                }
              />
              <input
                aria-label="Nội dung phụ đề"
                value={c.text}
                onChange={(e) =>
                  edit((n) => {
                    n.cues[i].text = e.target.value;
                    n.cues_edited = true;
                    n.cues_stale = false;
                  })
                }
              />
            </div>
          ))}
        </div>
      </details>
    </>
  );
}

export function TemplatePanel({
  p,
  edit,
  locked,
  templates,
  run,
  useAI,
  setUseAI,
  fonts = ['Arial'],
  onSave,
  applyTemplate,
}) {
  const [ref, setRef] = useState(''),
    [json, setJson] = useState(''),
    [jsonError, setJsonError] = useState(''),
    [replacements, setReplacements] = useState({});
  const t = p.template;
  const addLayer = (kind) =>
    edit((n) =>
      n.template.layers.push({
        id: crypto.randomUUID().slice(0, 8),
        kind,
        x: 0.1,
        y: 0.1,
        w: kind === 'circle' ? 0.15 : 0.8,
        h: 0.12,
        text: kind === 'text' ? '{title}' : '',
        size: 52,
        color: '#ffffff',
        background: '#101010',
        opacity: 1,
        asset_id: '',
        animation: 'none',
      }),
    );
  return (
    <>
      <div className="panel-title">
        <h2>Mẫu dựng</h2>
        <Button small icon={Save} disabled={locked} onClick={onSave}>
          Lưu mẫu
        </Button>
      </div>
      <div className="template-grid">
        {templates.map((x) => {
          const template = x.template || x,
            missing = x.missing_assets || [],
            selectedMap = Object.fromEntries(
              missing.map((item) => [item.id, replacements[`${x.id}:${item.id}`] || '']),
            );
          return (
            <div key={x.id} className="template-choice">
              <button
                className={t.id === template.id ? 'active' : ''}
                disabled={locked}
                onClick={() =>
                  x.built_in
                    ? edit((n) => {
                        n.template = clone(template);
                        n.mode = 'template';
                      })
                    : missing.length === 0 && applyTemplate(x.id, {})
                }
              >
                <div
                  className={
                    'template-art ' +
                    (template.id === 'editorial' ? 'paper' : template.id === 'bold' ? 'bold' : '')
                  }
                >
                  <div />
                  <i />
                  <b>Aa</b>
                </div>
                <span>{x.name || template.name}</span>
                {x.saved && <small>Đã lưu</small>}
              </button>
              {missing.map((item) => (
                <Field key={item.id} label={'Thiếu ' + item.name}>
                  <select
                    value={selectedMap[item.id]}
                    disabled={locked}
                    onChange={(e) =>
                      setReplacements((old) => ({ ...old, [`${x.id}:${item.id}`]: e.target.value }))
                    }
                  >
                    <option value="">Chọn ảnh thay thế</option>
                    {p.assets
                      .filter((a) => a.media === 'image' && !a.deleted)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                  </select>
                </Field>
              ))}
              {!x.built_in && missing.length > 0 && (
                <Button
                  small
                  disabled={locked || missing.some((item) => !selectedMap[item.id])}
                  onClick={() => applyTemplate(x.id, selectedMap)}
                >
                  Áp dụng template
                </Button>
              )}
            </div>
          );
        })}
      </div>
      <div className="subsection">
        <h3>Từ video mẫu của bạn</h3>
        <select value={ref} onChange={(e) => setRef(e.target.value)}>
          <option value="">Chọn video đã nhập ở vai trò Video mẫu</option>
          {p.assets
            .filter((a) => a.role === 'reference' && !a.deleted)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
        <label className="ai-toggle">
          <input type="checkbox" checked={useAI} onChange={(e) => setUseAI(e.target.checked)} />
          AI nhận diện bố cục, chữ và hình khối
        </label>
        <Button
          icon={Sparkles}
          disabled={locked || !ref}
          onClick={() => run('template', { asset_id: ref, use_ai: useAI })}
        >
          Phân tích mẫu
        </Button>
        {t.notes && <p className="helper">{t.notes}</p>}
      </div>
      <Field label="Tên mẫu">
        <input value={t.name} onChange={(e) => edit((n) => (n.template.name = e.target.value))} />
      </Field>
      <div className="subsection">
        <h3>Nội dung sản phẩm</h3>
        <p className="helper">
          Tên và mô tả cập nhật theo nguồn mới. Bật AI khi phân tích/tự dựng để nhận diện sản phẩm
          từ hình và kịch bản; nội dung bạn nhập bên dưới được ưu tiên.
        </p>
        <Field label="Tên sản phẩm · để trống để lấy tên source mới nhất">
          <input
            disabled={locked}
            value={p.product_name || ''}
            placeholder={productValues(p).product_name}
            onChange={(e) =>
              edit((next) => {
                next.product_name = e.target.value;
              })
            }
          />
        </Field>
        <Field label="Mô tả · để trống để dùng kịch bản hoặc nhãn source">
          <textarea
            disabled={locked}
            rows={3}
            value={p.product_description || ''}
            placeholder={productValues(p).product_description}
            onChange={(e) =>
              edit((next) => {
                next.product_description = e.target.value;
              })
            }
          />
        </Field>
        <Field label="Lời kêu gọi">
          <input
            disabled={locked}
            value={p.product_cta ?? 'KHÁM PHÁ SẢN PHẨM'}
            onChange={(e) =>
              edit((next) => {
                next.product_cta = e.target.value;
              })
            }
          />
        </Field>
      </div>
      <div className="row">
        <Field label="Màu nền">
          <input
            type="color"
            value={t.background}
            onChange={(e) => edit((n) => (n.template.background = e.target.value))}
          />
        </Field>
        <Field label="Chuyển cảnh">
          <select
            value={t.transition}
            onChange={(e) =>
              edit((n) => {
                n.template.transition = e.target.value;
                n.smooth_transitions = false;
                n.clips.forEach((c) => {
                  c.transition = e.target.value;
                  if (e.target.value === 'crossfade')
                    c.transition_duration = n.template.transition_duration;
                });
              })
            }
          >
            <option value="cut">Cắt thẳng</option>
            <option value="fade">Mờ qua nền</option>
            <option value="crossfade">Crossfade</option>
          </select>
        </Field>
      </div>
      <SmoothSetting p={p} edit={edit} locked={locked} />
      {t.transition === 'crossfade' && (
        <NumberField
          label="Overlap crossfade (giây)"
          value={t.transition_duration}
          min={0.08}
          max={1.5}
          step={0.05}
          onChange={(value) =>
            edit((n) => {
              n.template.transition_duration = value;
              n.clips.forEach((clip) => {
                if (clip.transition === 'crossfade') clip.transition_duration = value;
              });
            })
          }
        />
      )}
      <details className="advanced">
        <summary>Vị trí khung video</summary>
        <div className="grid-two">
          {['x', 'y', 'w', 'h'].map((k) => (
            <NumberField
              key={k}
              label={{ x: 'Trái', y: 'Trên', w: 'Rộng', h: 'Cao' }[k] + ' (0–1)'}
              value={t.viewport[k]}
              max={1}
              step={0.01}
              onChange={(v) => edit((n) => (n.template.viewport[k] = v))}
            />
          ))}
        </div>
      </details>
      <div className="row spread">
        <h3>Các lớp trong mẫu</h3>
        <div className="row">
          <Button small onClick={() => addLayer('text')}>
            + Chữ
          </Button>
          <Button small onClick={() => addLayer('rect')}>
            + Khối
          </Button>
          <Button small onClick={() => addLayer('image')}>
            + Logo
          </Button>
        </div>
      </div>
      {t.layers.map((l, i) => (
        <details className="layer-card" data-layer-id={l.id} key={l.id}>
          <summary>
            <span>
              {l.kind === 'text' ? 'T' : l.kind === 'image' ? '▧' : '■'} · {l.text || l.kind}
            </span>
            <button
              aria-label="Xóa lớp"
              onClick={(e) => {
                e.preventDefault();
                edit((n) => n.template.layers.splice(i, 1));
              }}
            >
              <Trash2 size={13} />
            </button>
          </summary>
          <LayerEditor
            layer={l}
            p={p}
            fonts={fonts}
            onChange={(k, v) => edit((n) => (n.template.layers[i][k] = v))}
          />
        </details>
      ))}
      <CaptionEditor
        style={t.caption}
        fonts={fonts}
        onChange={(k, v) => edit((n) => (n.template.caption[k] = v))}
      />
      <details
        className="advanced"
        data-template-layout
        onToggle={(e) => {
          if (e.target.open) setJson(JSON.stringify(t, null, 2));
        }}
      >
        <summary>Template JSON / bố cục từng ô</summary>
        <textarea className="code-input" value={json} onChange={(e) => setJson(e.target.value)} />
        {jsonError && <p className="inline-error">{jsonError}</p>}
        <Button
          small
          onClick={async () => {
            try {
              const parsed = await api('/templates/validate', JSON.parse(json));
              edit((n) => (n.template = parsed));
              setJsonError('');
            } catch (e) {
              setJsonError(e.message);
            }
          }}
        >
          Áp dụng JSON
        </Button>
      </details>
    </>
  );
}

export function LayerEditor({ layer: l, p, onChange, fonts = ['Arial'] }) {
  return (
    <>
      <Field label="Loại lớp">
        <select
          value={l.kind}
          onChange={(e) => {
            onChange('kind', e.target.value);
            if (e.target.value !== 'image') onChange('asset_id', '');
          }}
        >
          {['text', 'rect', 'circle', 'image'].map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
      </Field>
      {l.kind === 'text' && (
        <>
          <Field label="Nội dung lấy từ">
            <select
              value={l.content || 'static'}
              onChange={(e) => onChange('content', e.target.value)}
            >
              <option value="static">Chữ tùy chỉnh</option>
              <option value="product_name">Tên sản phẩm mới</option>
              <option value="product_description">Mô tả sản phẩm mới</option>
              <option value="cta">Lời kêu gọi</option>
            </select>
          </Field>
          <Field label="Nội dung · {title}, {project}, {caption}, {product_name}, {product_description}">
            <input value={l.text} onChange={(e) => onChange('text', e.target.value)} />
          </Field>
          <Field label="Font chữ">
            <select
              value={l.font_family || 'Arial'}
              onChange={(e) => onChange('font_family', e.target.value)}
            >
              {[...new Set([l.font_family || 'Arial', ...fonts])].map((family) => (
                <option key={family} value={family}>
                  {family}
                </option>
              ))}
            </select>
          </Field>
        </>
      )}
      {l.kind === 'image' && (
        <Field label="Logo / biểu tượng">
          <select value={l.asset_id} onChange={(e) => onChange('asset_id', e.target.value)}>
            <option value="">Chọn ảnh đã nhập</option>
            {p.assets
              .filter((a) => a.media === 'image' && !a.deleted)
              .map((a) => (
                <option value={a.id} key={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </Field>
      )}
      <div className="grid-two">
        {['x', 'y', 'w', 'h'].map((k) => (
          <NumberField
            key={k}
            label={k}
            value={l[k]}
            max={1}
            step={0.01}
            onChange={(v) => onChange(k, v)}
          />
        ))}
      </div>
      <div className="row">
        <Field label="Màu">
          <input type="color" value={l.color} onChange={(e) => onChange('color', e.target.value)} />
        </Field>
        <NumberField
          label="Cỡ chữ"
          value={l.size}
          min={12}
          max={200}
          step={1}
          onChange={(v) => onChange('size', v)}
        />
      </div>
      <Field label="Chuyển động">
        <select value={l.animation} onChange={(e) => onChange('animation', e.target.value)}>
          <option value="none">Tĩnh</option>
          <option value="fade">Hiện dần</option>
          <option value="slide">Trượt vào</option>
        </select>
      </Field>
      <Field label="Độ đậm">
        <input
          type="range"
          min="0"
          max="1"
          step=".05"
          value={l.opacity}
          onChange={(e) => onChange('opacity', Number(e.target.value))}
        />
      </Field>
    </>
  );
}
export function CaptionEditor({ style: s, onChange, fonts = ['Arial'] }) {
  return (
    <div className="subsection">
      <h3>Phụ đề</h3>
      <label className="ai-toggle">
        <input
          type="checkbox"
          checked={s.enabled}
          onChange={(e) => onChange('enabled', e.target.checked)}
        />
        Hiện phụ đề trên video
      </label>
      <div className="row">
        <NumberField
          label="Cỡ chữ"
          value={s.font_size}
          min={18}
          max={120}
          step={1}
          onChange={(v) => onChange('font_size', v)}
        />
        <Field label="Màu chữ">
          <input type="color" value={s.color} onChange={(e) => onChange('color', e.target.value)} />
        </Field>
      </div>
      <Field label="Font phụ đề">
        <select
          value={s.font_family || 'Arial'}
          onChange={(e) => onChange('font_family', e.target.value)}
        >
          {[...new Set([s.font_family || 'Arial', ...fonts])].map((family) => (
            <option key={family} value={family}>
              {family}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Vùng an toàn">
        <select
          value={s.safe_area || 'standard'}
          onChange={(e) => {
            const preset = e.target.value;
            const bottom = { standard: 0.08, reels: 0.2, cinematic: 0.12 }[preset];
            onChange('safe_area', preset);
            onChange('bottom', bottom);
          }}
        >
          <option value="standard">Tiêu chuẩn</option>
          <option value="reels">Reels / Shorts</option>
          <option value="cinematic">Điện ảnh</option>
        </select>
      </Field>
      <NumberField
        label="Khoảng cách từ đáy (0–1)"
        value={s.bottom}
        min={0.02}
        max={0.8}
        step={0.01}
        onChange={(v) => onChange('bottom', v)}
      />
      <NumberField
        label="Số từ mỗi nhóm"
        value={s.words_per_line}
        min={2}
        max={12}
        step={1}
        onChange={(v) => onChange('words_per_line', v)}
      />
      <label className="ai-toggle">
        <input
          type="checkbox"
          checked={s.karaoke}
          onChange={(e) => onChange('karaoke', e.target.checked)}
        />
        Tô sáng theo từ (karaoke)
      </label>
      {s.karaoke && (
        <Field label="Màu tô sáng">
          <input
            type="color"
            value={s.highlight}
            onChange={(e) => onChange('highlight', e.target.value)}
          />
        </Field>
      )}
    </div>
  );
}

export function AudioPanel({
  p,
  edit,
  locked,
  run,
  upload,
  settings,
  onSettings,
  cursor,
  duration,
}) {
  return (
    <>
      <div className="panel-title">
        <h2>Giọng đọc & nhạc</h2>
        <Volume2 size={20} />
      </div>
      <label className="ai-toggle">
        <input
          type="checkbox"
          disabled={locked}
          checked={p.auto_audio_assembly !== false}
          onChange={(event) =>
            edit((next) => {
              next.auto_audio_assembly = event.target.checked;
            })
          }
        />
        Tự phân tích và ghép cảnh khi tải audio lên
      </label>
      <SmoothSetting p={p} edit={edit} locked={locked} />
      <p className="helper">
        Nhập voice để nhận dạng lời đọc, phân tích tư liệu và ghép theo từng ý. Audio và ảnh cảnh
        được gửi tới API đã cấu hình; có thể dùng Gemini hiện tại hoặc nhận dạng cục bộ.
      </p>
      <div className="voice-provider">
        <Mic2 size={22} />
        <div>
          <b>
            {settings?.tts_provider === 'azure'
              ? 'Azure · Tiếng Việt'
              : settings?.tts_provider === 'windows'
                ? 'Giọng trên Windows'
                : 'OpenAI Speech'}
          </b>
          <small>
            {settings?.tts_provider === 'azure'
              ? settings.azure_voice
              : settings?.tts_provider === 'windows'
                ? settings.windows_voice || 'Giọng mặc định'
                : settings?.tts_voice}
          </small>
        </div>
        <Button small onClick={onSettings}>
          Đổi
        </Button>
      </div>
      <Button
        primary
        icon={Sparkles}
        disabled={locked || !p.script.trim()}
        onClick={() => run('voice')}
      >
        Tạo giọng từ kịch bản
      </Button>
      <p className="helper">
        Giọng dịch vụ cần API key và Internet. Giọng Windows dùng các giọng đã cài trên máy; có thể
        chưa có tiếng Việt.
      </p>
      <Field label="Giọng đọc đang dùng">
        <select
          value={p.voice_id}
          disabled={locked}
          onChange={(e) => edit((n) => (n.voice_id = e.target.value))}
        >
          <option value="">Không có giọng đọc</option>
          {p.assets
            .filter((a) => a.role === 'voice' && !a.deleted)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
      </Field>
      <label className="upload-inline">
        <Upload size={15} />
        Nhập file voice
        <input
          type="file"
          hidden
          accept="audio/*,video/*"
          disabled={locked}
          onChange={(e) => upload(e.target.files, 'voice')}
        />
      </label>
      {p.voice_id && (
        <>
          <audio controls src={mediaUrl(p, p.voice_id)} />
          <Button primary icon={Sparkles} disabled={locked} onClick={() => run('assemble-audio')}>
            Phân tích audio & ghép cảnh
          </Button>
          <Button
            small
            icon={FileText}
            disabled={locked}
            onClick={() => run('transcribe', { asset_id: p.voice_id })}
          >
            Căn phụ đề từ voice
          </Button>
          <p className="helper">
            Nhận dạng lại lời đọc. Gemini lấy mốc theo câu; Whisper hỗ trợ mốc từng từ.
          </p>
        </>
      )}
      <Field label={'Âm lượng voice · ' + Math.round(p.voice_volume * 100) + '%'}>
        <input
          type="range"
          min="0"
          max="2"
          step=".05"
          value={p.voice_volume}
          onChange={(e) => edit((n) => (n.voice_volume = Number(e.target.value)))}
        />
      </Field>
      <Field label={'Âm thanh từ video gốc · ' + Math.round((p.source_volume || 0) * 100) + '%'}>
        <input
          type="range"
          disabled={locked}
          min="0"
          max="1"
          step=".01"
          value={p.source_volume || 0}
          onChange={(e) => edit((n) => (n.source_volume = Number(e.target.value)))}
        />
      </Field>
      <div className="subsection">
        <h3>Nhạc nền</h3>
        <select value={p.music_id} onChange={(e) => edit((n) => (n.music_id = e.target.value))}>
          <option value="">Không có nhạc</option>
          {p.assets
            .filter((a) => a.role === 'music' && !a.deleted)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
        <label className="upload-inline">
          <Upload size={15} />
          Nhập nhạc nền
          <input
            type="file"
            hidden
            accept="audio/*"
            disabled={locked}
            onChange={(e) => upload(e.target.files, 'music')}
          />
        </label>
        {p.music_id && <audio controls src={mediaUrl(p, p.music_id)} />}
        <Field label={'Âm lượng nhạc · ' + Math.round(p.music_volume * 100) + '%'}>
          <input
            type="range"
            min="0"
            max="1"
            step=".01"
            value={p.music_volume}
            onChange={(e) => edit((n) => (n.music_volume = Number(e.target.value)))}
          />
        </Field>
        <label className="ai-toggle">
          <input
            type="checkbox"
            checked={p.ducking}
            onChange={(e) => edit((n) => (n.ducking = e.target.checked))}
          />
          Tự giảm nhạc khi có lời đọc
        </label>
        <p className="helper">Nhạc được lặp để đủ thời lượng và nhỏ dần ở cuối video.</p>
      </div>
      <SoundEffectsPanel {...{ p, edit, locked, cursor, duration }} />
    </>
  );
}

export function ClipInspector({ p, selected, edit, locked }) {
  const c = p.clips[selected];
  if (!c)
    return (
      <Empty icon={SlidersHorizontal} title="Chọn một cảnh">
        Các điều chỉnh sẽ hiện ở đây sau khi tạo timeline.
      </Empty>
    );
  const change = (k, v) => edit((n) => (n.clips[selected][k] = v));
  const a = aid(p, c.asset_id);
  const masks = sourceMasks(
    { ...p, source_text_mode: 'cover' },
    { ...c, text_mode: 'cover' },
    a || { scenes: [] },
  );
  const changeMasks = (regions) =>
    edit((next) => {
      next.clips[selected].text_regions = regions.map(({ x, y, w, h }) => ({ x, y, w, h }));
      next.clips[selected].text_regions_override = true;
    });
  const projectDuration = timelineDuration(p.clips);
  return (
    <fieldset disabled={locked || c.locked} className="inspector-fields">
      <Field label="Tư liệu">
        <select
          value={c.asset_id}
          onChange={(e) =>
            edit((n) => {
              n.clips[selected].asset_id = e.target.value;
              n.clips[selected].source_start = 0;
              n.clips[selected].scene_id = '';
              n.clips[selected].text_regions = [];
              n.clips[selected].text_regions_override = false;
            })
          }
        >
          {p.assets
            .filter((a) => a.media !== 'audio' && a.role === 'source' && !a.deleted)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
      </Field>
      <Field label="Thay bằng cảnh">
        <select
          value={c.scene_id}
          onChange={(e) => {
            const sc = a.scenes.find((s) => s.id === e.target.value);
            if (sc)
              edit((n) => {
                n.clips[selected].scene_id = sc.id;
                n.clips[selected].source_start = sc.start;
                n.clips[selected].text_regions = [];
                n.clips[selected].text_regions_override = false;
              });
          }}
        >
          <option value="">Tùy chỉnh</option>
          {a?.scenes.map((sc, i) => (
            <option value={sc.id} key={sc.id}>
              Cảnh {i + 1} · {fmt(sc.start)} · {sc.tags.slice(0, 25)}
            </option>
          ))}
        </select>
      </Field>
      <div className="grid-two">
        <NumberField
          label="Cắt từ (s)"
          value={c.source_start}
          max={a?.duration || 0}
          step={0.1}
          onChange={(v) => {
            change('source_start', v);
          }}
        />
        <NumberField
          label="Dài (s)"
          value={c.duration}
          min={0.2}
          max={180}
          step={0.1}
          onChange={(v) => change('duration', v)}
        />
      </div>
      <Field label="Tiêu đề trong mẫu">
        <textarea rows="2" value={c.title} onChange={(e) => change('title', e.target.value)} />
      </Field>
      <Field label="Ghi đè phụ đề cảnh" hint="Để trống để dùng phụ đề theo voice.">
        <textarea rows="2" value={c.caption} onChange={(e) => change('caption', e.target.value)} />
      </Field>
      <div className="grid-two">
        <Field label="Khung hình">
          <select value={c.fit} onChange={(e) => change('fit', e.target.value)}>
            <option value="cover">Lấp đầy / cắt</option>
            <option value="contain">Giữ trọn hình</option>
          </select>
        </Field>
        <NumberField
          label="Tốc độ"
          value={c.speed}
          min={0.25}
          max={4}
          step={0.05}
          onChange={(v) => change('speed', v)}
        />
      </div>
      <Field label="Vị trí cắt ngang">
        <input
          type="range"
          min="0"
          max="1"
          step=".01"
          value={c.crop_x}
          onChange={(e) => change('crop_x', Number(e.target.value))}
        />
      </Field>
      <Field label="Vị trí cắt dọc">
        <input
          type="range"
          min="0"
          max="1"
          step=".01"
          value={c.crop_y}
          onChange={(e) => change('crop_y', Number(e.target.value))}
        />
      </Field>
      <Field label="Chuyển cảnh">
        <select value={c.transition} onChange={(e) => change('transition', e.target.value)}>
          <option value="cut">Cắt thẳng</option>
          <option value="fade">Mờ qua nền</option>
          <option value="crossfade">Crossfade</option>
        </select>
      </Field>
      {c.transition === 'crossfade' && (
        <NumberField
          label="Overlap trước cảnh (giây)"
          value={c.transition_duration || 0.4}
          min={0.08}
          max={1.5}
          step={0.05}
          onChange={(value) => change('transition_duration', value)}
        />
      )}
      <details className="advanced">
        <summary>Che chữ gốc ({masks.length} vùng)</summary>
        <Field label="Xử lý chữ trong cảnh này">
          <select
            value={c.text_mode || 'inherit'}
            onChange={(event) => change('text_mode', event.target.value)}
          >
            <option value="inherit">Theo cài đặt tư liệu</option>
            <option value="cover">Che kín</option>
            <option value="blur">Làm mờ</option>
            <option value="off">Giữ chữ gốc</option>
          </select>
        </Field>
        <p className="helper">
          Vị trí tính theo % khung hình video gốc, trước khi cắt khung. AI tìm vùng chữ trên ảnh đại
          diện; rà lại nếu chữ di chuyển.
        </p>
        {masks.map((region, index) => (
          <div className="text-region-editor" key={index}>
            <div className="grid-two">
              {['x', 'y', 'w', 'h'].map((key) => (
                <NumberField
                  key={key}
                  label={{ x: 'Trái (%)', y: 'Trên (%)', w: 'Rộng (%)', h: 'Cao (%)' }[key]}
                  value={Math.round(region[key] * 1000) / 10}
                  min={key === 'w' || key === 'h' ? 1 : 0}
                  max={
                    (key === 'w'
                      ? 1 - region.x
                      : key === 'h'
                        ? 1 - region.y
                        : key === 'x'
                          ? 1 - region.w
                          : 1 - region.h) * 100
                  }
                  step={0.5}
                  onChange={(value) => {
                    const regions = masks.map(({ x, y, w, h }) => ({ x, y, w, h }));
                    regions[index][key] = Math.max(
                      key === 'w' || key === 'h' ? 0.01 : 0,
                      Math.min(
                        value / 100,
                        key === 'w'
                          ? 1 - region.x
                          : key === 'h'
                            ? 1 - region.y
                            : key === 'x'
                              ? 1 - region.w
                              : 1 - region.h,
                      ),
                    );
                    changeMasks(regions);
                  }}
                />
              ))}
            </div>
            <Button
              small
              icon={Trash2}
              onClick={() => changeMasks(masks.filter((_, item) => item !== index))}
            >
              Bỏ vùng {index + 1}
            </Button>
          </div>
        ))}
        <Button
          small
          icon={Plus}
          disabled={masks.length >= 12}
          onClick={() => changeMasks([...masks, { x: 0.05, y: 0.8, w: 0.9, h: 0.15 }])}
        >
          Thêm vùng che chữ
        </Button>
        {c.text_regions_override && (
          <Button
            small
            onClick={() =>
              edit((next) => {
                next.clips[selected].text_regions = [];
                next.clips[selected].text_regions_override = false;
              })
            }
          >
            Dùng lại vùng AI
          </Button>
        )}
      </details>
      <div className="row">
        <Button
          small
          icon={Copy}
          disabled={
            locked || c.locked || p.clips.length >= 150 || projectDuration + c.duration > 180
          }
          onClick={() =>
            edit((n) => {
              const cp = clone(n.clips[selected]);
              cp.id = crypto.randomUUID().replaceAll('-', '').slice(0, 16);
              cp.locked = false;
              n.clips.splice(selected + 1, 0, cp);
            })
          }
        >
          Nhân đôi
        </Button>
        <Button
          small
          icon={Trash2}
          disabled={locked || c.locked}
          onClick={() => edit((n) => n.clips.splice(selected, 1))}
        >
          Xóa
        </Button>
      </div>
    </fieldset>
  );
}

export function AssistantPanel({
  p,
  locked,
  dirty,
  run,
  proposal,
  onApply,
  onPreview,
  onSettings,
}) {
  const [prompt, setPrompt] = useState('');
  const [selected, setSelected] = useState({ script: false, music_volume: false, clips: [] });
  const [review, setReview] = useState(null);
  const [reviewError, setReviewError] = useState('');
  const [reviewBusy, setReviewBusy] = useState(false);
  useEffect(() => {
    setSelected({
      script: !!proposal?.script,
      music_volume: proposal?.music_volume != null,
      clips: proposal?.clip_changes?.map((change) => change.id) || [],
    });
    setReview(null);
    setReviewError('');
  }, [proposal?.proposal_id]);
  const selectionPayload = proposal
    ? {
        revision: proposal.revision,
        script: selected.script ? proposal.script : null,
        music_volume: selected.music_volume ? proposal.music_volume : null,
        clip_changes: proposal.clip_changes.filter((change) => selected.clips.includes(change.id)),
      }
    : null;
  const signature = selectionPayload ? JSON.stringify(selectionPayload) : '';
  const canSelect = selected.script || selected.music_volume || selected.clips.length > 0;
  const clipImage = (clip) => {
    const asset = aid(p, clip?.asset_id);
    const scene = asset?.scenes.find((item) => item.id === clip.scene_id);
    return asset && thumbUrl(p, scene?.thumbnail || asset.thumbnail);
  };
  const clipDescription = (clip) => {
    const asset = aid(p, clip?.asset_id);
    const scene = asset?.scenes.find((item) => item.id === clip.scene_id);
    return [
      asset?.name || 'Không xác định tư liệu',
      scene?.tags || (scene ? `Cảnh ${scene.start}s–${scene.end}s` : ''),
    ]
      .filter(Boolean)
      .join(' · ');
  };
  return (
    <>
      <div className="panel-title">
        <h2>Trợ lý dựng video</h2>
        <Sparkles size={20} />
      </div>
      <div className="assistant-intro">
        <div className="ai-orb">
          <Sparkles size={26} />
        </div>
        <h3>Bạn muốn thay đổi điều gì?</h3>
        <p>
          AI đề xuất thay cảnh, chỉnh chữ, kịch bản hoặc âm lượng. Bạn xem trước từng mục trước khi
          áp dụng.
        </p>
      </div>
      <div className="suggestion-list">
        {[
          'Viết phần mở đầu cuốn hút hơn, giữ video khoảng 36 giây.',
          'Giảm nhạc nền còn 10%.',
          'Rút ngắn tiêu đề các cảnh còn tối đa 6 từ.',
        ].map((text) => (
          <button key={text} onClick={() => setPrompt(text)}>
            {text}
            <ArrowRight size={14} />
          </button>
        ))}
      </div>
      <textarea
        rows="4"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Ví dụ: đổi tiêu đề cảnh 3 thành…"
      />
      <Button
        primary
        icon={Send}
        disabled={locked || !prompt.trim()}
        onClick={() => run('assistant', { prompt, revision: p.revision })}
      >
        Gửi yêu cầu
      </Button>
      <p className="helper">
        Kịch bản và danh sách cảnh sẽ được gửi đến API bạn cấu hình.{' '}
        <button className="text-link" onClick={onSettings}>
          Cài đặt kết nối
        </button>
      </p>
      {proposal && (
        <div className="proposal">
          <h3>Đề xuất chỉnh sửa</h3>
          <p>{proposal.message}</p>
          {proposal.provider && (
            <small className="provider-telemetry">
              {proposal.provider.provider} · {proposal.provider.model}
              {proposal.provider.fallback_used ? ' · đã chuyển dự phòng' : ''}
              {' · '}
              {proposal.provider.call_count || 1} lần gọi
              {' · '}
              {(
                (proposal.provider.total_elapsed_ms || proposal.provider.elapsed_ms || 0) / 1000
              ).toFixed(1)}
              s
              {proposal.provider.total_usage?.total_tokens
                ? ` · ${proposal.provider.total_usage.total_tokens} tokens`
                : ''}
            </small>
          )}
          {proposal.script && (
            <label className="proposal-choice">
              <input
                type="checkbox"
                checked={selected.script}
                onChange={(event) => {
                  setSelected((old) => ({ ...old, script: event.target.checked }));
                  setReview(null);
                  setReviewError('');
                }}
              />
              <span>
                <b>Kịch bản</b>
                <blockquote>{proposal.script}</blockquote>
              </span>
            </label>
          )}
          {proposal.music_volume != null && (
            <label className="proposal-choice">
              <input
                type="checkbox"
                checked={selected.music_volume}
                onChange={(event) => {
                  setSelected((old) => ({ ...old, music_volume: event.target.checked }));
                  setReview(null);
                  setReviewError('');
                }}
              />
              <span>Nhạc nền: {Math.round(proposal.music_volume * 100)}%</span>
            </label>
          )}
          {proposal.clip_changes?.map((change) => {
            const before = p.clips.find((clip) => clip.id === change.id);
            const after = { ...before, ...change };
            return (
              <label className="proposal-clip" key={change.id}>
                <input
                  type="checkbox"
                  checked={selected.clips.includes(change.id)}
                  onChange={(event) => {
                    setSelected((old) => ({
                      ...old,
                      clips: event.target.checked
                        ? [...old.clips, change.id]
                        : old.clips.filter((id) => id !== change.id),
                    }));
                    setReview(null);
                    setReviewError('');
                  }}
                />
                <span className="proposal-clip-content">
                  <b>Cảnh {p.clips.findIndex((clip) => clip.id === change.id) + 1}</b>
                  <span className="proposal-compare">
                    <span>
                      <small>Hiện tại</small>
                      {before && clipImage(before) && (
                        <img src={clipImage(before)} alt="Cảnh hiện tại" />
                      )}
                      <em>{before?.title || 'Không có tiêu đề'}</em>
                      <small>{clipDescription(before)}</small>
                    </span>
                    <span>
                      <small>Đề xuất</small>
                      {clipImage(after) && <img src={clipImage(after)} alt="Cảnh đề xuất" />}
                      <em>{after?.title || before?.title || 'Không đổi tiêu đề'}</em>
                      <small>{clipDescription(after)}</small>
                    </span>
                  </span>
                  {change.caption != null && (
                    <small>Phụ đề: {change.caption || 'xóa nội dung cảnh'}</small>
                  )}
                  {change.duration != null && <small>Thời lượng: {change.duration}s</small>}
                </span>
              </label>
            );
          })}
          {(dirty || proposal.revision !== p.revision) && (
            <p className="inline-error">
              Dự án đã có chỉnh sửa sau đề xuất. Hãy gửi lại yêu cầu theo revision hiện tại.
            </p>
          )}
          <Button
            icon={RefreshCw}
            disabled={
              locked || dirty || proposal.revision !== p.revision || !canSelect || reviewBusy
            }
            onClick={async () => {
              setReviewBusy(true);
              setReview(null);
              setReviewError('');
              try {
                const result = await onPreview(selectionPayload);
                setReview({ ...result, signature });
              } catch (error) {
                const detail = error?.detail || error?.response?.data?.detail;
                setReviewError(
                  typeof detail === 'string'
                    ? detail
                    : error?.message || 'Không thể kiểm tra đề xuất.',
                );
              } finally {
                setReviewBusy(false);
              }
            }}
          >
            {reviewBusy ? 'Đang kiểm tra…' : 'Xem trước & preflight'}
          </Button>
          {reviewError && (
            <p className="inline-error" role="alert">
              {reviewError}
            </p>
          )}
          {review?.signature === signature && (
            <div className="proposal-review" role="status">
              <strong>
                {review.can_apply ? 'Bản xem trước sẵn sàng' : 'Preflight phát hiện lỗi chặn'}
              </strong>
              {p.voice_id && !review.project?.voice_id && (
                <p className="proposal-issue warning">
                  Kịch bản mới sẽ gỡ giọng đọc hiện tại. Chọn lại hoặc tạo lại voice sau khi áp
                  dụng.
                </p>
              )}
              {review.project?.cues_stale && (
                <p className="proposal-issue warning">
                  Mốc phụ đề được giữ lại và cần kiểm tra theo kịch bản mới.
                </p>
              )}
              {review.project && (
                <AIProposalPreview
                  key={review.preview_token || signature}
                  project={review.project}
                  locked={locked}
                />
              )}
              {review.preflight?.issues?.map((issue, index) => (
                <p key={issue.code + index} className={'proposal-issue ' + issue.severity}>
                  <b>{issue.code}</b> · {issue.message} <small>{issue.fix}</small>
                </p>
              ))}
              <Button
                icon={Check}
                primary
                disabled={locked || dirty || !review.can_apply || !review.preview_token}
                onClick={() =>
                  onApply({ ...selectionPayload, preview_token: review.preview_token })
                }
              >
                Áp dụng mục đã chọn
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
