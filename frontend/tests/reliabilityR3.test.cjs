const assert = require('node:assert/strict');
const test = require('node:test');
const { context, drain, nodes } = require('./harness.cjs');

function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  return (node.props?.children || []).flat(Infinity).map(text).join(' ');
}
const buttons = (tree, label) => nodes(tree, node => typeof node.props?.onClick === 'function' && label.test(text(node)));

for (const code of ['REVISION_CONFLICT', 'CONFIG_CHANGED']) {
  test(`R3 TaskCenter ${code} creates a new task from the current project`, async () => {
    const jid = 'b'.repeat(16), requests = [];
    const job = { id: jid, pid: 'a'.repeat(16), kind: 'plan', status: 'blocked', state_version: 9,
      can_restart_current: true, can_resume: false, blocked_reason: { code, message: 'Inputs changed' },
      steps: [], provider_calls: [], error_history: [] };
    const api = async (url, body) => {
      requests.push({ url, body });
      if (url === '/jobs/queue') return { jobs: [job], running: 0, queued: 0 };
      if (url === '/projects') return [];
      if (url === `/jobs/${jid}/restart-current`) return { id: 'c'.repeat(16), parent_job_id: jid, status: 'queued' };
      if (url === `/jobs/${jid}`) return job;
      throw new Error(`Unexpected R3 API ${url}`);
    };
    const c = context(api), { TaskCenter } = c.load('src/components/TaskCenter.jsx');
    c.h.mount(() => TaskCenter({ onClose() {}, onOpenSettings() {}, onOpenStorage() {} }));
    try {
      await drain(c);
      await buttons(c.h.current, /^Chi tiết$/)[0].props.onClick(); await drain(c);
      await buttons(c.h.current, /^Tạo tác vụ từ bản hiện tại$/)[0].props.onClick(); await drain(c);
      assert.deepEqual(requests.filter(r => r.url.endsWith('/restart-current')), [{ url: `/jobs/${jid}/restart-current`, body: { state_version: 9 } }]);
      assert.equal(requests.filter(r => r.url.endsWith('/resume')).length, 0);
    } finally { c.h.unmount(); }
    assert.equal(c.clock.count, 0);
  });
}
