const assert = require('node:assert/strict');
const test = require('node:test');
const { context, drain, nodes } = require('./harness.cjs');

function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  return (node.props?.children || []).flat(Infinity).map(text).join(' ');
}
const buttons = (tree, label) => nodes(tree, node => typeof node.props?.onClick === 'function' && label.test(text(node)));

for (const [capability, pending, label] of [
  ['snapshot', false, 'Bắt đầu tác vụ đã lưu'],
  ['checkpointed', false, 'Tiếp tục từ checkpoint'],
  ['checkpointed', true, 'Hoàn tất lưu kết quả'],
]) {
  test(`R2 TaskCenter ${label} uses the saved job and current state version`, async () => {
    const jid = 'b'.repeat(16), pid = 'a'.repeat(16), requests = [];
    const job = { id: jid, pid, kind: 'render', status: 'interrupted', state_version: 7,
      attempt: 1, resume_capability: capability, can_resume: true, bookkeeping_pending: pending,
      steps: [], provider_calls: [], error_history: [], created_at: '2026-10-03T00:00:00Z' };
    const api = async (url, body) => {
      requests.push({ url, body });
      if (url === '/jobs/queue') return { jobs: [structuredClone(job)], running: 0, queued: job.status === 'queued' ? 1 : 0 };
      if (url === '/projects') return [{ id: pid, name: 'R2 fixture' }];
      if (url === `/jobs/${jid}`) return structuredClone(job);
      if (url === `/jobs/${jid}/resume`) {
        job.status = pending ? 'done' : 'queued'; job.can_resume = false;
        return structuredClone(job);
      }
      throw new Error(`Unexpected R2 API ${url}`);
    };
    const c = context(api), { TaskCenter } = c.load('src/components/TaskCenter.jsx');
    c.h.mount(() => TaskCenter({ onClose() {}, onOpenSettings() {}, onOpenStorage() {} }));
    try {
      await drain(c);
      await buttons(c.h.current, /^Chi tiết$/)[0].props.onClick(); await drain(c);
      const action = buttons(c.h.current, new RegExp(`^${label}$`))[0];
      assert.ok(action);
      await action.props.onClick(); await drain(c);
      assert.deepEqual(requests.filter(r => r.url.endsWith('/resume')), [{ url: `/jobs/${jid}/resume`, body: { state_version: 7 } }]);
      assert.ok(requests.some(r => r.url === '/jobs/queue'));
    } finally { c.h.unmount(); }
    assert.equal(c.clock.count, 0);
  });
}

test('R2 assistant without saved prompt offers no start or resume action', async () => {
  const jid = 'c'.repeat(16);
  const job = { id: jid, pid: 'a'.repeat(16), kind: 'assistant', status: 'interrupted',
    resume_capability: 'restart_only', can_resume: false, steps: [], provider_calls: [], error_history: [] };
  const api = async url => url === '/jobs/queue' ? { jobs: [job] } : url === '/projects' ? [] : structuredClone(job);
  const c = context(api), { TaskCenter } = c.load('src/components/TaskCenter.jsx');
  c.h.mount(() => TaskCenter({ onClose() {}, onOpenSettings() {}, onOpenStorage() {} }));
  try {
    await drain(c); await buttons(c.h.current, /^Chi tiết$/)[0].props.onClick(); await drain(c);
    assert.equal(buttons(c.h.current, /Bắt đầu tác vụ đã lưu|Tiếp tục từ checkpoint/).length, 0);
  } finally { c.h.unmount(); }
});
