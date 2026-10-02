import { Archive, Check, HardDrive, Image, RotateCcw, Trash2, Upload, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { Button, Empty } from './common.jsx';

const formatBytes = (value = 0) => {
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024,
    index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[index]}`;
};

export function StoragePanel({ onOpenProject, onTrashed, beforeTrash }) {
  const [report, setReport] = useState(null),
    [preview, setPreview] = useState(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const refresh = () =>
    Promise.all([api('/storage'), api('/trash')]).then(([next, trash]) =>
      setReport({ ...next, trash }),
    );
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);
  const act = async (callback) => {
    setBusy(true);
    setError('');
    try {
      await callback();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const uploadBackup = (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setError('Chọn file backup ZIP của ClipForge.');
      return;
    }
    const form = new FormData();
    form.append('file', file);
    act(async () => {
      const project = await api('/projects/import', form, 'POST');
      await onOpenProject(project.id);
      await refresh();
    });
  };
  if (!report)
    return (
      <div className="management-page">
        <p>Đang đọc dung lượng…</p>
      </div>
    );
  const categories = [
    ['media', 'Nguồn và tư liệu'],
    ['cache', 'Cache có thể tạo lại'],
    ['exports', 'Bản xuất video'],
    ['history', 'Lịch sử và bản sao schema'],
    ['templates', 'Thư viện template'],
    ['trash', 'Thùng rác'],
    ['other', 'Dữ liệu ứng dụng'],
  ];
  return (
    <section className="management-page">
      <div className="management-heading">
        <div>
          <span className="eyebrow">DỮ LIỆU CỤC BỘ</span>
          <h1>Dung lượng và khôi phục</h1>
          <p>{report.root}</p>
        </div>
        <div className="row management-actions">
          <label className="button small upload-backup">
            <Upload size={14} /> Nhập backup ZIP
            <input
              type="file"
              accept=".zip,application/zip"
              onChange={uploadBackup}
              disabled={busy}
            />
          </label>
        </div>
      </div>
      <div className="storage-summary">
        <div className="storage-total">
          <HardDrive size={20} />
          <span>
            Tổng đang dùng<strong>{formatBytes(report.total)}</strong>
          </span>
        </div>
        <div className="storage-categories">
          {categories.map(([key, label]) => (
            <div key={key}>
              <small>{label}</small>
              <strong>{formatBytes(report.categories[key] || 0)}</strong>
            </div>
          ))}
        </div>
      </div>
      <section className="management-section">
        <div className="row spread">
          <div>
            <h2>Dọn cache</h2>
            <p>
              Chỉ xóa waveform và audio cache có thể tạo lại. Nguồn media và dữ liệu job đang chạy
              được giữ nguyên.
            </p>
          </div>
          <Button
            small
            disabled={busy}
            onClick={() => act(async () => setPreview(await api('/storage/cleanup-preview')))}
          >
            Xem trước dung lượng
          </Button>
        </div>
        {preview && (
          <div className="confirm-strip">
            <span>
              Sẽ xóa {preview.files} file cache, giải phóng khoảng{' '}
              <b>{formatBytes(preview.bytes_to_free)}</b>.
            </span>
            <Button
              small
              primary
              disabled={busy || !preview.bytes_to_free}
              onClick={() =>
                act(async () => {
                  const result = await api('/storage/cleanup-cache', {});
                  setPreview(null);
                  await refresh();
                  setError('');
                  window.dispatchEvent(
                    new CustomEvent('clipforge-toast', {
                      detail: `Đã dọn ${formatBytes(result.deleted_bytes)} cache`,
                    }),
                  );
                })
              }
            >
              Dọn cache
            </Button>
            <Button small disabled={busy} onClick={() => setPreview(null)}>
              Hủy
            </Button>
          </div>
        )}
      </section>
      <section className="management-section">
        <h2>Dự án</h2>
        <div className="storage-project-list">
          {report.projects.length === 0 && <Empty icon={Archive} title="Chưa có dự án" />}
          {report.projects.map((project) => (
            <div className="storage-project" key={project.id}>
              <button className="storage-project-open" onClick={() => onOpenProject(project.id)}>
                <strong>{project.name}</strong>
                <small>{project.id}</small>
              </button>
              <div className="storage-project-sizes">
                <span>{formatBytes(project.media)} tư liệu</span>
                <span>{formatBytes(project.exports)} xuất</span>
                <span>{formatBytes(project.history)} lịch sử</span>
              </div>
              <strong>{formatBytes(project.total)}</strong>
              <Button
                small
                icon={Trash2}
                disabled={busy}
                onClick={() => {
                  if (
                    !window.confirm(`Chuyển “${project.name}” vào thùng rác? Có thể phục hồi sau.`)
                  )
                    return;
                  act(async () => {
                    if (!(await beforeTrash(project.id))) return;
                    const result = await api(`/projects/${project.id}/trash`, {});
                    await onTrashed(project.id);
                    await refresh();
                    window.dispatchEvent(
                      new CustomEvent('clipforge-toast', {
                        detail: `Đã chuyển ${result.name} vào thùng rác`,
                      }),
                    );
                  });
                }}
              >
                Thùng rác
              </Button>
            </div>
          ))}
        </div>
      </section>
      <section className="management-section">
        <h2>
          Thùng rác <small>{report.trash.length} dự án</small>
        </h2>
        {report.trash.length === 0 ? (
          <p className="muted">Các dự án đã xóa sẽ xuất hiện ở đây để phục hồi.</p>
        ) : (
          <div className="storage-project-list">
            {report.trash.map((item) => (
              <div className="storage-project trashed" key={item.id}>
                <div className="storage-project-open">
                  <strong>{item.name}</strong>
                  <small>Đã chuyển {new Date(item.trashed_at).toLocaleString()}</small>
                </div>
                <strong>{formatBytes(item.bytes)}</strong>
                <Button
                  small
                  icon={RotateCcw}
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await api(`/trash/${item.id}/restore`, {});
                      await refresh();
                    })
                  }
                >
                  Phục hồi
                </Button>
                <Button
                  small
                  icon={Trash2}
                  disabled={busy}
                  onClick={() => {
                    if (!window.confirm(`Xóa vĩnh viễn “${item.name}” cùng toàn bộ media?`)) return;
                    act(async () => {
                      await api(`/trash/${item.id}`, undefined, 'DELETE');
                      await refresh();
                    });
                  }}
                >
                  Xóa hẳn
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>
      {error && (
        <p className="management-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

export function TemplateLibrary({ templates, p, onApply, onRename, onDuplicate, onDelete, busy }) {
  const [aspect, setAspect] = useState(p?.aspect || '9:16'),
    [selected, setSelected] = useState(null),
    [replace, setReplace] = useState({}),
    [renameId, setRenameId] = useState(''),
    [renameValue, setRenameValue] = useState('');
  useEffect(() => setAspect(p?.aspect || '9:16'), [p?.aspect]);
  const missing = selected?.missing_assets || [];
  const choice = (template) => setSelected(template);
  const rename = async (id) => {
    if (await onRename(id, renameValue)) setRenameId('');
  };
  return (
    <section className="management-page template-library-page">
      <div className="management-heading">
        <div>
          <span className="eyebrow">BỐ CỤC VÀ NHỊP DỰNG</span>
          <h1>Thư viện template</h1>
          <p>
            Xem trước theo tỷ lệ dự án, rồi áp dụng và tinh chỉnh lớp/khung trong trình biên tập.
          </p>
        </div>
        <label className="field aspect-picker">
          Tỷ lệ xem trước
          <select value={aspect} onChange={(e) => setAspect(e.target.value)}>
            <option>9:16</option>
            <option>16:9</option>
            <option>1:1</option>
          </select>
        </label>
      </div>
      {!p && <div className="notice-card">Chọn hoặc tạo một dự án để áp dụng template.</div>}
      <div className="library-grid">
        {templates.map((item) => {
          const template = item.template || item;
          const isRenaming = renameId === item.id;
          return (
            <article className="library-card" key={item.id}>
              <button
                className="library-preview"
                onClick={() => choice(item)}
                title={`Xem trước ${item.name}`}
              >
                <img
                  src={`/api/templates/${encodeURIComponent(item.id)}/thumbnail.svg?aspect=${aspect}`}
                  alt={`Xem trước ${item.name}`}
                />
              </button>
              <div className="library-card-body">
                {isRenaming ? (
                  <div className="rename-template">
                    <input
                      aria-label="Tên template"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') rename(item.id);
                        if (e.key === 'Escape') setRenameId('');
                      }}
                    />
                    <Button
                      small
                      icon={Check}
                      disabled={!renameValue.trim() || busy}
                      onClick={() => rename(item.id)}
                    />
                    <Button small icon={X} onClick={() => setRenameId('')} />
                  </div>
                ) : (
                  <strong>{item.name || template.name}</strong>
                )}
                <small>
                  {item.built_in
                    ? 'Mẫu có sẵn'
                    : item.legacy
                      ? 'Mẫu đã lưu · bản cũ'
                      : 'Mẫu đã lưu'}
                  {item.missing_assets?.length ? ` · thiếu ${item.missing_assets.length} ảnh` : ''}
                </small>
                {item.missing_assets?.length > 0 &&
                  item.missing_assets.map((asset) => (
                    <label className="field" key={asset.id}>
                      <span>Thay {asset.name}</span>
                      <select
                        value={replace[`${item.id}:${asset.id}`] || ''}
                        onChange={(e) =>
                          setReplace((old) => ({
                            ...old,
                            [`${item.id}:${asset.id}`]: e.target.value,
                          }))
                        }
                      >
                        <option value="">Chọn ảnh trong dự án</option>
                        {p?.assets
                          .filter((a) => a.media === 'image' && !a.deleted)
                          .map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  ))}
                <div className="library-actions">
                  <Button small onClick={() => choice(item)}>
                    Xem trước
                  </Button>
                  <Button
                    small
                    primary
                    disabled={
                      !p ||
                      busy ||
                      item.missing_assets?.some((asset) => !replace[`${item.id}:${asset.id}`])
                    }
                    onClick={() => {
                      if (aspect !== p?.aspect) {
                        choice(item);
                        return;
                      }
                      onApply(
                        item,
                        Object.fromEntries(
                          (item.missing_assets || []).map((asset) => [
                            asset.id,
                            replace[`${item.id}:${asset.id}`],
                          ]),
                        ),
                        aspect,
                      );
                    }}
                  >
                    Áp dụng
                  </Button>
                </div>
                {item.saved && !item.built_in && (
                  <div className="library-manage">
                    <Button
                      small
                      onClick={() => {
                        setRenameId(item.id);
                        setRenameValue(item.name || template.name);
                      }}
                    >
                      Đổi tên
                    </Button>
                    <Button
                      small
                      icon={Archive}
                      disabled={busy}
                      onClick={() => onDuplicate(item.id)}
                    >
                      Nhân bản
                    </Button>
                    <Button
                      small
                      icon={Trash2}
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm(`Xóa template “${item.name}”?`)) onDelete(item.id);
                      }}
                    >
                      Xóa
                    </Button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>
      {templates.length === 0 && <Empty icon={Image} title="Chưa có template" />}
      {selected && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
          <section
            className="template-preview-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Xem trước ${selected.name}`}
          >
            <header className="modal-header">
              <div>
                <span className="eyebrow">XEM TRƯỚC · {aspect}</span>
                <h2>{selected.name}</h2>
              </div>
              <button className="icon-button" onClick={() => setSelected(null)} aria-label="Đóng">
                <X />
              </button>
            </header>
            <div className="template-preview-content">
              <img
                src={`/api/templates/${encodeURIComponent(selected.id)}/thumbnail.svg?aspect=${aspect}`}
                alt={`Bố cục ${selected.name}`}
              />
              <div>
                <strong>{selected.template?.name || selected.name}</strong>
                <p>
                  Hình và logo giữ nguyên tỷ lệ trong khung xem trước. Sau khi áp dụng, mở tab Mẫu
                  dựng để chỉnh vị trí khung, chữ, logo và lớp theo tỷ lệ này.
                </p>
                <p className="muted">Tỷ lệ dự án: {p?.aspect || 'chưa chọn dự án'}</p>
              </div>
            </div>
            <footer className="modal-footer">
              <Button onClick={() => setSelected(null)}>Đóng</Button>
              <Button
                primary
                disabled={
                  !p || busy || missing.some((asset) => !replace[`${selected.id}:${asset.id}`])
                }
                onClick={() => {
                  onApply(
                    selected,
                    Object.fromEntries(
                      missing.map((asset) => [asset.id, replace[`${selected.id}:${asset.id}`]]),
                    ),
                    aspect,
                  );
                  setSelected(null);
                }}
              >
                Áp dụng template
              </Button>
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}
