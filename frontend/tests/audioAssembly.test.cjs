const assert = require('node:assert/strict');
const test = require('node:test');
const { context, nodes } = require('./harness.cjs');

function clips() {
  return ['one', 'two', 'three'].map((id, index) => ({ id, asset_id: id, duration: 2,
    transition: index ? 'crossfade' : 'cut', transition_duration: .3,
    source_start: 1, speed: 1, fit: 'cover', crop_x: .5, crop_y: .5 }));
}

test('preview retains the preloaded incoming video across a boundary', () => {
  const c = context(() => Promise.resolve({}));
  const { residentScenes } = c.load('src/previewMedia.js');
  const before = residentScenes(clips(), 1.69, 0);
  const overlap = residentScenes(clips(), 1.71, 0);
  const after = residentScenes(clips(), 2.01, 0);
  assert.deepEqual(before.resident.map(({ clip }) => clip.id), ['one', 'two']);
  assert.deepEqual(overlap.active.map(({ clip }) => clip.id), ['one', 'two']);
  assert.deepEqual(after.resident.map(({ clip }) => clip.id), ['two', 'three']);
});

test('source masks respect crop, contain and an explicit empty override', () => {
  const c = context(() => Promise.resolve({}));
  const { maskGeometry, sourceMasks } = c.load('src/previewMedia.js');
  const region = { x: .1, y: .6, w: .8, h: .2 };
  const asset = { width: 1920, height: 1080, scenes: [{ id: 'scene', text_regions: [region] }] };
  const clip = { ...clips()[0], scene_id: 'scene' };
  assert.equal(sourceMasks({ source_text_mode: 'cover' }, clip, asset).length, 1);
  assert.deepEqual(sourceMasks({ source_text_mode: 'cover' }, { ...clip, text_regions_override: true }, asset), []);
  assert.deepEqual(sourceMasks({ source_text_mode: 'cover' }, { ...clip, text_mode: 'off' }, asset), []);
  const contained = maskGeometry(region, asset, { ...clip, fit: 'contain' }, 1080, 1920);
  assert.equal(Number.parseFloat(contained.left), 10);
  assert.equal(Number.parseFloat(contained.width), 80);
  assert.ok(Number.parseFloat(contained.top) > 50);
  const covered = maskGeometry(region, asset, clip, 1080, 1920);
  assert.ok(Number.parseFloat(covered.left) < 0);
  assert.equal(Number.parseFloat(covered.top), 60);
});

test('audio panel provides automatic assembly and manual retry', () => {
  const c = context(() => Promise.resolve({}));
  const { AudioPanel } = c.load('src/components/EditorPanels.jsx');
  const calls = [];
  const p = { auto_audio_assembly: true, smooth_transitions: true, script: '', voice_id: 'voice',
    music_id: '', voice_volume: 1, music_volume: .15, source_volume: 0, assets: [] };
  c.h.mount(() => AudioPanel({ p, edit() {}, locked: false, run: (...args) => calls.push(args), upload() {} }));
  const button = nodes(c.h.current, node => node.type?.name === 'Button' &&
    node.props.children.includes('Phân tích audio & ghép cảnh'))[0];
  button.props.onClick();
  assert.deepEqual(calls, [['assemble-audio']]);
  const toggle = nodes(c.h.current, node => node.type === 'input' && node.props.type === 'checkbox')[0];
  assert.equal(toggle.props.checked, true);
});
