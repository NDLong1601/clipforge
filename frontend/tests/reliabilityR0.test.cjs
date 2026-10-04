const test = require('node:test');
const assert = require('node:assert/strict');
const { context, nodes, drain } = require('./harness.cjs');

// Historical F5/F9 regressions are green after R4. Names keep finding IDs.
function regression(name, finding, fn) { test(`${finding}: ${name}`, fn); }

const pid = 'a'.repeat(16), jid = 'b'.repeat(16);
const project = () => ({ id: pid, revision: 1, name: 'R0 fixture', script: 'Original',
  assets: [], clips: [], cues: [], exports: [], warnings: [], template: { layers: [], caption: {} } });
const terminalJob = (status, attempt = 1, kind = 'render') => ({ id: jid, pid, kind,
  status, attempt, message: 'fixture', state_version: attempt, result: { id: jid } });

async function mountApp() {
  let jobs = [], current = project(), dirty = false, preview = null;
  const pRef = { current }, dirtyRef = { current: false }, gets = [], applied = [];
  const latest = { ...current, revision: 2, exports: [{ id: jid, filename: 'preview.mp4' }] };
  let c;
  const api = async (url) => {
    if (url === `/projects/${pid}`) { gets.push(url); return structuredClone(latest); }
    if (url === '/projects') return [];
    if (url === '/templates' || url === '/template-packages') return [];
    if (url.endsWith('/history')) return { can_undo: false, can_redo: false };
    if (url.endsWith('/schema-backups')) return { backups: [] };
    if (url === '/fonts') return { fonts: ['Arial'] };
    if (url === '/settings') return {};
    throw new Error(`Unexpected R0 App API ${url}`);
  };
  const session = () => ({ p: current, dirty, pRef, dirtyRef,
    setP(value) { current = value; pRef.current = value; applied.push(value); },
    setDirty(value) { dirty = value; dirtyRef.current = value; },
    edit() {}, save: async () => current, open: async () => current,
    saveStatus: 'saved', conflict: null, loadLatest() {},
  });
  c = context(api, undefined, undefined, {
    'src/hooks/useProjectSession.js': { useProjectSession: session },
    'src/hooks/useJobs.js': { useJobs: () => ({ jobs, setJobs(value) {
      jobs = typeof value === 'function' ? value(jobs) : value;
    }, active: jobs.find(job => ['queued', 'running'].includes(job.status)), cancel() {} }) },
    'src/hooks/usePreviewState.js': { usePreviewState: () => ({ preview,
      setPreview(value) { preview = value; }, play: false, setPlay() {}, cursor: 0, setCursor() {} }) },
  });
  const App = c.load('src/App.jsx').default;
  const render = () => c.h.mount(() => App());
  render(); await drain(c);
  return { c, latest, gets, applied, get preview() { return preview; },
    async poll(status, attempt) { jobs = [terminalJob(status, attempt)]; render(); await drain(c); },
    async dirty(value) { dirty = value; dirtyRef.current = value; render(); await drain(c); },
  };
}

regression('attempt 2 done refreshes the editor exactly once after attempt 1 error', 'F5', async () => {
  const a = await mountApp();
  try {
    await a.poll('error', 1);
    await a.poll('queued', 2);
    await a.poll('running', 2);
    await a.poll('done', 2);
    await a.poll('done', 2);
    assert.equal(a.gets.length, 1, 'F5: resumed done event must fetch the project once');
    assert.equal(a.applied.length, 1);
    assert.equal(a.preview.id, jid);
  } finally { a.c.h.unmount(); }
});

regression('done during a dirty draft remains pending until synchronization is safe', 'F5', async () => {
  const a = await mountApp();
  try {
    await a.dirty(true);
    await a.poll('done', 2);
    assert.equal(a.gets.length, 0);
    await a.dirty(false);
    await a.poll('done', 2);
    assert.equal(a.gets.length, 1, 'F5: dirty draft must not consume and lose the done event');
    assert.equal(a.applied.length, 1);
  } finally { a.c.h.unmount(); }
});

