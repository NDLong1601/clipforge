const assert = require('node:assert/strict');
const test = require('node:test');
const { context, nodes } = require('./harness.cjs');

test('product fields update immediately when sources and scripts change', () => {
  const c = context(() => Promise.resolve([]));
  const { layerText } = c.load('src/templateContent.js');
  const p = { name: 'Dự án', script: 'Mô tả mới', assets: [{ id: 'a', role: 'source', name: 'Bình_nước.mp4' }] };
  const clip = { title: 'Cảnh 1', caption: '' };
  assert.equal(layerText(p, { text: 'TÊN SẢN PHẨM' }, clip), 'Bình nước');
  assert.equal(layerText(p, { text: 'MÔ TẢ SẢN PHẨM' }, clip), 'Mô tả mới');
  p.assets.push({ id: 'b', role: 'source', name: 'Túi_mới.mp4' });
  assert.equal(layerText(p, { text: '{product_name}: {product_description}' }, clip), 'Túi mới: Mô tả mới');
  p.product_name = 'Tên tùy chỉnh';
  assert.equal(layerText(p, { text: 'Sample', content: 'product_name' }, clip), 'Tên tùy chỉnh');
  assert.equal(layerText(p, { text: 'Thương hiệu cố định', content: 'static' }, clip), 'Thương hiệu cố định');
});

test('sound panel inserts a manual cue at the selected cursor and removes it by ID', () => {
  const c = context(() => Promise.resolve([]));
  const { SoundEffectsPanel } = c.load('src/components/SoundEffectsPanel.jsx');
  const p = { assets: [], sound_effects: [], auto_sound_effects: true, sound_effect_volume: .35 };
  const props = { p, locked: false, cursor: 3.25, duration: 8, edit: fn => fn(p) };
  c.h.mount(() => SoundEffectsPanel(props));
  nodes(c.h.current, node => node.type?.name === 'Button' && node.props.children.includes('Thêm hiệu ứng'))[0].props.onClick();
  assert.equal(p.sound_effects.length, 1);
  assert.equal(p.sound_effects[0].time, 3.25);
  assert.equal(p.sound_effects[0].origin, 'manual');
  assert.equal(p.sound_effects[0].effect, 'whoosh');
  c.h.mount(() => SoundEffectsPanel(props));
  nodes(c.h.current, node => node.type?.name === 'Button' && node.props.children.includes('Xóa'))[0].props.onClick();
  assert.equal(p.sound_effects.length, 0);
});
