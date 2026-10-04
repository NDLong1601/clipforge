const test = require('node:test');
const assert = require('node:assert/strict');
const { context, drain, nodes } = require('./harness.cjs');
const pid = 'a'.repeat(16), other = 'c'.repeat(16), jid = 'b'.repeat(16);
const project = (id = pid) => ({ id, revision: 1, name: 'Original', script: 'Old', exports: [], assets: [] });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  return (node.props?.children || []).flat(Infinity).map(text).join(' ');
}
const buttons = (tree, label) => nodes(tree, n => typeof n.props?.onClick === 'function' && label.test(text(n)));

async function session(api) {
  const c = context(api), { useProjectSession } = c.load('src/hooks/useProjectSession.js');
  c.h.mount(() => useProjectSession()); await c.h.current.open(pid); await drain(c); return c;
}

test('R4 job metadata merges into an unsaved draft, advances revision and retains local recovery', async () => {
  let server = project(), puts = [];
  const c = await session(async (url, body, method) => {
    if (method === 'PUT') { puts.push(body); server = { ...body, revision: server.revision + 1 }; }
    return structuredClone(server);
  });
  try {
    c.h.current.edit(p => p.script = 'Still typing'); await drain(c);
    server = { ...server, revision: 2, job_commit_tokens: [jid], exports: [{ id: jid }] };
    await c.h.current.syncJobResult(pid); await drain(c);
    assert.equal(c.h.current.p.script, 'Still typing'); assert.equal(c.h.current.p.revision, 2);
    assert.equal(c.h.current.p.exports[0].id, jid); assert.equal(c.h.current.dirty, true);
    assert.ok(c.local.entries().some(([, v]) => JSON.parse(v).project?.script === 'Still typing'));
    await c.h.current.save(); await drain(c);
    assert.equal(puts[0].revision, 2); assert.equal(server.script, 'Still typing'); assert.equal(server.exports[0].id, jid);
  } finally { c.h.unmount(); }
});

test('R4 unchanged job polling does not postpone draft autosave forever', async () => {
  let server = project(), puts = 0;
  const c = await session(async (url, body, method) => {
    if (method === 'PUT') { puts++; server = { ...body, revision: server.revision + 1 }; }
    return structuredClone(server);
  });
  try {
    c.h.current.edit(p => p.script = 'Draft'); await drain(c);
    server = { ...server, revision: 2, assets: [{ id: jid, name: 'Upload' }] };
    await c.h.current.syncJobResult(pid); await drain(c);
    c.clock.advance(1000); await c.h.current.syncJobResult(pid); await drain(c);
    c.clock.advance(300); await drain(c);
    assert.equal(puts, 1); assert.equal(server.script, 'Draft'); assert.equal(server.assets[0].id, jid);
  } finally { c.h.unmount(); }
});

test('R4 array rebase retains server uploads while editing an existing asset', () => {
  const c = context(() => {}), { rebaseChanges } = c.load('src/hooks/projectChanges.js');
  const base = { assets: [{ id: 'a', name: 'Original' }] };
  const edited = { assets: [{ id: 'a', name: 'Draft' }] };
  const server = { assets: [...base.assets, { id: 'b', name: 'New upload' }] };
  assert.deepEqual(rebaseChanges(base, edited, server), { assets: [{ id: 'a', name: 'Draft' }, server.assets[1]] });
});

test('R4 autosave 409 from a committed export rebases and saves the draft with that export', async () => {
  let server = project(), calls = 0;
  const c = await session(async (url, body, method) => {
    if (method === 'PUT') {
      calls++;
      if (body.revision !== server.revision) throw Object.assign(new Error('Conflict'), { status: 409 });
      server = { ...body, revision: server.revision + 1 };
    }
    return structuredClone(server);
  });
  try {
    c.h.current.edit(p => p.name = 'Draft'); await drain(c);
    server = { ...server, revision: 2, exports: [{ id: jid }], job_commit_tokens: [jid] };
    await c.h.current.save(); await drain(c);
    assert.equal(calls, 2); assert.equal(c.h.current.conflict, null);
    assert.equal(server.name, 'Draft'); assert.equal(server.exports[0].id, jid);
  } finally { c.h.unmount(); }
});

test('R4 conflicting job changes preserve the draft and stop autosave without overwriting either copy', async () => {
  let server = project();
  const c = await session(async () => structuredClone(server));
  try {
    c.h.current.edit(p => p.script = 'Local draft'); await drain(c);
    server = { ...server, revision: 2, script: 'Server job result' };
    assert.equal(await c.h.current.syncJobResult(pid), null); await drain(c);
    assert.equal(c.h.current.p.script, 'Local draft'); assert.equal(c.h.current.conflict.server.script, 'Server job result');
    assert.equal(await c.h.current.save(), null); assert.ok(c.local.length);
    assert.equal(c.clock.count, 0);
  } finally { c.h.unmount(); }
});

