const test = require('node:test');
const assert = require('node:assert/strict');
const { context, drain } = require('./harness.cjs');
function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  return (node.props?.children || []).flat(Infinity).map(text).join(' ');
}
for (const estimated of [true, false]) {
  test(`R5 running step is visible and ETA requires evidence (${estimated})`, async () => {
    const job = { id: 'a'.repeat(16), pid: 'b'.repeat(16), kind: 'render', status: 'running', progress: 50,
      current_step: 'encode-video', encode_progress: { eta_estimated: estimated, eta_seconds: estimated ? 12 : null } };
    const c = context(async url => url === '/jobs/queue' ? { jobs: [job] } : []);
    const { TaskCenter } = c.load('src/components/TaskCenter.jsx');
    c.h.mount(() => TaskCenter({ onClose() {} }));
    try {
      await drain(c);
      assert.match(text(c.h.current), /Bước đang chạy:\s+encode-video/);
      assert.equal(text(c.h.current).includes('Thời gian còn lại của bước (ước tính)'), estimated);
    } finally { c.h.unmount(); }
  });
}
