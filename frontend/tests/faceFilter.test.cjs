const assert = require('node:assert/strict');
const test = require('node:test');
const { context, nodes } = require('./harness.cjs');

test('copyright checkbox saves the preference before filtering, and keeps it when re-opened', () => {
  const c = context(() => Promise.resolve({}));
  const { MediaPanel } = c.load('src/components/EditorPanels.jsx');
  const p = { assets: [], avoid_faces: false };
  const calls = [];
  const props = { p, locked: false, useAI: false, edit: fn => { fn(p); calls.push('saved'); },
    run: name => calls.push([name, p.avoid_faces]) };
  c.h.mount(() => MediaPanel(props));
  const checkbox = () => nodes(c.h.current, n => n.type === 'input' &&
    n.props.type === 'checkbox' && n.props.disabled !== undefined)[0];
  checkbox().props.onChange({ target: { checked: true } });
  assert.deepEqual(calls, ['saved', ['filter-faces', true]]);
  c.h.mount(() => MediaPanel(props));
  assert.equal(checkbox().props.checked, true);
  const retry = nodes(c.h.current, n => n.type?.name === 'Button' &&
    n.props.children.includes('Lọc lại cảnh có mặt'))[0];
  retry.props.onClick();
  assert.deepEqual(calls.at(-1), ['filter-faces', true]);
  checkbox().props.onChange({ target: { checked: false } });
  assert.equal(p.avoid_faces, false);
  assert.equal(calls.at(-1), 'saved');
  c.h.mount(() => MediaPanel({ ...props, locked: true }));
  assert.equal(checkbox().props.disabled, true);
});
