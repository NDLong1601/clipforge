import {
  AlertCircle,
  Archive,
  ArrowRight,
  CheckCircle2,
  Clapperboard,
  Download,
  FileText,
  Film,
  FolderOpen,
  HardDrive,
  KeyRound,
  LayoutTemplate,
  Library,
  Loader2,
  Mic2,
  PanelLeftClose,
  Play,
  Plus,
  Redo2,
  RefreshCw,
  Save,
  Scissors,
  SlidersHorizontal,
  Sparkles,
  Undo2,
  Upload,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { aid, api, clone, exportUrl, fmt, uploadWithProgress } from './api/client.js';
import { Button } from './components/common.jsx';
import {
  AssistantPanel,
  AudioPanel,
  ClipInspector,
  MediaPanel,
  ScriptPanel,
  TemplatePanel,
} from './components/EditorPanels.jsx';
import { StoragePanel, TemplateLibrary } from './components/ManagementPanels.jsx';
import { Preview } from './components/Preview.jsx';
import { SettingsModal } from './components/SettingsModal.jsx';
import { Timeline } from './components/Timeline.jsx';
import { useJobs } from './hooks/useJobs.js';
import { usePreviewState } from './hooks/usePreviewState.js';
import { useProjectSession } from './hooks/useProjectSession.js';
import { clipAtTime, clipStarts, timelineDuration } from './timelineMath.js';

const tabs = [
  ['media', 'Tư liệu', FolderOpen],
  ['script', 'Kịch bản', FileText],
  ['template', 'Mẫu dựng', LayoutTemplate],
  ['audio', 'Âm thanh', Mic2],
  ['ai', 'Trợ lý AI', Sparkles],
];

export default function App() {
  const session = useProjectSession();
  const {
    p,
    setP,
    dirty,
    setDirty,
    edit: editProject,
    save,
    open: openProject,
    saveStatus,
    conflict,
    loadLatest,
    pRef,
    dirtyRef,
  } = session;
  const [projects, setProjects] = useState([]),
    [tab, setTab] = useState('media'),
    [selected, setSelected] = useState(0),
    [busy, setBusy] = useState(false),
    [toast, setToast] = useState(''),
    [error, setError] = useState(''),
    [settings, setSettings] = useState(null),
    [fonts, setFonts] = useState(['Arial']),
    [templates, setTemplates] = useState([]),
    [useAI, setUseAI] = useState(false),
    [proposal, setProposal] = useState(null),
    [preflight, setPreflight] = useState(null),
    [schemaBackups, setSchemaBackups] = useState([]),
    [managementView, setManagementView] = useState(''),
    [backupExports, setBackupExports] = useState(false),
    [backupHistory, setBackupHistory] = useState(false),
    [showSettings, setShowSettings] = useState(false),
    [showProjects, setShowProjects] = useState(true),
    [uploadState, setUploadState] = useState(null),
    [history, setHistory] = useState({ can_undo: false, can_redo: false }),
    [showConflictDiff, setShowConflictDiff] = useState(false);
  const { jobs, setJobs, active, cancel: cancelJob } = useJobs(p?.id);
  const { preview, setPreview, play, setPlay, cursor, setCursor } = usePreviewState();
  const uploadAbort = useRef(null),
    jobSeen = useRef(new Set());
  const loadProjects = () =>
    api('/projects').then((list) => {
      setProjects(list);
      return list;
    });
  useEffect(() => {
    loadProjects().then((list) => {
      const id = localStorage.getItem('clipforge-project');
      if (list.some((x) => x.id === id && !x.corrupt)) safe(() => open(id));
    });
    Promise.all([api('/templates'), api('/template-packages')]).then(([all, packages]) =>
      setTemplates([
        ...all
          .filter((x) => ['clean', 'editorial', 'bold'].includes(x.id))
          .map((template) => ({
            id: template.id,
            name: template.name,
            template,
            built_in: true,
            missing_assets: [],
          })),
        ...packages,
      ]),
    );
    api('/settings').then(setSettings);
    api('/fonts')
      .then((result) => setFonts(result.fonts || ['Arial']))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!p?.id) return;
    let cancelled = false;
    api(`/projects/${p.id}/schema-backups`)
      .then((info) => {
        if (!cancelled) setSchemaBackups(info.backups || []);
      })
      .catch(() => {});
    api(`/projects/${p.id}/history`)
      .then((info) => {
        if (!cancelled) setHistory(info);
      })
      .catch(() => {});
    api('/projects')
      .then((list) => {
        if (!cancelled) setProjects(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [p?.id, p?.revision]);
  useEffect(() => {
    const guard = (e) => {
      if (dirtyRef.current) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, []);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(''), 4000);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  useEffect(() => {
    const onToast = (event) => setToast(event.detail || '');
    window.addEventListener('clipforge-toast', onToast);
    return () => window.removeEventListener('clipforge-toast', onToast);
  }, []);
  const safe = async (fn) => {
    setError('');
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      if (e.name === 'AbortError') setToast('Đã hủy tải lên');
      else setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  const open = async (id) => {
    const project = await openProject(id);
    if (!project) return null;
    const pastJobs = await api('/jobs?pid=' + id);
    pastJobs
      .filter((j) => ['done', 'error', 'cancelled', 'interrupted'].includes(j.status))
      .forEach((j) => jobSeen.current.add(j.id));
    localStorage.setItem('clipforge-project', id);
    setJobs(pastJobs);
    setSelected(0);
    setCursor(0);
    setPreview(null);
    setPreflight(null);
    setProposal(null);
    setPlay(false);
    return project;
  };
  const openManagement = async (view) => {
    if (dirtyRef.current && !(await save())) return;
    setManagementView(view);
  };
  const openFromManagement = async (id) => {
    const project = await safe(() => open(id));
    if (project) setManagementView('');
  };
  const beforeTrashProject = async (id) => {
    if (dirtyRef.current && p?.id === id) return !!(await save());
    return true;
  };
  const projectTrashed = async (id) => {
    if (p?.id === id) {
      setDirty(false);
      setP(null);
      setPreview(null);
      localStorage.removeItem('clipforge-project');
    }
    await loadProjects();
  };
  const reloadTemplates = async () => {
    const packages = await api('/template-packages');
    setTemplates((old) => [...old.filter((item) => item.built_in), ...packages]);
  };
  const applyLibraryTemplate = async (item, assetMap, aspect) => {
    if (item.built_in) {
      await safe(async () => {
        if (dirtyRef.current && !(await save())) return;
        editProject((next) => {
          next.template = clone(item.template);
          next.mode = 'template';
          next.aspect = aspect;
        });
        setPreflight(null);
        setPlay(false);
        setTab('template');
        setManagementView('');
      });
      return;
    }
    const result = await safe(async () => {
      if (dirtyRef.current && !(await save())) return null;
      const next = await api(`/projects/${pRef.current.id}/templates/${item.id}/apply`, {
        asset_map: assetMap,
        aspect,
      });
      setP(next);
      setDirty(false);
      setPreview(null);
      setPreflight(null);
      setToast('Đã áp dụng template theo tỷ lệ đã xem trước');
      return next;
    });
    if (result) {
      setTab('template');
      setManagementView('');
    }
  };
  const edit = (fn) => {
    if (busy || active) return;
    editProject(fn);
    setPreflight(null);
    setPlay(false);
  };
  const create = () =>
    safe(async () => {
      if (dirtyRef.current && !(await save())) return;
      const next = await api('/projects', { name: 'Video mới', mode: 'remix' });
      setP(next);
      setDirty(false);
      setSelected(0);
      setCursor(0);
      setPreview(null);
      setPreflight(null);
      setProposal(null);
      await loadProjects();
    });
  const recover = (item) =>
    safe(async () => {
      const restored = await api(`/projects/${item.id}/recover`, {});
      await loadProjects();
      setToast('Đã phục hồi dự án từ lịch sử hợp lệ');
      if (p?.id === item.id) {
        setP(restored);
        setDirty(false);
        setPreview(null);
      }
    });
  const useEnvironmentSecret = (secret, draft) =>
    safe(async () => {
      await api('/settings', draft, 'PUT');
      const next = await api('/settings/use-environment', { secret });
      setSettings(next);
      setToast('Đã chuyển cấu hình sang dùng biến môi trường');
      return next;
    });
  useEffect(() => {
    if (!p?.id) return;
    const pid = p.id;
    for (const j of jobs) {
      if (
        !['done', 'error', 'cancelled', 'interrupted'].includes(j.status) ||
        jobSeen.current.has(j.id)
      )
        continue;
      jobSeen.current.add(j.id);
      if (j.status === 'error' || j.status === 'interrupted') setError(j.message);
      if (j.status === 'done') {
        if (j.kind === 'assistant') setProposal(j.result);
        else if (!dirtyRef.current) {
          api('/projects/' + pid)
            .then((updated) => {
              if (pRef.current?.id !== pid || dirtyRef.current) return;
              setP(updated);
              if (j.kind === 'upload' && j.result?.auto_assemble) {
                setPlay(false);
                setPreview(null);
                api(`/projects/${pid}/assemble-audio`, {})
                  .then((nextJob) => {
                    if (pRef.current?.id === pid) setJobs((old) => [...old, nextJob]);
                  })
                  .catch((e) => setError(e.message));
              }
              if (j.kind === 'render') {
                setPreview(updated.exports.at(-1));
                setPlay(false);
              }
              loadProjects();
            })
            .catch((e) => setError(e.message));
        }
        setToast(
          'Đã hoàn tất ' +
            ({
              analyze: 'phân tích cảnh',
              plan: 'lập timeline',
              voice: 'tạo giọng',
              template: 'phân tích mẫu',
              render: 'xuất video',
              transcribe: 'căn phụ đề',
              demo: 'dự án mẫu',
              assistant: 'đề xuất AI',
              upload: 'nhập tư liệu',
              'assemble-audio': 'dựng theo audio',
              'filter-faces': 'lọc và thay cảnh có mặt',
            }[j.kind] || ''),
        );
      }
    }
  }, [jobs, p?.id]);
  const run = (name, body = {}) =>
    safe(async () => {
      setPlay(false);
      if (dirtyRef.current && !(await save())) return null;
      if (name === 'render') {
        const report = await api(`/projects/${pRef.current.id}/preflight`);
        setPreflight(report);
        if (!report.ok) {
          setToast('Sửa các lỗi kiểm tra trước khi xuất video.');
          return null;
        }
      }
      const requestBody =
        name === 'assistant' ? { ...body, revision: pRef.current.revision } : body;
      const job = await api(`/projects/${pRef.current.id}/${name}`, requestBody);
      setJobs((old) => [...old, job]);
      return job;
    });
  const applyTemplate = (id, assetMap = {}) =>
    safe(async () => {
      if (dirtyRef.current && !(await save())) return;
      const next = await api(`/projects/${pRef.current.id}/templates/${id}/apply`, {
        asset_map: assetMap,
      });
      setP(next);
      setDirty(false);
      setPreview(null);
      setPreflight(null);
      setToast('Đã áp dụng template và nhập tài nguyên vào dự án');
    });
  const regenerateCues = () =>
    safe(async () => {
      if (dirtyRef.current && !(await save())) return;
      const current = pRef.current;
      const next = await api(`/projects/${current.id}/cues/regenerate`, {
        revision: current.revision,
      });
      setP(next);
      setDirty(false);
      setPreflight(null);
      setToast('Đã tạo lại mốc phụ đề');
    });
  const confirmCues = () =>
    safe(async () => {
      if (dirtyRef.current && !(await save())) return;
      const current = pRef.current;
      const next = await api(`/projects/${current.id}/cues/confirm`, {
        revision: current.revision,
      });
      setP(next);
      setDirty(false);
      setPreflight(null);
      setToast('Đã xác nhận giữ các mốc phụ đề hiện tại');
    });
  const upload = (files, role) => {
    const chosen = Array.from(files);
    return safe(async () => {
      if (!chosen.length) return;
      if (dirtyRef.current && !(await save())) return;
      const form = new FormData();
      chosen.forEach((f) => form.append('files', f));
      const detectedRole =
        role === 'source' &&
        chosen.every(
          (file) =>
            file.type.startsWith('audio/') || /\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(file.name),
        )
          ? 'voice'
          : role;
      form.append('role', detectedRole);
      form.append('auto_assemble', String(pRef.current.auto_audio_assembly !== false));
      const controller = new AbortController();
      uploadAbort.current = controller;
      setUploadState({ phase: 'uploading', progress: 0, count: chosen.length });
      try {
        const response = await uploadWithProgress(
          `/projects/${pRef.current.id}/assets`,
          form,
          (progress) =>
            setUploadState((old) =>
              old ? { ...old, progress, phase: progress >= 100 ? 'processing' : 'uploading' } : old,
            ),
          controller.signal,
        );
        if (response.job) setJobs((old) => [...old, response.job]);
        setToast('Đã tải file lên; ClipForge đang kiểm tra tư liệu');
        return response.job;
      } finally {
        uploadAbort.current = null;
        setUploadState(null);
      }
    });
  };
  const demo = () =>
    safe(async () => {
      if (dirtyRef.current && !(await save())) return;
      const data = await api('/demo', {});
      setP(data.project);
      setDirty(false);
      setSelected(0);
      setCursor(0);
      setPreview(null);
      setJobs([data.job]);
      await loadProjects();
    });
  const historyAction = (kind) =>
    safe(async () => {
      if (dirtyRef.current && !(await save())) return;
      const pid = pRef.current.id;
      const next = await api(`/projects/${pid}/${kind}`, {});
      setP(next);
      setDirty(false);
      setHistory(await api(`/projects/${pid}/history`));
      await loadProjects();
    });
  const duration = timelineDuration(p?.clips || []);
  useEffect(() => {
    if (p) {
      if (selected >= p.clips.length) setSelected(Math.max(0, p.clips.length - 1));
      if (cursor > duration) setCursor(duration);
    }
  }, [p?.clips.length, duration]);
  const locked = busy || !!active || !!conflict;
  const selectClip = (i, preserveCursor = false) => {
    setSelected(i);
    if (!preserveCursor) setCursor(clipStarts(p.clips)[i] || 0);
    setPlay(false);
    setPreview(null);
  };
  const selectAtTime = (time) => setSelected(clipAtTime(p.clips, time));
  const addScene = (a, sc) =>
    edit((n) => {
      n.clips.push({
        id: crypto.randomUUID().replaceAll('-', '').slice(0, 16),
        asset_id: a.id,
        scene_id: sc?.id || '',
        source_start: sc?.start || 0,
        duration: Math.min(3, sc ? sc.end - sc.start : a.duration || 3),
        speed: 1,
        fit: 'cover',
        crop_x: 0.5,
        crop_y: 0.5,
        title: n.name,
        caption: '',
        transition: 'cut',
        transition_duration: 0.4,
        layers: [],
      });
    });
  const splitAtPlayhead = () => {
    if (locked) return;
    const clip = p?.clips[selected];
    if (!clip || clip.locked) return;
    const start = clipStarts(p.clips)[selected] || 0;
    const leftDuration = Math.round((cursor - start) * 30) / 30;
    const rightDuration = Number((clip.duration - leftDuration).toFixed(6));
    if (leftDuration < 0.2 || rightDuration < 0.2) {
      setToast('Mỗi phần sau khi tách phải dài ít nhất 0,2 giây.');
      return;
    }
    if (p.clips.length >= 150) {
      setToast('Timeline đã đạt giới hạn 150 cảnh.');
      return;
    }
    const asset = aid(p, clip.asset_id);
    const scene = asset?.scenes.find((item) => item.id === clip.scene_id);
    const limit = scene?.end ?? asset?.duration ?? Number.POSITIVE_INFINITY;
    const frameDuration = 1 / Math.max(asset?.fps || 30, 1);
    const nextSourceStart = Math.min(
      clip.source_start + leftDuration * clip.speed,
      Math.max(clip.source_start, limit - frameDuration),
    );
    const right = {
      ...structuredClone(clip),
      id: crypto.randomUUID().replaceAll('-', '').slice(0, 16),
      locked: false,
      transition: 'cut',
      source_start: Number(nextSourceStart.toFixed(6)),
      duration: rightDuration,
    };
    edit((next) => {
      next.clips.splice(selected, 1, { ...next.clips[selected], duration: leftDuration }, right);
    });
    setSelected(selected + 1);
    setCursor(start + leftDuration);
  };
  const duplicateSelected = () => {
    if (locked) return;
    const clip = p?.clips[selected];
    if (!clip) return;
    if (p.clips.length >= 150) {
      setToast('Không thể nhân đôi: timeline tối đa 180 giây và 150 cảnh.');
      return;
    }
    const copy = {
      ...structuredClone(clip),
      id: crypto.randomUUID().replaceAll('-', '').slice(0, 16),
      locked: false,
    };
    const candidate = [...p.clips.slice(0, selected + 1), copy, ...p.clips.slice(selected + 1)];
    if (timelineDuration(candidate) > 180) {
      setToast('Không thể nhân đôi: timeline tối đa 180 giây và 150 cảnh.');
      return;
    }
    const start = clipStarts(candidate)[selected + 1];
    edit((next) => next.clips.splice(selected + 1, 0, copy));
    setSelected(selected + 1);
    setCursor(start);
  };
  const deleteSelected = () => {
    if (locked) return;
    const clip = p?.clips[selected];
    if (!clip || clip.locked) return;
    const nextIndex = Math.max(0, Math.min(selected, p.clips.length - 2));
    const nextCursor = clipStarts(p.clips)[nextIndex] || 0;
    edit((next) => next.clips.splice(selected, 1));
    setSelected(nextIndex);
    setCursor(nextCursor);
  };
  const toggleSelectedLock = () => {
    if (locked) return;
    if (!p?.clips[selected]) return;
    edit((next) => {
      next.clips[selected].locked = !next.clips[selected].locked;
    });
  };
  const focusIssue = (issue) => {
    const entity = issue.entity || {};
    if (entity.type === 'clip' || entity.type === 'layer') {
      const clipIndex = p.clips.findIndex(
        (clip) => clip.id === entity.id || clip.layers.some((layer) => layer.id === entity.id),
      );
      if (clipIndex >= 0) {
        selectClip(clipIndex);
        requestAnimationFrame(() =>
          document
            .querySelector(
              `[data-clip-id="${entity.type === 'clip' ? entity.id : p.clips[clipIndex].id}"]`,
            )
            ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' }),
        );
        return;
      }
      if (entity.type === 'layer') {
        setTab('template');
        requestAnimationFrame(() => {
          const layer = document.querySelector(`[data-layer-id="${entity.id}"]`);
          const target = layer || document.querySelector('[data-template-layout]');
          if (target instanceof HTMLDetailsElement) target.open = true;
          target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        return;
      }
    } else if (entity.type === 'cue') {
      const cueIndex = Number(entity.id) - 1;
      if (cueIndex >= 0 && p.cues[cueIndex]) setCursor(p.cues[cueIndex].start);
      setTab('script');
      requestAnimationFrame(() => {
        const editor = document.querySelector('[data-cue-editor]');
        if (editor) editor.open = true;
        requestAnimationFrame(() =>
          document
            .querySelector(`[data-cue-id="${entity.id}"]`)
            ?.scrollIntoView({ behavior: 'smooth', block: 'center' }),
        );
      });
      return;
    } else if (entity.type === 'voice' || entity.type === 'music') {
      setTab('audio');
      return;
    } else if (entity.type === 'asset') {
      setTab('media');
      requestAnimationFrame(() =>
        document
          .querySelector(`[data-asset-id="${entity.id}"]`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' }),
      );
      return;
    }
    document.querySelector('.timeline')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  const conflictKeys = conflict
    ? Object.keys(conflict.project || {}).filter(
        (key) => JSON.stringify(conflict.project[key]) !== JSON.stringify(conflict.server?.[key]),
      )
    : [];
  useEffect(() => {
    const onKeyDown = (event) => {
      if (!p || locked) return;
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest('input, textarea, select, [contenteditable="true"]')
      )
        return;
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && key === 'z') {
        event.preventDefault();
        historyAction(event.shiftKey ? 'redo' : 'undo');
        return;
      }
      if ((event.ctrlKey || event.metaKey) && key === 'y') {
        event.preventDefault();
        historyAction('redo');
        return;
      }
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (
        target instanceof Element &&
        target.closest('button, a, [role="slider"]') &&
        !target.closest('.timeline, .preview-controls')
      )
        return;
      if (key === ' ' || key === 'spacebar') {
        event.preventDefault();
        setPreview(null);
        if (cursor >= duration) setCursor(0);
        setPlay((value) => !value);
      } else if (key === 's') {
        event.preventDefault();
        splitAtPlayhead();
      } else if (key === 'delete' || key === 'backspace') {
        event.preventDefault();
        deleteSelected();
      } else if (key === 'arrowleft' || key === 'arrowright') {
        event.preventDefault();
        const next = Math.max(
          0,
          Math.min(p.clips.length - 1, selected + (key === 'arrowright' ? 1 : -1)),
        );
        if (p.clips[next]) selectClip(next);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [
    p,
    locked,
    selected,
    cursor,
    duration,
    historyAction,
    splitAtPlayhead,
    deleteSelected,
    selectClip,
  ]);
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon">
            <Clapperboard size={22} />
          </div>
          <b>
            clipforge<span>STUDIO</span>
          </b>
        </div>
        <span className="local-badge">
          <i /> LOCAL WORKSPACE
        </span>
        <div className="top-actions">
          <span className="muted">Video ngắn. Câu chuyện của bạn.</span>
          <span
            className={
              'api-status ' +
              (settings?.ai_profiles?.some((x) => x.enabled && x.api_key) ? 'ready' : 'missing')
            }
          >
            {settings?.ai_profiles?.some((x) => x.enabled && x.api_key)
              ? 'AI đã kết nối'
              : 'Chưa có API AI'}
          </span>
          <Button small icon={HardDrive} onClick={() => openManagement('storage')}>
            Dung lượng
          </Button>
          <Button small icon={Library} onClick={() => openManagement('templates')}>
            Template
          </Button>
          <Button icon={KeyRound} onClick={() => setShowSettings(true)}>
            Cài đặt API
          </Button>
        </div>
      </header>
      <div className={`workspace ${!showProjects ? 'collapsed' : ''}`}>
        <aside className="project-sidebar">
          <div className="sidebar-head">
            <span>DỰ ÁN CỦA BẠN</span>
            <button
              className="icon-button"
              aria-label="Tạo dự án"
              disabled={locked}
              onClick={create}
            >
              <Plus size={18} />
            </button>
          </div>
          <div className="project-list">
            {projects.map((item) =>
              item.corrupt ? (
                <div key={item.id} className="project-item project-corrupt">
                  <AlertCircle size={18} />
                  <span>
                    <strong>{item.name}</strong>
                    <small>
                      {item.recovery_available
                        ? 'Có thể phục hồi từ lịch sử'
                        : 'Không có lịch sử hợp lệ'}
                    </small>
                  </span>
                  <Button
                    small
                    disabled={locked || !item.recovery_available}
                    onClick={() => recover(item)}
                  >
                    Phục hồi
                  </Button>
                </div>
              ) : (
                <button
                  disabled={locked}
                  key={item.id}
                  className={`project-item ${p?.id === item.id ? 'selected' : ''}`}
                  onClick={() =>
                    safe(async () => {
                      await open(item.id);
                      setManagementView('');
                    })
                  }
                >
                  <Film size={18} />
                  <span>
                    <strong>{item.name}</strong>
                    <small>
                      {item.mode === 'template' ? 'Theo mẫu' : 'Tái biên tập'} · {item.clips} cảnh
                    </small>
                  </span>
                </button>
              ),
            )}
          </div>
          <div className="sidebar-bottom">
            <div className="mini-label">BẮT ĐẦU NHANH</div>
            <button className="sample-card" onClick={demo} disabled={locked}>
              <span className="sample-art">✦</span>
              <span>
                Thử dự án mẫu<small>5 cảnh · nhạc · timeline</small>
              </span>
              <ArrowRight size={16} />
            </button>
            <small className="storage-note">
              Tư liệu được lưu trên máy.
              <br />
              AI sử dụng API bạn cấu hình.
            </small>
          </div>
        </aside>
        <main className="main-workspace">
          {managementView === 'storage' ? (
            <StoragePanel
              onOpenProject={openFromManagement}
              onTrashed={projectTrashed}
              beforeTrash={beforeTrashProject}
            />
          ) : managementView === 'templates' ? (
            <TemplateLibrary
              templates={templates}
              p={p}
              busy={locked}
              onApply={applyLibraryTemplate}
              onRename={(id, name) =>
                safe(async () => {
                  await api(`/template-packages/${id}`, { name }, 'PATCH');
                  await reloadTemplates();
                  setToast('Đã đổi tên template');
                  return true;
                })
              }
              onDuplicate={(id) =>
                safe(async () => {
                  await api(`/template-packages/${id}/duplicate`, {});
                  await reloadTemplates();
                  setToast('Đã nhân bản template');
                })
              }
              onDelete={(id) =>
                safe(async () => {
                  await api(`/template-packages/${id}`, undefined, 'DELETE');
                  await reloadTemplates();
                  setToast('Đã xóa template');
                })
              }
            />
          ) : !p ? (
            <div className="welcome">
              <div className="welcome-symbol">
                <Clapperboard size={58} />
              </div>
              <span className="eyebrow">KHÔNG GIAN DỰNG VIDEO CỦA BẠN</span>
              <h1>
                Từ tư liệu thô
                <br />
                đến <em>câu chuyện ngắn.</em>
              </h1>
              <p>
                Tạo video 30–45 giây theo mẫu hoặc ghép cảnh theo kịch bản.
                <br />
                Giữ mọi bản dựng trong một không gian trên máy của bạn.
              </p>
              <div className="welcome-actions">
                <Button primary icon={Plus} onClick={create} disabled={locked}>
                  Tạo video đầu tiên
                </Button>
                <Button icon={Play} onClick={demo} disabled={locked}>
                  Khám phá dự án mẫu
                </Button>
              </div>
              <div className="welcome-modes">
                <div>
                  <LayoutTemplate />
                  <b>Dựng theo mẫu</b>
                  <span>Khung, biểu tượng và nhịp dựng nhất quán.</span>
                </div>
                <div>
                  <Scissors />
                  <b>Tái biên tập</b>
                  <span>Chọn cảnh từ 5–10 video theo lời đọc.</span>
                </div>
                <div>
                  <Sparkles />
                  <b>AI bên cạnh</b>
                  <span>Tìm cảnh, tạo voice và đề xuất chỉnh sửa.</span>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="project-toolbar">
                <button
                  className="icon-button"
                  title="Ẩn/hiện danh sách"
                  onClick={() => setShowProjects(!showProjects)}
                >
                  <PanelLeftClose size={18} />
                </button>
                <input
                  className="project-name"
                  aria-label="Tên dự án"
                  value={p.name}
                  disabled={locked}
                  onChange={(e) => edit((n) => (n.name = e.target.value))}
                />
                <span className="save-state">
                  {saveStatus === 'saving'
                    ? 'Đang lưu…'
                    : saveStatus === 'conflict'
                      ? 'Có xung đột'
                      : dirty
                        ? 'Bản nháp trên máy'
                        : 'Đã lưu trên máy'}
                </span>
                <div className="toolbar-actions">
                  <details className="schema-backup-menu project-backup-menu">
                    <summary className="schema-backup-link">
                      <Archive size={14} /> Backup ZIP
                    </summary>
                    <div className="schema-backup-options backup-options">
                      <label>
                        <input
                          type="checkbox"
                          checked={backupExports}
                          onChange={(event) => setBackupExports(event.target.checked)}
                        />{' '}
                        Kèm bản xuất
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={backupHistory}
                          onChange={(event) => setBackupHistory(event.target.checked)}
                        />{' '}
                        Kèm lịch sử hoàn tác
                      </label>
                      <a
                        href={`/api/projects/${p.id}/backup?include_exports=${backupExports}&include_history=${backupHistory}`}
                        download
                        onClick={async (event) => {
                          event.preventDefault();
                          const href = event.currentTarget.href;
                          if (dirtyRef.current && !(await save())) return;
                          window.location.href = href;
                        }}
                      >
                        <Download size={14} /> Tải backup dự án
                      </a>
                    </div>
                  </details>
                  {schemaBackups.length > 0 && (
                    <details className="schema-backup-menu">
                      <summary
                        className="button small schema-backup-link"
                        title="Tải bản JSON gốc và media đã sao lưu; hướng dẫn khôi phục nằm trong manifest của ZIP"
                      >
                        <Download size={14} />
                        Bản gốc ({schemaBackups.length})
                      </summary>
                      <div className="schema-backup-options">
                        {schemaBackups
                          .slice()
                          .reverse()
                          .map((backup) => (
                            <a
                              key={backup.id}
                              href={`/api/projects/${p.id}/schema-backups/${backup.id}/download`}
                              download
                              title={`${backup.asset_count} file media đã sao lưu${backup.missing_assets.length ? ` · thiếu ${backup.missing_assets.length}` : ''}`}
                            >
                              Schema {backup.source_schema_version} ·{' '}
                              {new Date(backup.created_at).toLocaleString()}
                            </a>
                          ))}
                      </div>
                    </details>
                  )}
                  <Button
                    small
                    icon={Undo2}
                    disabled={locked || (!history.can_undo && !dirty)}
                    onClick={() => historyAction('undo')}
                  >
                    Hoàn tác
                  </Button>
                  <Button
                    small
                    icon={Redo2}
                    disabled={locked || !history.can_redo}
                    onClick={() => historyAction('redo')}
                  >
                    Làm lại
                  </Button>
                  <Button small icon={Save} disabled={locked || !dirty} onClick={() => safe(save)}>
                    Lưu
                  </Button>
                  <Button
                    small
                    primary
                    icon={Download}
                    disabled={locked || !p.clips.length}
                    onClick={() => run('render', { preview: false })}
                  >
                    Xuất video
                  </Button>
                </div>
              </div>
              <div className="editor-grid">
                <section className="control-pane">
                  <nav className="tabs">
                    {tabs.map(([key, label, Icon]) => (
                      <button
                        key={key}
                        className={tab === key ? 'active' : ''}
                        onClick={() => setTab(key)}
                      >
                        <Icon size={19} />
                        <span>{label}</span>
                      </button>
                    ))}
                  </nav>
                  <div className="control-scroll">
                    <div className="mode-selector">
                      <button
                        disabled={locked}
                        className={p.mode === 'remix' ? 'active' : ''}
                        onClick={() => edit((n) => (n.mode = 'remix'))}
                      >
                        <Scissors size={15} />
                        Tái biên tập
                      </button>
                      <button
                        disabled={locked}
                        className={p.mode === 'template' ? 'active' : ''}
                        onClick={() => edit((n) => (n.mode = 'template'))}
                      >
                        <LayoutTemplate size={15} />
                        Theo mẫu
                      </button>
                    </div>
                    {tab === 'media' && (
                      <MediaPanel
                        {...{ p, upload, locked, run, useAI, setUseAI, edit, addScene }}
                        onDelete={(id) =>
                          safe(async () => {
                            if (dirtyRef.current && !(await save())) return;
                            const next = await api(
                              `/projects/${p.id}/assets/${id}`,
                              undefined,
                              'DELETE',
                            );
                            setP(next);
                            setDirty(false);
                            setToast('Đã gỡ tư liệu. Có thể dùng Hoàn tác để khôi phục chỉnh sửa.');
                          })
                        }
                      />
                    )}
                    {tab === 'script' && (
                      <ScriptPanel
                        {...{ p, edit, locked, run, useAI, setUseAI }}
                        onRegenerateCues={regenerateCues}
                        onConfirmCues={confirmCues}
                      />
                    )}
                    {tab === 'template' && (
                      <TemplatePanel
                        {...{
                          p,
                          edit,
                          locked,
                          templates,
                          run,
                          useAI,
                          setUseAI,
                          applyTemplate,
                          fonts,
                        }}
                        onSave={() =>
                          safe(async () => {
                            const t = await api(`/projects/${p.id}/templates`, p.template);
                            setTemplates((old) => [...old, t]);
                            setToast('Đã đóng gói mẫu cùng tài nguyên ảnh');
                          })
                        }
                      />
                    )}
                    {tab === 'audio' && (
                      <AudioPanel
                        {...{ p, edit, locked, run, upload, cursor, duration }}
                        settings={settings}
                        onSettings={() => setShowSettings(true)}
                      />
                    )}
                    {tab === 'ai' && (
                      <AssistantPanel
                        {...{ p, locked, dirty, run, proposal }}
                        onPreview={(request) => api(`/projects/${p.id}/assistant-preview`, request)}
                        onApply={(request) =>
                          safe(async () => {
                            if (dirtyRef.current) {
                              setToast(
                                'Lưu hoặc bỏ chỉnh sửa rồi gửi lại yêu cầu AI để làm mới revision.',
                              );
                              return;
                            }
                            const next = await api(`/projects/${p.id}/apply-ai`, request);
                            setP(next);
                            setDirty(false);
                            setProposal(null);
                            setPreview(null);
                            setPreflight(null);
                            setHistory(await api(`/projects/${p.id}/history`));
                          })
                        }
                        onSettings={() => setShowSettings(true)}
                      />
                    )}
                  </div>
                </section>
                <section className="preview-pane">
                  <div className="section-heading">
                    <span>
                      <i className="dot" /> XEM TRƯỚC
                    </span>
                    <div className="select-inline">
                      <select
                        aria-label="Tỷ lệ khung"
                        disabled={locked}
                        value={p.aspect}
                        onChange={(e) => edit((n) => (n.aspect = e.target.value))}
                      >
                        <option>9:16</option>
                        <option>16:9</option>
                        <option>1:1</option>
                      </select>
                      <select
                        aria-label="Độ phân giải"
                        disabled={locked}
                        value={p.resolution}
                        onChange={(e) => edit((n) => (n.resolution = e.target.value))}
                      >
                        <option value="1080">1080p</option>
                        <option value="720">720p</option>
                      </select>
                    </div>
                  </div>
                  <Preview
                    {...{ p, selected, preview, play, setPlay, cursor, setCursor, duration }}
                    onSelect={setSelected}
                    previewIsStale={!!preview && (dirty || preview.project_revision !== p.revision)}
                    onReturnLive={() => {
                      setPreview(null);
                      setPlay(false);
                    }}
                  />
                  <div className="preview-controls">
                    <Button
                      small
                      icon={Play}
                      disabled={!p.clips.length}
                      onClick={() => {
                        setPreview(null);
                        if (cursor >= duration) setCursor(0);
                        setPlay(!play);
                      }}
                    >
                      {play ? 'Tạm dừng' : 'Phát timeline'}
                    </Button>
                    <span className="timecode">
                      {fmt(cursor)} <span>/ {fmt(duration)}</span>
                    </span>
                    <Button
                      small
                      icon={RefreshCw}
                      disabled={locked || !p.clips.length}
                      onClick={() => run('render', { preview: true })}
                    >
                      Dựng xem thử
                    </Button>
                  </div>
                  {!preview && (
                    <input
                      aria-label="Vị trí phát"
                      className="scrubber"
                      type="range"
                      min="0"
                      max={duration || 1}
                      step=".05"
                      value={Math.min(cursor, duration)}
                      disabled={!p.clips.length}
                      onChange={(e) => {
                        const time = Number(e.target.value);
                        setCursor(time);
                        selectAtTime(time);
                        setPlay(false);
                      }}
                    />
                  )}
                  <small className="preview-note">
                    {preview
                      ? 'Bản MP4 đã dựng · ' + preview.width + ' × ' + preview.height
                      : 'Xem nhanh bố cục. Dựng xem thử để kiểm tra chính xác hiệu ứng và âm thanh.'}
                  </small>
                </section>
                <aside className="inspector">
                  <div className="section-heading">
                    <span>
                      <SlidersHorizontal size={15} /> CHỈNH CẢNH
                    </span>
                    <span className="count-pill">
                      {p.clips.length ? String(selected + 1).padStart(2, '0') : '—'}
                    </span>
                  </div>
                  <ClipInspector {...{ p, selected, edit, locked }} />
                  <div className="export-history">
                    <h4>Bản xuất gần đây</h4>
                    {p.exports.length === 0 ? (
                      <p className="muted">MP4 và phụ đề sẽ xuất hiện ở đây.</p>
                    ) : (
                      p.exports
                        .slice(-4)
                        .reverse()
                        .map((e) => (
                          <div className="export-item" key={e.id}>
                            <div className="export-item-main">
                              <button
                                onClick={() => {
                                  setPreview(e);
                                  setPlay(false);
                                }}
                              >
                                <Play size={14} />
                                {e.preview ? 'Xem thử' : 'Video'} · {e.duration}s
                              </button>
                              {e.encoder && (
                                <small>
                                  {e.hardware_encoder ? 'GPU' : 'CPU'} · {e.encoder}
                                  {' · cache '}
                                  {e.clip_cache_hits || 0}/
                                  {(e.clip_cache_hits || 0) + (e.clip_cache_misses || 0)}
                                  {e.proxies_used ? ` · ${e.proxies_used} proxy` : ''}
                                </small>
                              )}
                            </div>
                            <a href={exportUrl(p, e)} download={`${p.name}.mp4`} title="Tải MP4">
                              <Download size={15} />
                            </a>
                            <a href={exportUrl(p, e, 'captions.srt')} title="Tải SRT">
                              SRT
                            </a>
                          </div>
                        ))
                    )}
                    <a className="text-link" href={`/api/projects/${p.id}/download`} download>
                      Xuất dự án JSON
                    </a>
                  </div>
                </aside>
              </div>
              <Timeline
                p={p}
                selected={selected}
                cursor={cursor}
                setCursor={setCursor}
                setPlay={setPlay}
                onSelect={selectClip}
                edit={edit}
                locked={locked}
                duration={duration}
                onSplit={splitAtPlayhead}
                onDuplicate={duplicateSelected}
                onDelete={deleteSelected}
                onToggleLock={toggleSelectedLock}
                useAI={useAI}
                setUseAI={setUseAI}
                onPlan={() => run('plan', { use_ai: useAI })}
              />
              {p.warnings.length > 0 && (
                <div className="warnings">
                  <AlertCircle size={14} />
                  <span>{p.warnings.join(' ')}</span>
                </div>
              )}
              {preflight?.issues?.length > 0 && (
                <section className="preflight-panel" aria-live="polite">
                  <strong>
                    Kiểm tra trước khi xuất ·{' '}
                    {preflight.ok ? 'Có lưu ý cần xem' : 'Cần sửa trước khi xuất'}
                  </strong>
                  {preflight.issues.map((issue, i) => (
                    <button
                      className={'preflight-issue ' + issue.severity}
                      key={issue.code + '-' + i}
                      onClick={() => focusIssue(issue)}
                      title="Đi đến vị trí cần kiểm tra"
                    >
                      <b>{issue.code}</b>
                      <span>{issue.message}</span>
                      <small>{issue.fix}</small>
                      <em>Đi tới vị trí</em>
                    </button>
                  ))}
                </section>
              )}
            </>
          )}
        </main>
      </div>
      {conflict && (
        <section className="draft-conflict" role="alert">
          <div>
            <strong>Dự án đã được thay đổi ở nơi khác</strong>
            <p>
              Bản nháp của bạn vẫn được giữ trên máy. Chọn xem khác biệt hoặc tải bản mới trước khi
              tiếp tục chỉnh sửa.
            </p>
          </div>
          <div className="row">
            <Button small onClick={() => setShowConflictDiff((value) => !value)}>
              {showConflictDiff ? 'Ẩn khác biệt' : 'Xem khác biệt'}
            </Button>
            <Button
              small
              primary
              onClick={() =>
                safe(async () => {
                  await loadLatest();
                  setShowConflictDiff(false);
                })
              }
            >
              Tải bản mới
            </Button>
          </div>
          {showConflictDiff && (
            <div className="conflict-diff">
              {conflictKeys.length ? (
                conflictKeys.map((key) => (
                  <div key={key}>
                    <b>{key}</b>
                    <div className="conflict-columns">
                      <pre>{JSON.stringify(conflict.project[key], null, 2)}</pre>
                      <pre>{JSON.stringify(conflict.server?.[key], null, 2)}</pre>
                    </div>
                  </div>
                ))
              ) : (
                <p>Không có trường khác nhau.</p>
              )}
            </div>
          )}
        </section>
      )}
      {jobs.some((job) => job.status === 'interrupted') && (
        <div className="interrupted-note">
          <AlertCircle size={15} />
          <span>
            Tác vụ dở dang được đánh dấu gián đoạn sau khi ứng dụng đóng. ClipForge không tự chạy
            lại; hãy chủ động chọn lại thao tác cần thiết.
          </span>
        </div>
      )}
      {uploadState && (
        <div className="job-banner upload-banner">
          <Upload size={18} />
          <div>
            <b>
              {uploadState.phase === 'uploading'
                ? 'Đang tải lên tư liệu'
                : 'Đã tải file · đang chuẩn bị kiểm tra'}
            </b>
            <div className="progress">
              <i style={{ width: uploadState.progress + '%' }} />
            </div>
          </div>
          <span>{uploadState.progress}%</span>
          <Button small onClick={() => uploadAbort.current?.abort()}>
            Hủy tải lên
          </Button>
        </div>
      )}
      {active && (
        <div className="job-banner">
          <Loader2 className="spin" size={18} />
          <div>
            <b>{active.message}</b>
            <small>
              {active.phase === 'processing'
                ? 'Đang xử lý trên máy'
                : active.phase === 'cancelling'
                  ? 'Đang hủy; yêu cầu gửi tới dịch vụ có thể vẫn đang xử lý'
                  : 'Đang chờ'}
            </small>
            {active.provider_calls?.length > 0 &&
              (() => {
                const latest = active.provider_calls.at(-1);
                const elapsed = active.provider_calls.reduce(
                  (sum, call) => sum + (call.elapsed_ms || 0),
                  0,
                );
                return (
                  <small className="provider-telemetry">
                    {latest.provider} · {latest.model} · {active.provider_calls.length} lần gọi
                    {active.provider_calls.some((call) => call.fallback)
                      ? ' · có chuyển dự phòng'
                      : ''}
                    {' · '}
                    {(elapsed / 1000).toFixed(1)}s
                  </small>
                );
              })()}
            <div className="progress">
              <i style={{ width: active.progress + '%' }} />
            </div>
          </div>
          <span>{Math.round(active.progress)}%</span>
          <Button
            small
            disabled={active.status === 'cancelling'}
            onClick={() => safe(() => cancelJob(active.id))}
          >
            {active.status === 'cancelling' ? 'Đang hủy…' : 'Hủy'}
          </Button>
        </div>
      )}
      {error && (
        <div className="error-toast" role="alert">
          <AlertCircle size={18} />
          <span>{error}</span>
          <button onClick={() => setError('')} aria-label="Đóng lỗi">
            <X size={16} />
          </button>
        </div>
      )}
      {toast && (
        <div className="toast">
          <CheckCircle2 size={17} />
          {toast}
        </div>
      )}
      {showSettings && settings && (
        <SettingsModal
          settings={settings}
          onClose={() => setShowSettings(false)}
          onUseEnvironment={useEnvironmentSecret}
          onSave={(s) =>
            safe(async () => {
              const next = await api('/settings', s, 'PUT');
              setSettings(next);
              setToast('Đã lưu cài đặt API');
              setShowSettings(false);
            })
          }
          onTest={(s, id) =>
            safe(async () => {
              const next = await api('/settings', s, 'PUT');
              setSettings(next);
              const result = await api('/settings/test', { profile_id: id, allow_fallback: false });
              setToast(result.message || 'Kết nối AI thành công');
              return {
                settings: next,
                message: result.message || 'Kết nối AI thành công',
                route: result.route,
              };
            })
          }
        />
      )}
    </div>
  );
}