test('R4 a reordered entity with the same edited field causes a conflict by ID', async () => {
  let server = { ...project(), clips: [{ id: 'x', title: 'Old X' }, { id: 'y', title: 'Old Y' }] };
  const c = await session(async () => structuredClone(server));
  try {
    c.h.current.edit(p => p.clips[0].title = 'Draft X'); await drain(c);
    server = { ...server, revision: 2, clips: [server.clips[1], { id: 'x', title: 'Server X' }] };
    assert.equal(await c.h.current.syncJobResult(pid), null); await drain(c);
    assert.equal(c.h.current.p.clips[0].title, 'Draft X'); assert.ok(c.h.current.conflict);
  } finally { c.h.unmount(); }
});

test('R4 late project open A cannot replace B', async () => {
  const a = deferred(), b = deferred(), c = context(url => url.endsWith(pid) ? a.promise : b.promise);
  const { useProjectSession } = c.load('src/hooks/useProjectSession.js'); c.h.mount(() => useProjectSession());
  try {
    const openingA = c.h.current.open(pid), openingB = c.h.current.open(other);
    b.resolve(project(other)); await openingB; await drain(c);
    a.resolve(project()); assert.equal(await openingA, null); await drain(c);
    assert.equal(c.h.current.p.id, other);
  } finally { c.h.unmount(); }
});

test('R4 switching projects joins an existing autosave without creating a spurious conflict', async () => {
  const put = deferred(); let server = project(), puts = 0;
  const c = await session(async (url, body, method) => {
    if (method === 'PUT') { puts++; return put.promise; }
    return url.endsWith(other) ? project(other) : structuredClone(server);
  });
  try {
    c.h.current.edit(p => p.name = 'Saved before switch'); await drain(c);
    const save = c.h.current.save({ flush: false });
    const opening = c.h.current.open(other);
    server = { ...c.h.current.p, revision: 2 }; put.resolve(server);
    await save; await opening; await drain(c);
    assert.equal(c.h.current.p.id, other); assert.equal(c.h.current.conflict, null); assert.equal(puts, 1);
  } finally { c.h.unmount(); }
});

test('R4 late job sync A is ignored after switching away and back to A', async () => {
  const delayed = deferred(); let calls = 0;
  const c = await session(url => ++calls === 2 ? delayed.promise : Promise.resolve(project(url.endsWith(other) ? other : pid)));
  try {
    const sync = c.h.current.syncJobResult(pid);
    await c.h.current.open(other); await drain(c); await c.h.current.open(pid); await drain(c);
    delayed.resolve({ ...project(), revision: 9, name: 'Stale response' });
    assert.equal(await sync, null); assert.equal(c.h.current.p.name, 'Original');
  } finally { c.h.unmount(); }
});

function events(api, dirty = false) {
  const c = context(api), { useJobEvents } = c.load('src/hooks/useJobEvents.js');
  const applied = [], previews = [], assembled = [], errors = [], done = [];
  let options = { projectId: pid, jobs: [], dirty, revision: 1, dirtyRef: { current: dirty },
    setP: p => applied.push(p), onPreview: p => previews.push(p), onAutoAssemble: j => assembled.push(j),
    onError: e => errors.push(e), onDone: j => done.push(j), onProposal() {} };
  const render = () => c.h.mount(() => useJobEvents(options)); render();
  return { c, applied, previews, assembled, errors, done,
    async poll(job, changes = {}) { options = { ...options, ...changes, jobs: [job] }; render(); await drain(c); },
    options: () => options };
}
const job = (status = 'done', attempt = 2, kind = 'render') => ({ pid, id: jid, status, attempt, kind, result: { id: jid } });

test('R4 legacy missing attempt, blocked and resumed done have distinct terminal keys', async () => {
  const e = events(async () => ({ ...project(), exports: [{ id: jid }] }));
  try {
    await e.poll({ ...job('blocked'), attempt: undefined }); await e.poll(job()); await e.poll({ ...job(), state_version: 99 });
    assert.equal(e.errors.length, 1); assert.equal(e.done.length, 1); assert.equal(e.applied.length, 1);
  } finally { e.c.h.unmount(); }
});

