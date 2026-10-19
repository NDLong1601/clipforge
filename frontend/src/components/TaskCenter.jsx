import {
  AlertTriangle,
  ArrowLeft,
  ArrowUp,
  CircleAlert,
  Cpu,
  Download,
  HardDrive,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';
import { Button } from './common.jsx';

const DISCARDABLE = new Set(['error', 'interrupted', 'blocked']);
const discardSummary = (job) =>
  job.kind === 'upload'
    ? 'Bỏ file nhập tạm, checkpoint và kết quả dịch vụ chưa áp dụng.'
    : job.kind === 'render'
      ? 'Bỏ bản xuất chưa lưu, checkpoint và kết quả dịch vụ chưa áp dụng.'
      : 'Bỏ checkpoint và kết quả dịch vụ chưa áp dụng.';
const ACTIVE = new Set(['queued', 'running', 'cancelling']);
const STATUS = {
  queued: 'Đang chờ',
  running: 'Đang chạy',
  cancelling: 'Đang hủy',
  blocked: 'Bị chặn',
  interrupted: 'Bị gián đoạn',
  paused: 'Đã tạm dừng',
  done: 'Hoàn tất',
  error: 'Lỗi',
  cancelled: 'Đã hủy',
};
const KIND = {
  render: 'Xuất video',
  portrait: 'Xuất bản dọc vẽ tay',
  'whiteboard-render': 'Xuất video vẽ tay',
  'whiteboard-plan': 'Lập storyboard vẽ tay',
  'whiteboard-images': 'Vẽ tranh AI',
  analyze: 'Phân tích tư liệu',
  plan: 'Lập timeline',
  template: 'Dựng theo mẫu',
  'filter-faces': 'Lọc khuôn mặt',
  'assemble-audio': 'Dựng từ audio',
  voice: 'Tạo giọng đọc',
  transcribe: 'Chuyển lời nói thành văn bản',
  upload: 'Nhập tư liệu',
  assistant: 'Trợ lý AI',
};

function bytes(value) {
  if (value == null) return 'Không có số liệu';
  if (value < 1024 ** 3) return `${Math.round(value / 1024 ** 2)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

export function TaskCenter({ onClose, onOpenSettings, onOpenStorage }) {
  const [queue, setQueue] = useState(null);
  const [projects, setProjects] = useState([]);
  const [selectedProjects, setSelectedProjects] = useState([]);
  const [previewBatch, setPreviewBatch] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [error, setError] = useState('');
  const [connectionState, setConnectionState] = useState('connecting');
  const [lastSuccessAt, setLastSuccessAt] = useState(null);
  const [failureCount, setFailureCount] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [diagnosticsManifest, setDiagnosticsManifest] = useState(null);
  const [costConfirmed, setCostConfirmed] = useState(false);
  const [storage, setStorage] = useState(null);
  const [mutating, setMutating] = useState(false);
  const detailsRequestRef = useRef(0);
  const queueRequestRef = useRef(0);
  const selectedIdRef = useRef(null);
  const alive = useRef(true);
  const scopeRef = useRef(0);
  const failuresRef = useRef(0);
  const controllers = useRef(new Set());
  const invalidate = () => {
    ++scopeRef.current;
    ++queueRequestRef.current;
    ++detailsRequestRef.current;
    for (const controller of controllers.current) controller.abort();
  };
  const close = () => {
    alive.current = false;
    invalidate();
    onClose();
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      invalidate();
    };
  }, []);

  const refresh = useCallback(async () => {
    const request = ++queueRequestRef.current;
    const scope = scopeRef.current;
    const id = selectedIdRef.current;
    const detailRequest = ++detailsRequestRef.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    const valid = () =>
      alive.current && scope === scopeRef.current && request === queueRequestRef.current;
    const options = { signal: controller.signal, timeoutMs: 12000 };
    try {
      const [state, list, current] = await Promise.all([
        api('/jobs/queue', undefined, 'GET', options),
        api('/projects', undefined, 'GET', options),
        id ? api(`/jobs/${id}`, undefined, 'GET', options) : null,
      ]);
      if (!valid()) return null;
      setQueue(state);
      setProjects(list.filter((project) => !project.corrupt));
      if (current && id === selectedIdRef.current && detailRequest === detailsRequestRef.current)
        setDetail(current);
      failuresRef.current = 0;
      setConnectionState('connected');
      setLastSuccessAt(new Date().toISOString());
      setFailureCount(0);
      setError('');
      return state;
    } catch (err) {
      controller.abort();
      if (valid() && err?.name !== 'AbortError') {
        failuresRef.current += 1;
        setFailureCount(failuresRef.current);
        setConnectionState(failuresRef.current >= 3 ? 'offline' : 'reconnecting');
        setError(err.message || 'Không lấy được trạng thái hàng đợi.');
      }
      throw err;
    } finally {
      controllers.current.delete(controller);
    }
  }, []);

  useEffect(() => {
    let stopped = false,
      pending = false,
      timer;
    const load = async () => {
      if (stopped || pending) return;
      pending = true;
      try {
        await refresh();
      } catch {
        /* refresh retains the last successful data */
      } finally {
        pending = false;
        if (!stopped)
          timer = setTimeout(
            load,
            failuresRef.current
              ? Math.min(15000, 1000 * 2 ** Math.min(failuresRef.current - 1, 4))
              : document.visibilityState === 'hidden'
                ? 6000
                : 2000,
          );
      }
    };
    load();
    const onVisibility = () => {
      if (document.visibilityState === 'visible' && !pending) {
        clearTimeout(timer);
        timer = setTimeout(load, 0);
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [selectedId, refresh]);

  const mutate = async (action) => {
    const scope = scopeRef.current;
    if (!alive.current) return;
    setError('');
    setMutating(true);
    try {
      await action();
      if (alive.current && scope === scopeRef.current) await refresh();
    } catch (err) {
      if (alive.current && scope === scopeRef.current)
        setError(err.message || 'Không cập nhật được hàng đợi.');
    } finally {
      if (alive.current) setMutating(false);
    }
  };
  const discard = (job) =>
    mutate(async () => {
      const scope = scopeRef.current;
      await api(`/jobs/${job.id}/cancel`, {});
      const report = await api('/storage');
      if (alive.current && scope === scopeRef.current) setStorage(report);
    });

  const jobs = [...(queue?.jobs || [])].sort((a, b) => {
    if (a.status === 'queued' && b.status === 'queued')
      return (a.queue_position || 99999) - (b.queue_position || 99999);
    if (ACTIVE.has(a.status) !== ACTIVE.has(b.status)) return ACTIVE.has(a.status) ? -1 : 1;
    return (b.created_at || '').localeCompare(a.created_at || '');
  });
  const projectNames = Object.fromEntries(projects.map((project) => [project.id, project.name]));
  const toggleProject = (id) =>
    setSelectedProjects((old) =>
      old.includes(id) ? old.filter((item) => item !== id) : [...old, id].slice(0, 50),
    );
  const openDetails = async (id) => {
    invalidate();
    selectedIdRef.current = id;
    const requestId = ++detailsRequestRef.current;
    const scope = scopeRef.current;
    setSelectedId(id);
    setDetail(null);
    setDiagnosticsManifest(null);
    setCostConfirmed(false);
    try {
      const result = await api(`/jobs/${id}`);
      if (
        alive.current &&
        scope === scopeRef.current &&
        detailsRequestRef.current === requestId &&
        selectedIdRef.current === id
      )
        setDetail(result);
    } catch (err) {
      if (alive.current && scope === scopeRef.current && detailsRequestRef.current === requestId)
        setError(err.message || 'Không lấy được chi tiết tác vụ.');
    }
  };
  const actOnDetail = (action) => mutate(action);
  const previewDiagnostics = () => {
    const id = selectedIdRef.current,
      scope = scopeRef.current;
    return mutate(async () => {
      const manifest = await api(`/jobs/${id}/diagnostics/preview`);
      if (alive.current && scope === scopeRef.current && selectedIdRef.current === id)
        setDiagnosticsManifest(manifest);
    });
  };
  const connectionLabel =
    connectionState === 'connected'
      ? 'Đã đồng bộ'
      : connectionState === 'offline'
        ? 'Mất kết nối · đang giữ trạng thái gần nhất'
        : 'Đang kết nối lại';

  return (
    <section className="task-center management-page">
      <div className="management-heading">
        <div>
          <span className="eyebrow muted">CLIPFORGE · MỌI DỰ ÁN</span>
          <h1>Tác vụ</h1>
          <p>Theo dõi hàng đợi, slot tài nguyên và kết quả gần đây.</p>
        </div>
        <div className="task-center-actions">
          <Button
            small
            onClick={() => mutate(() => api('/jobs/queue', { paused: !queue?.paused }, 'PATCH'))}
          >
            {queue?.paused ? <Play size={14} /> : <Pause size={14} />}
            {queue?.paused ? 'Tiếp tục hàng đợi' : 'Tạm dừng hàng đợi'}
          </Button>
          <Button
            small
            onClick={() =>
              mutate(() => api('/jobs/queue', { low_resource: !queue?.low_resource }, 'PATCH'))
            }
          >
            <Cpu size={14} />
            {queue?.low_resource ? 'Tắt chế độ ít tài nguyên' : 'Máy ít tài nguyên'}
          </Button>
          <Button small icon={ArrowLeft} onClick={close}>
            Quay lại
          </Button>
        </div>
      </div>

      {error && (
        <div className="task-center-error" role="alert">
          <CircleAlert size={15} />
          {error}
        </div>
      )}
      <div className={`task-connection ${connectionState}`} role="status">
        <span className="task-connection-dot" />
        <span>
          {connectionLabel}
          {lastSuccessAt ? ` · đồng bộ lúc ${new Date(lastSuccessAt).toLocaleTimeString()}` : ''}
        </span>
        {failureCount > 0 && (
          <Button small icon={RefreshCw} onClick={() => mutate(refresh)}>
            Đồng bộ lại
          </Button>
        )}
      </div>

      {storage && (
        <small role="status">Đã cập nhật dung lượng · tổng đang dùng {bytes(storage.total)}.</small>
      )}
      <div className="task-resource-summary">
        <div>
          <span>Hàng đợi</span>
          <strong>
            {queue?.queued ?? '—'} chờ · {queue?.running ?? '—'} chạy
          </strong>
        </div>
        <div>
          <span>CPU hệ thống</span>
          <strong>
            {queue?.system_cpu_percent == null
              ? 'Không có số liệu'
              : `${Math.round(queue.system_cpu_percent)}%`}
          </strong>
        </div>
        <div>
          <span>RAM khả dụng</span>
          <strong>{bytes(queue?.available_memory_bytes)}</strong>
        </div>
        <div>
          <span>Dung lượng trống</span>
          <strong>{bytes(queue?.available_disk_bytes)}</strong>
        </div>
        <div>
          <span>Giới hạn bước nặng</span>
          <strong>
            {queue?.resource_use?.heavy_slots || 0}/{queue?.resource_limits?.heavy_slots ?? 2}
          </strong>
        </div>
      </div>
      {queue?.gpu_telemetry == null && (
        <small className="task-telemetry-note">
          GPU: không có số liệu đáng tin cậy trên cấu hình này. Slot encode vẫn được giới hạn.
        </small>
      )}

      <section className="task-batch">
        <div className="task-list-heading">
          <div>
            <h2>Xuất nhiều dự án</h2>
            <small>
              Chọn snapshot hiện tại của từng dự án. Một dự án lỗi không ngăn các dự án khác vào
              queue.
            </small>
          </div>
          <div className="task-batch-controls">
            <label>
              <input
                type="checkbox"
                checked={previewBatch}
                onChange={(event) => setPreviewBatch(event.target.checked)}
              />{' '}
              Bản xem thử
            </label>
            <Button
              small
              primary
              disabled={!selectedProjects.length}
              onClick={() =>
                mutate(async () => {
                  const result = await api('/jobs/render-batch', {
                    projects: projects
                      .filter((project) => selectedProjects.includes(project.id))
                      .map((project) => ({ id: project.id, revision: project.revision })),
                    preview: previewBatch,
                  });
                  setBatchResult(result);
                  if (result.accepted?.length) setSelectedProjects([]);
                })
              }
            >
              Xếp {selectedProjects.length} dự án
            </Button>
          </div>
        </div>
        <div className="task-project-picker">
          {projects.map((project) => (
            <label className="task-project-option" key={project.id}>
              <input
                type="checkbox"
                checked={selectedProjects.includes(project.id)}
                onChange={() => toggleProject(project.id)}
              />
              <span>
                <strong>{project.name}</strong>
                <small>
                  Revision {project.revision ?? '—'} · {project.clips} cảnh · {project.mode}
                </small>
              </span>
            </label>
          ))}
          {!projects.length && <small className="muted">Chưa có dự án hợp lệ để xuất.</small>}
        </div>
        {batchResult && (
          <div className="task-batch-result">
            Đã xếp {batchResult.accepted?.length || 0} job
            {(batchResult.rejected || []).map((item) => (
              <small key={item.pid}>
                {projectNames[item.pid] || item.pid}: {item.message}
              </small>
            ))}
          </div>
        )}
      </section>

      <div className="task-list-heading">
        <h2>Danh sách tác vụ</h2>
        <small>
          {queue?.paused ? 'Hàng đợi đang tạm dừng' : 'FIFO có tăng ưu tiên theo thời gian'}
        </small>
      </div>
      {!queue ? (
        <div className="task-empty">
          <Loader2 className="spin" size={18} /> Đang đồng bộ hàng đợi…
        </div>
      ) : jobs.length === 0 ? (
        <div className="task-empty">Chưa có tác vụ nào.</div>
      ) : (
        <div className="task-list">
          {jobs.map((job) => (
            <article className={`task-row ${job.status}`} key={job.id}>
              <div className="task-row-main">
                <div className="task-row-heading">
                  <strong>{KIND[job.kind] || job.kind}</strong>
                  <span className={`task-status ${job.status}`}>
                    {STATUS[job.status] || job.status}
                  </span>
                  {job.status === 'queued' && job.dispatch_ready && (
                    <small>#{job.queue_position || '—'}</small>
                  )}
                </div>
                <small className="task-project">
                  {projectNames[job.pid] || `Dự án ${job.pid.slice(0, 6)}`} · {job.message}
                </small>
                {ACTIVE.has(job.status) && (
                  <div className="progress">
                    <i style={{ width: `${Math.min(100, job.progress || 0)}%` }} />
                  </div>
                )}
                {job.status === 'queued' && job.resource_wait_reason && (
                  <small className="task-wait">{job.resource_wait_reason}</small>
                )}
                {job.status === 'error' && job.error?.message && (
                  <small className="task-error-message">{job.error.message}</small>
                )}
                {job.status === 'blocked' && job.blocked_reason?.message && (
                  <small className="task-error-message">{job.blocked_reason.message}</small>
                )}
                {DISCARDABLE.has(job.status) && (
                  <small className="task-discard-summary">
                    {discardSummary(job)} Nguồn gốc và kết quả đã lưu được giữ.
                  </small>
                )}
                <small className="task-resource-line">
                  <HardDrive size={12} /> {job.resource_class?.join(' · ') || 'Tác vụ nhẹ'}
                  {job.status === 'queued' &&
                    ` · Ước tính RAM ${bytes(job.estimated_ram_bytes)}, ổ đĩa ${bytes(job.estimated_disk_bytes)}`}
                </small>
                {job.status === 'running' && job.current_step && (
                  <small className="task-wait">Bước đang chạy: {job.current_step}</small>
                )}
                {job.status === 'running' && job.encode_progress?.eta_estimated && (
                  <small className="task-wait">
                    Thời gian còn lại của bước (ước tính):{' '}
                    {Math.ceil(job.encode_progress.eta_seconds)} giây
                  </small>
                )}
              </div>
              <div className="task-row-actions">
                <Button small onClick={() => openDetails(job.id)}>
                  Chi tiết
                </Button>
                {job.status === 'queued' && job.dispatch_ready && (
                  <Button
                    small
                    aria-label="Ưu tiên tác vụ"
                    title="Đưa lên đầu hàng đợi"
                    icon={ArrowUp}
                    onClick={() => mutate(() => api(`/jobs/${job.id}/promote`, {}))}
                  />
                )}
                {DISCARDABLE.has(job.status) && (
                  <Button small disabled={mutating} icon={X} onClick={() => discard(job)}>
                    Bỏ tác vụ và dữ liệu phục hồi
                  </Button>
                )}
                {ACTIVE.has(job.status) && (
                  <Button
                    small
                    disabled={job.status === 'cancelling'}
                    icon={X}
                    onClick={() => mutate(() => api(`/jobs/${job.id}/cancel`, {}))}
                  >
                    {job.status === 'cancelling' ? 'Đang hủy' : 'Hủy'}
                  </Button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}

      {selectedId && (
        <section className="task-detail">
          <div className="task-detail-heading">
            <div>
              <span className="eyebrow muted">CHI TIẾT TÁC VỤ</span>
              <h2>
                {detail
                  ? `${KIND[detail.kind] || detail.kind} · ${STATUS[detail.status] || detail.status}`
                  : 'Đang tải…'}
              </h2>
            </div>
            <Button
              small
              icon={X}
              onClick={() => {
                invalidate();
                selectedIdRef.current = null;
                setSelectedId(null);
                setDetail(null);
                setDiagnosticsManifest(null);
              }}
            >
              Đóng
            </Button>
          </div>
          {!detail ? (
            <div className="task-empty">
              <Loader2 className="spin" size={18} /> Đang tải thông tin…
            </div>
          ) : (
            <>
              <div className="task-detail-facts">
                <div>
                  <span>Bước hiện tại</span>
                  <strong>{detail.current_step || '—'}</strong>
                </div>
                <div>
                  <span>Lần chạy</span>
                  <strong>{detail.attempt || 1}</strong>
                </div>
                <div>
                  <span>Trạng thái dữ liệu</span>
                  <strong>{failureCount ? 'Có thể đã cũ' : 'Mới nhất'}</strong>
                </div>
                <div>
                  <span>Cập nhật</span>
                  <strong>
                    {detail.updated_at ? new Date(detail.updated_at).toLocaleString() : '—'}
                  </strong>
                </div>
                <div>
                  <span>Tiến độ</span>
                  <strong>{Math.round(detail.progress || 0)}%</strong>
                </div>
              </div>
              {(detail.error || detail.blocked_reason) && (
                <div className="task-detail-error">
                  <AlertTriangle size={16} />
                  <div>
                    <strong>
                      {detail.error?.code || detail.blocked_reason?.code || 'JOB_BLOCKED'}
                    </strong>
                    <p>{detail.error?.message || detail.blocked_reason?.message}</p>
                    {detail.error?.step_id && <small>Bước lỗi: {detail.error.step_id}</small>}
                    {detail.error?.diagnostic_id && (
                      <small>Mã chẩn đoán: {detail.error.diagnostic_id}</small>
                    )}
                    {detail.error?.safe_details?.http_status && (
                      <small>HTTP {detail.error.safe_details.http_status}</small>
                    )}
                    <div className="task-error-recovery">
                      {detail.error?.category === 'storage' && (
                        <Button small icon={HardDrive} onClick={onOpenStorage}>
                          Mở Dung lượng
                        </Button>
                      )}
                      {['PROVIDER_AUTH_REQUIRED', 'PROVIDER_QUOTA_EXCEEDED'].includes(
                        detail.error?.code,
                      ) && (
                        <Button small icon={Cpu} onClick={onOpenSettings}>
                          Mở Cài đặt API
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              )}
              {detail.resume_plan && (
                <div className="task-resume-plan">
                  <strong>Kiểm tra tiếp tục từ checkpoint</strong>
                  <small>
                    {detail.resume_plan.reusable_candidates?.length || 0} mục có thể kiểm tra lại ·{' '}
                    {detail.resume_plan.missing_or_changed?.length || 0} mục thiếu hoặc đã đổi
                  </small>
                  {!!detail.resume_plan.missing_or_changed?.length && (
                    <small>
                      Cần dựng lại: {detail.resume_plan.missing_or_changed.slice(0, 8).join(', ')}
                    </small>
                  )}
                  <small>
                    Trước khi dùng lại, ClipForge sẽ xác minh checkpoint. Lời gọi AI có thể phát
                    sinh: {detail.resume_plan.ai_calls_possible ? 'có' : 'không'}
                  </small>
                </div>
              )}
              <div className="task-detail-actions">
                {detail.can_restart_current && (
                  <Button
                    small
                    primary
                    icon={Play}
                    onClick={() =>
                      actOnDetail(() =>
                        api(`/jobs/${selectedId}/restart-current`, {
                          state_version: detail.state_version,
                        }),
                      )
                    }
                  >
                    Tạo tác vụ từ bản hiện tại
                  </Button>
                )}
                {detail.can_resume && (
                  <Button
                    small
                    primary
                    icon={Play}
                    onClick={() =>
                      actOnDetail(() =>
                        api(`/jobs/${selectedId}/resume`, { state_version: detail.state_version }),
                      )
                    }
                  >
                    {detail.bookkeeping_pending
                      ? 'Hoàn tất lưu kết quả'
                      : detail.resume_capability === 'snapshot'
                        ? 'Bắt đầu tác vụ đã lưu'
                        : 'Tiếp tục từ checkpoint'}
                  </Button>
                )}
                {detail.can_retry_unknown && (
                  <div className="task-cost-confirm">
                    <label>
                      <input
                        type="checkbox"
                        checked={costConfirmed}
                        onChange={(event) => setCostConfirmed(event.target.checked)}
                      />{' '}
                      Tôi hiểu gửi lại có thể bị tính phí lần nữa.
                    </label>
                    <Button
                      small
                      disabled={!costConfirmed}
                      icon={RefreshCw}
                      onClick={() =>
                        actOnDetail(() =>
                          api(`/jobs/${selectedId}/retry-unknown`, {
                            confirm_cost: true,
                            state_version: detail.state_version,
                          }),
                        )
                      }
                    >
                      Xác nhận gửi lại
                    </Button>
                  </div>
                )}
                {DISCARDABLE.has(detail.status) && (
                  <div className="task-discard-summary">
                    <small>{discardSummary(detail)} Nguồn gốc và kết quả đã lưu được giữ.</small>
                    <Button small disabled={mutating} icon={X} onClick={() => discard(detail)}>
                      Bỏ tác vụ và dữ liệu phục hồi
                    </Button>
                  </div>
                )}
                {ACTIVE.has(detail.status) && (
                  <Button
                    small
                    disabled={detail.status === 'cancelling'}
                    icon={X}
                    onClick={() => actOnDetail(() => api(`/jobs/${selectedId}/cancel`, {}))}
                  >
                    Hủy tác vụ
                  </Button>
                )}
                {!detail.can_resume && ['error', 'interrupted'].includes(detail.status) && (
                  <small>
                    Tác vụ không có checkpoint hợp lệ để tiếp tục. Hãy sửa tư liệu/cấu hình được nêu
                    rồi tạo tác vụ mới.
                  </small>
                )}
              </div>
              {!!detail.error_history?.length && (
                <div className="task-detail-section">
                  <h3>Lịch sử lỗi theo lần chạy</h3>
                  {detail.error_history
                    .slice()
                    .reverse()
                    .map((entry, index) => (
                      <div className="task-history-entry" key={`${entry.attempt}-${index}`}>
                        <strong>
                          Lần {entry.attempt || '—'} · {entry.error?.code || 'Lỗi'}
                        </strong>
                        <small>
                          {entry.error?.message || 'Không có mô tả.'}
                          {entry.finished_at
                            ? ` · ${new Date(entry.finished_at).toLocaleString()}`
                            : ''}
                        </small>
                      </div>
                    ))}
                </div>
              )}
              {!!detail.steps?.length && (
                <div className="task-detail-section">
                  <h3>Các bước đã ghi nhận</h3>
                  <div className="task-step-list">
                    {detail.steps.slice(0, 30).map((step, index) => (
                      <span key={`${step.id}-${index}`}>
                        {step.id} · {step.status || 'đã lưu'}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {!!detail.provider_calls?.length && (
                <div className="task-detail-section">
                  <h3>Telemetry dịch vụ</h3>
                  {detail.provider_calls
                    .slice(-10)
                    .reverse()
                    .map((call, index) => (
                      <div className="task-history-entry" key={`${call.attempt}-${index}`}>
                        <strong>
                          {call.provider || 'provider'} · {call.outcome || 'unknown'}
                        </strong>
                        <small>
                          Lần {call.attempt || '—'}
                          {call.status_code ? ` · HTTP ${call.status_code}` : ''}
                          {call.elapsed_ms ? ` · ${call.elapsed_ms} ms` : ''}
                        </small>
                      </div>
                    ))}
                </div>
              )}
              <div className="task-detail-section task-diagnostics">
                <div>
                  <h3>Gói chẩn đoán</h3>
                  <small>
                    Xem danh sách nội dung trước khi tải. ZIP được tạo trên máy và ClipForge không
                    tự gửi đi.
                  </small>
                </div>
                <Button small icon={Download} onClick={previewDiagnostics}>
                  Xem nội dung gói
                </Button>
                {diagnosticsManifest && (
                  <div className="task-diagnostics-preview">
                    <strong>Gói gồm</strong>
                    {diagnosticsManifest.files?.map((file) => (
                      <small key={file.name}>
                        <b>{file.name}</b> · {file.description}
                      </small>
                    ))}
                    <strong>Không gồm</strong>
                    {diagnosticsManifest.excludes?.map((item) => (
                      <small key={item}>· {item}</small>
                    ))}
                    <a
                      className="button small"
                      href={`/api/jobs/${selectedId}/diagnostics.zip`}
                      download
                    >
                      {' '}
                      <Download size={14} /> Tải ZIP
                    </a>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      )}
    </section>
  );
}
