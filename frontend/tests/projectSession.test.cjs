const test = require('node:test');
const assert = require('node:assert/strict');
const { context, storage, drain } = require('./harness.cjs');

const id = 'a'.repeat(16);
const original = () => ({ id, revision: 1, name: 'Original', script: 'Old script',
  voice_id: 'b'.repeat(16), music_volume: 0.1, cues_stale: false, warnings: [] });

function server(project = original()) {
  const pending = [], gets = [];
  return {
    pending, gets, project,
    api(url, body, method) {
      if (method === 'PUT') return new Promise((resolve, reject) => pending.push({ body: structuredClone(body), resolve, reject }));
      gets.push(url); return Promise.resolve(structuredClone(this.project));
    },
    accept(index, changes = {}) {
      this.project = { ...pending[index].body, revision: this.project.revision + 1, ...changes };
      pending[index].resolve(structuredClone(this.project));
    },
  };
}

async function mount(s, local, session) {
  const c = context(s.api.bind(s), local, session);
  const { useProjectSession } = c.load('src/hooks/useProjectSession.js');
  c.h.mount(() => useProjectSession());
  await c.h.current.open(s.project.id); c.h.flush();
  return c;
}

const drafts = (c, name) => c.local.entries().map(([, v]) => JSON.parse(v)).filter(v => v.project?.name === name);

test('autosave waits 1300ms after the last edit while typing continuously', async () => {
  const s = server(), c = await mount(s);
  for (let index = 0; index < 3; index++) {
    c.h.current.edit(p => p.name = `Text ${index}`); c.h.flush();
    c.clock.advance(1000); await drain(c);
    assert.equal(s.pending.length, 0);
  }
  c.clock.advance(299); await drain(c); assert.equal(s.pending.length, 0);
  c.clock.advance(1); await drain(c); assert.equal(s.pending.length, 1);
  assert.equal(s.pending[0].body.name, 'Text 2');
  s.accept(0); await drain(c); assert.equal(c.h.current.saveStatus, 'saved');
});

test('manual flush retains newer edits and server normalization in the second PUT', async () => {
  const s = server(), c = await mount(s);
  c.h.current.edit(p => p.script = 'New script'); c.h.flush();
  const saving = c.h.current.save();
  c.h.current.edit(p => p.music_volume = 0.2); c.h.flush();
  s.accept(0, { voice_id: '', cues_stale: true, warnings: ['Review cues'] }); await drain(c);
  assert.equal(s.pending.length, 2);
  assert.equal(s.pending[1].body.voice_id, '');
  assert.equal(s.pending[1].body.music_volume, 0.2);
  assert.equal(s.pending[1].body.cues_stale, true);
  assert.deepEqual(s.pending[1].body.warnings, ['Review cues']);
  s.accept(1); await saving; c.h.flush(); assert.equal(c.h.current.dirty, false);
});

test('autosave leaves newer edits debounced; a manual flush joins the existing PUT', async () => {
  const s = server(), c = await mount(s);
  c.h.current.edit(p => p.script = 'New script'); c.h.flush();
  const autosave = c.h.current.save({ flush: false });
  c.clock.advance(200);
  c.h.current.edit(p => p.music_volume = 0.2); c.h.flush();
  s.accept(0, { voice_id: '' }); await autosave; await drain(c);
  assert.equal(s.pending.length, 1); assert.equal(c.h.current.dirty, true);
  c.clock.advance(1299); await drain(c); assert.equal(s.pending.length, 1);
  c.clock.advance(1); await drain(c); assert.equal(s.pending.length, 2);
  const manual = c.h.current.save();
  s.accept(1); await manual; await drain(c);
  assert.equal(s.pending.length, 2); assert.equal(c.h.current.p.voice_id, '');
});

test('a deliberate voice change after the snapshot survives server normalization', async () => {
  const s = server(), c = await mount(s);
  c.h.current.edit(p => p.script = 'New script'); c.h.flush();
  const saving = c.h.current.save();
  c.h.current.edit(p => p.voice_id = 'c'.repeat(16)); c.h.flush();
  s.accept(0, { voice_id: '' }); await drain(c);
  assert.equal(s.pending[1].body.voice_id, 'c'.repeat(16));
  s.accept(1); await saving;
});