test('R4 project-open baseline seeds legacy errors but leaves a resumed completion and upload follow-up actionable', async () => {
  let calls = 0; const e = events(async url => {
    calls++;
    return url.endsWith('assemble-audio') ? { id: other } : { ...project(), revision: 2, exports: [{ id: jid }] };
  });
  try {
    e.c.h.current([{ ...job('error'), attempt: undefined }], project());
    await e.poll({ ...job('error'), attempt: undefined }); assert.equal(e.errors.length, 0);
    await e.poll(job('done', 2)); assert.equal(e.done.length, 1);
    const upload = { ...job('done', 1, 'upload'), result: { auto_assemble: true, revision: 1 } };
    e.c.h.current([upload], project()); await e.poll(upload);
    assert.equal(e.assembled.length, 1); assert.equal(calls, 3);
  } finally { e.c.h.unmount(); }
});

test('R4 transient project fetch failure retains completion for reconnect', async () => {
  let calls = 0; const e = events(async () => { if (++calls === 1) throw new Error('Offline'); return { ...project(), exports: [{ id: jid }] }; });
  try {
    await e.poll(job()); assert.equal(e.done.length, 0);
    await e.poll(job()); await e.poll(job()); assert.equal(e.done.length, 1); assert.equal(calls, 2);
  } finally { e.c.h.unmount(); }
});

test('R4 a missing export keeps the event pending and never selects another export', async () => {
  let exported = false; const e = events(async () => ({ ...project(), exports: [{ id: exported ? jid : other }] }));
  try {
    await e.poll(job()); assert.equal(e.previews.length, 0); assert.equal(e.done.length, 0);
    exported = true; await e.poll(job()); assert.equal(e.previews[0].id, jid); assert.equal(e.done.length, 1);
  } finally { e.c.h.unmount(); }
});

test('R4 event fetch ignores a different project and an edit arriving during GET', async () => {
  const wait = deferred(), e = events(() => wait.promise);
  try {
    await e.poll(job()); e.options().dirtyRef.current = true;
    wait.resolve({ ...project(), exports: [{ id: jid }] }); await drain(e.c);
    assert.equal(e.applied.length, 0);
    await e.poll(job(), { projectId: other }); assert.equal(e.done.length, 0);
  } finally { e.c.h.unmount(); }
});

test('R4 upload completion retries a lost POST response with the same durable upload key exactly once', async () => {
  let posts = 0; const bodies = [], e = events(async (url, body) => {
    if (url.endsWith('/assemble-audio')) { bodies.push(body); if (++posts === 1) throw new Error('Response lost'); return { id: other }; }
    return project();
  });
  try {
    const upload = { ...job('done', 2, 'upload'), result: { auto_assemble: true } };
    await e.poll(upload); await e.poll(upload); await e.poll(upload);
    assert.deepEqual(bodies, [{ upload_job_id: jid }, { upload_job_id: jid }]);
    assert.equal(e.assembled.length, 1); assert.equal(e.done.length, 1);
  } finally { e.c.h.unmount(); }
});

test('R4 useJobs rejects an older success after a newer failure and clears requests on unmount', async () => {
  const requests = [], c = context((url, body, method, options) => { const d = deferred(); requests.push({ ...d, options }); return d.promise; });
  const { useJobs } = c.load('src/hooks/useJobs.js'); c.h.mount(() => useJobs(pid));
  try {
    const manual = c.h.current.refresh(); requests[1].reject(new Error('Offline'));
    await assert.rejects(manual); await drain(c);
    requests[0].resolve([job()]); await drain(c);
    assert.equal(c.h.current.connectionState, 'reconnecting'); assert.equal(c.h.current.jobs.length, 0);
    const next = c.h.current.refresh(); c.h.unmount(); assert.equal(requests[2].options.signal.aborted, true);
    requests[2].resolve([job()]); await next;
  } finally { c.h.unmount(); }
  assert.equal(c.clock.count, 0); assert.equal(c.documentListeners.size, 0);
});

test('R4 useJobs A → B → A ignores the first A response', async () => {
  const requests = [], c = context(url => { const d = deferred(); requests.push({ ...d, url }); return d.promise; });
  const { useJobs } = c.load('src/hooks/useJobs.js'); let selected = pid;
  const render = () => c.h.mount(() => useJobs(selected)); render();
  try {
    selected = other; render(); selected = pid; render();
    requests[2].resolve([{ ...job(), message: 'Current' }]); await drain(c);
    requests[0].resolve([{ ...job(), message: 'Old' }]); requests[1].resolve([]); await drain(c);
    assert.equal(c.h.current.jobs[0].message, 'Current'); assert.equal(c.h.current.connectionState, 'connected');
  } finally { c.h.unmount(); }
});

