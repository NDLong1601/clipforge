const test = require('node:test');
const assert = require('node:assert/strict');
const { context, nodes } = require('./harness.cjs');

function drag(side, coordinates) {
  const c = context(() => Promise.resolve({ peaks: [] }));
  const { Timeline } = c.load('src/components/Timeline.jsx');
  let committed, commits = 0;
  const p = { id: 'a'.repeat(16), target_duration: 5, assets: [
    { id: 'source', name: 'Source', scenes: [{ id: 'scene', start: 0, end: 20 }] },
  ], clips: [{ id: 'clip', asset_id: 'source', scene_id: 'scene', source_start: 5,
    duration: 3, speed: 2 }], cues: [] };
  c.h.mount(() => Timeline({ p, selected: 0, cursor: 0, duration: 3,
    edit: fn => { committed = structuredClone(p); fn(committed); commits++; },
    setCursor() {}, onSelect() {}, setPlay() {},
  }));
  const handle = nodes(c.h.current, n => n.props?.className === 'trim-handle ' + side)[0];
  handle.props.onPointerDown({ clientX: 100, pointerId: 1, preventDefault() {}, stopPropagation() {},
    currentTarget: { setPointerCapture() {} } }); c.h.flush();
  for (const clientX of coordinates) { c.listeners.get('pointermove')({ clientX }); c.h.flush(); }
  assert.equal(commits, 0);
  c.listeners.get('pointerup')(); c.h.flush(); assert.equal(commits, 1);
  return committed.clips[0];
}

test('right trim stays at the same duration across repeated pointer events', () => {
  const clip = drag('right', [164, 164, 164]);
  assert.equal(clip.duration, 4); assert.equal(clip.source_start, 5);
});

test('left trim preserves source time at speed 2 across repeated pointer events', () => {
  const clip = drag('left', [132, 132, 132]);
  assert.equal(clip.duration, 2.5); assert.equal(clip.source_start, 6);
});

test('moving either edge back to its origin restores the original clip', () => {
  for (const side of ['left', 'right']) {
    const clip = drag(side, [132, 164, 100]);
    assert.equal(clip.duration, 3); assert.equal(clip.source_start, 5);
  }
});