test('two tabs retain independent drafts and restore the losing draft after a 409', async () => {
  const s = server(), local = storage(), a = await mount(s, local), b = await mount(s, local);
  a.h.current.edit(p => p.name = 'Tab A'); a.h.flush(); const saveA = a.h.current.save();
  b.h.current.edit(p => p.name = 'Tab B'); b.h.flush();
  s.accept(0); await saveA; await drain(a, b);
  assert.equal(drafts(b, 'Tab B').length, 1);
  const saveB = b.h.current.save();
  s.pending[1].reject(Object.assign(new Error('Conflict'), { status: 409 }));
  await assert.rejects(saveB, /Conflict/); await drain(b);
  assert.ok(b.h.current.conflict); assert.equal(drafts(b, 'Tab B').length, 1);
  b.h.unmount(); const reloaded = await mount(s, local, b.session);
  assert.equal(reloaded.h.current.p.name, 'Tab B'); assert.ok(reloaded.h.current.conflict);
  // Loading the server must fetch the current revision, even if the conflict's copy is older.
  s.project = { ...s.project, revision: 3, name: 'Latest server' };
  await reloaded.h.current.loadLatest(); reloaded.h.flush();
  assert.equal(reloaded.h.current.p.name, 'Latest server');
  reloaded.h.unmount(); const again = await mount(s, local, reloaded.session);
  assert.equal(again.h.current.conflict, null); assert.equal(again.h.current.p.name, 'Latest server');
});

test('duplicated tabs inheriting sessionStorage do not share a draft owner', async () => {
  const s = server(), local = storage(), a = await mount(s, local);
  const b = await mount(s, local, storage(a.session.entries()));
  a.h.current.edit(p => p.name = 'Tab A'); a.h.flush();
  b.h.current.edit(p => p.name = 'Tab B'); b.h.flush();
  const all = local.entries().map(([, value]) => JSON.parse(value));
  assert.equal(new Set(all.map(draft => draft.owner)).size, 2);
  b.h.current.setDirty(false); b.h.flush();
  assert.equal(drafts(a, 'Tab A').length, 1);
});

test('legacy drafts recover and only the exact saved draft is removed', async () => {
  const s = server(), local = storage();
  const old = { ...s.project, name: 'Legacy draft' };
  local.setItem(`clipforge-draft:${id}`, JSON.stringify({ project: old, baseRevision: 1, editedAt: '2026-10-01' }));
  const c = await mount(s, local);
  assert.equal(c.h.current.p.name, 'Legacy draft'); assert.equal(c.h.current.dirty, true);
  const saving = c.h.current.save(); s.accept(0); await saving; c.h.flush();
  assert.equal(local.length, 0);
});

test('discarding a migrated legacy conflict does not restore it on the next reload', async () => {
  const s = server(), local = storage();
  local.setItem(`clipforge-draft:${id}`, JSON.stringify({
    project: { ...s.project, revision: 0, name: 'Legacy conflict' }, baseRevision: 0,
  }));
  const c = await mount(s, local);
  assert.ok(c.h.current.conflict);
  await c.h.current.loadLatest(); c.h.flush(); c.h.unmount();
  const again = await mount(s, local, c.session);
  assert.equal(again.h.current.conflict, null); assert.equal(again.h.current.p.name, 'Original');
});

test('rebase matches nested entities by ID when clips reorder during a save', () => {
  const c = context(() => {}), { rebaseChanges } = c.load('src/hooks/projectChanges.js');
  const before = { revision: 1, clips: [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }] };
  const after = { ...before, clips: [{ id: 'b', title: 'New B' }, before.clips[0]] };
  const saved = { revision: 2, clips: before.clips.map(clip => ({ ...clip, locked: false })) };
  assert.deepEqual(rebaseChanges(before, after, saved), { revision: 2, clips: [
    { id: 'b', title: 'New B', locked: false }, { id: 'a', title: 'A', locked: false },
  ] });
});