test('R4 TaskCenter stale refresh/detail/diagnostics cannot overwrite a newly selected job', async () => {
  const a = [], diag = deferred();
  const getJob = id => ({ ...job('blocked'), id, message: id, steps: [], provider_calls: [], error_history: [] });
  const c = context(url => {
    if (url === '/jobs/queue') return Promise.resolve({ jobs: [getJob(jid), getJob(other)] });
    if (url === '/projects') return Promise.resolve([]);
    if (url.endsWith('/diagnostics/preview')) return diag.promise;
    if (url.endsWith(jid)) { const d = deferred(); a.push(d); return d.promise; }
    if (url.endsWith(other)) return Promise.resolve(getJob(other));
    throw new Error(url);
  });
  const { TaskCenter } = c.load('src/components/TaskCenter.jsx'); c.h.mount(() => TaskCenter({ onClose() {} }));
  try {
    await drain(c); const opening = buttons(c.h.current, /^Chi tiết$/)[0].props.onClick(); await drain(c);
    a.forEach(d => d.resolve(getJob(jid))); await opening; await drain(c);
    const manifest = buttons(c.h.current, /^Xem nội dung gói$/)[0].props.onClick();
    c.clock.advance(2000); await drain(c);
    const old = a.at(-1); await buttons(c.h.current, /^Chi tiết$/)[1].props.onClick(); await drain(c);
    old.resolve({ ...getJob(jid), status: 'error', attempt: 9 });
    diag.resolve({ files: [{ name: 'STALE A' }], excludes: [] }); await manifest; await drain(c);
    assert.ok(text(c.h.current).includes('Bị chặn')); assert.ok(!text(c.h.current).includes('STALE A'));
    const facts = nodes(c.h.current, n => n.props?.className === 'task-detail-facts')[0];
    assert.ok(!text(facts).includes('9'));
  } finally { c.h.unmount(); }
  assert.equal(c.clock.count, 0);
});

test('R4 TaskCenter closing invalidates in-flight mutations before they can refresh or set errors', async () => {
  const pause = deferred(); let requests = 0, closed = 0;
  const c = context((url, body) => {
    if (url === '/jobs/queue' && body) return pause.promise;
    if (url === '/jobs/queue') { requests++; return Promise.resolve({ jobs: [], paused: false }); }
    return Promise.resolve([]);
  });
  const { TaskCenter } = c.load('src/components/TaskCenter.jsx'); c.h.mount(() => TaskCenter({ onClose() { closed++; } }));
  await drain(c); const pending = buttons(c.h.current, /Tạm dừng hàng đợi/)[0].props.onClick();
  buttons(c.h.current, /^Quay lại$/)[0].props.onClick(); c.h.unmount(); pause.resolve({}); await pending;
  assert.equal(closed, 1); assert.equal(requests, 1); assert.equal(c.clock.count, 0);
});

test('R4 TaskCenter old queue success cannot report connected after a newer refresh failed', async () => {
  const pending = [], c = context((url, body) => {
    if (url === '/jobs/queue' && !body) { const d = deferred(); pending.push(d); return d.promise; }
    return Promise.resolve(url === '/projects' ? [] : {});
  });
  const { TaskCenter } = c.load('src/components/TaskCenter.jsx'); c.h.mount(() => TaskCenter({ onClose() {} }));
  try {
    const mutation = buttons(c.h.current, /Tạm dừng hàng đợi/)[0].props.onClick(); await drain(c);
    pending[1].reject(new Error('Backend lost')); await mutation; await drain(c);
    pending[0].resolve({ jobs: [] }); await drain(c);
    assert.ok(!text(c.h.current).includes('Đã đồng bộ')); assert.ok(text(c.h.current).includes('Đang kết nối lại'));
  } finally { c.h.unmount(); }
});

test('R4 discard describes the data and reloads storage and queue immediately', async () => {
  const calls = [], c = context(async url => {
    calls.push(url); if (url === '/jobs/queue') return { jobs: [job('error')] };
    if (url === '/storage') return { total: 1024 ** 3 }; return [];
  });
  const { TaskCenter } = c.load('src/components/TaskCenter.jsx'); c.h.mount(() => TaskCenter({ onClose() {} }));
  try {
    await drain(c); assert.ok(text(c.h.current).includes('Bỏ bản xuất chưa lưu'));
    await buttons(c.h.current, /^Bỏ tác vụ và dữ liệu phục hồi$/)[0].props.onClick(); await drain(c);
    assert.equal(calls.filter(x => x === '/storage').length, 1);
    assert.equal(calls.filter(x => x === '/jobs/queue').length, 2);
    assert.ok(text(c.h.current).includes('1.0 GB'));
  } finally { c.h.unmount(); }
});