regression('render preview selects this job export when another export completes later', 'F5', async () => {
  const a = await mountApp();
  try {
    a.latest.exports.push({ id: 'c'.repeat(16), filename: 'other.mp4' });
    await a.poll('done', 2);
    assert.equal(a.gets.length, 1);
    assert.equal(a.preview.id, jid, 'F5: preview must belong to the completing job');
  } finally { a.c.h.unmount(); }
});

function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  return (node.props?.children || []).flat(Infinity).map(text).join(' ');
}
const controls = (tree, label) => nodes(tree, node => typeof node.props?.onClick === 'function' && label.test(text(node)));

async function mountTasks(status) {
  const job = { ...terminalJob(status), resume_capability: 'checkpointed', can_resume: false,
    created_at: '2026-10-03T00:00:00Z', steps: [], provider_calls: [], error_history: [] };
  const requests = [];
  const api = async (url, body, method) => {
    requests.push({ url, body, method });
    if (url === '/jobs/queue') return { jobs: [structuredClone(job)], queued: 0, running: 0 };
    if (url === '/projects') return [{ id: pid, name: 'R0 fixture' }];
    if (url === '/storage') return { total: 0 };
    if (url === `/jobs/${jid}`) return structuredClone(job);
    if (url === `/jobs/${jid}/cancel`) { job.status = 'cancelled'; return structuredClone(job); }
    throw new Error(`Unexpected R0 TaskCenter API ${url}`);
  };
  const c = context(api), { TaskCenter } = c.load('src/components/TaskCenter.jsx');
  c.h.mount(() => TaskCenter({ onClose() {}, onOpenSettings() {}, onOpenStorage() {} }));
  await drain(c);
  return { c, requests };
}

for (const status of ['error', 'interrupted', 'blocked']) {
  for (const surface of ['row', 'detail']) {
    regression(`${status} exposes discard in ${surface} and refreshes queue after cancellation`, 'F9', async () => {
      const a = await mountTasks(status);
      try {
        let tree = a.c.h.current;
        if (surface === 'detail') {
          const details = controls(tree, /^Chi tiết$/)[0];
          assert.ok(details, 'R0 fixture must expose job details');
          const opening = details.props.onClick(); a.c.h.flush(); await opening; await drain(a.c);
          tree = nodes(a.c.h.current, node => node.props?.className === 'task-detail')[0];
          assert.ok(tree, 'R0 fixture must load the actual detail component');
        } else {
          tree = nodes(tree, node => node.props?.className?.startsWith('task-row '))[0];
        }
        const discard = controls(tree, /Bỏ tác vụ.*dữ liệu phục hồi/)[0];
        assert.ok(discard, `F9: ${status} has no recovery discard control in ${surface}`);
        const before = a.requests.length;
        await discard.props.onClick(); await drain(a.c);
        const mutations = a.requests.slice(before);
        assert.equal(mutations.filter(request => request.url === `/jobs/${jid}/cancel`).length, 1);
        assert.ok(mutations.some(request => request.url === '/jobs/queue'), 'Discard must refresh the queue');
      } finally {
        a.c.h.unmount();
        assert.equal(a.c.clock.count, 0, 'Unmount must clear TaskCenter polling');
        assert.equal(a.c.documentListeners.size, 0);
      }
    });
  }
}

test('R0 TaskCenter control fixture executes active cancellation and clears polling', async () => {
  const a = await mountTasks('queued');
  try {
    const cancel = controls(a.c.h.current, /^Hủy$/)[0];
    assert.ok(cancel);
    await cancel.props.onClick(); await drain(a.c);
    assert.equal(a.requests.filter(request => request.url === `/jobs/${jid}/cancel`).length, 1);
  } finally {
    a.c.h.unmount();
    assert.equal(a.c.clock.count, 0);
    assert.equal(a.c.documentListeners.size, 0);
  }
});
