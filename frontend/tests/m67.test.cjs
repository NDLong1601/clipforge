const assert = require('node:assert/strict');
const test = require('node:test');
const { context, nodes, drain } = require('./harness.cjs');

function project() {
  return {
    id: 'project', revision: 4, name: 'Fixture', script: 'Original', aspect: '16:9', mode: 'remix',
    source_volume: .8, music_volume: .15, voice_volume: 1, cues: [],
    assets: ['red', 'blue'].map(id => ({ id, name: id, media: 'image', scenes: [], thumbnail: `${id}.jpg` })),
    clips: ['red', 'blue'].map((id, index) => ({
      id: `clip-${id}`, asset_id: id, source_start: 0, speed: 1, fit: 'cover', crop_x: .5, crop_y: .5,
      duration: 2, transition: index ? 'crossfade' : 'cut', transition_duration: .5,
      title: id, caption: index ? 'Second' : 'First',
      layers: [{ id: `layer-${id}`, kind: 'rect', x: .1, y: .1, w: .1, h: .1,
        size: 30, color: '#00ff00', opacity: 1, animation: 'none' }],
    })),
    template: { background: '#000000', viewport: { x: .1, y: .1, w: .8, h: .8 }, layers: [],
      caption: { enabled: true, bottom: .18, safe_area: 'standard', font_family: 'Arial',
        font_size: 52, words_per_line: 6, color: '#ffffff' } },
  };
}

function previewProps(p, cursor) {
  return { p, selected: 1, preview: null, play: false, cursor, duration: 3.5,
    setCursor() {}, setPlay() {}, onSelect() {} };
}

test('crossfade composites complete opaque scenes, including layers and viewport background', () => {
  const c = context(() => Promise.resolve({}));
  const { Preview } = c.load('src/components/Preview.jsx');
  const p = project();
  c.h.mount(() => Preview(previewProps(p, 1.75)));
  const scenes = nodes(c.h.current, n => n.props?.className === 'canvas-scene');
  assert.deepEqual(scenes.map(n => n.props.style.opacity), [1, .5]);
  for (const scene of scenes) {
    assert.equal(scene.props.style.background, '#000000');
    assert.equal(nodes(scene, n => n.props?.className === 'canvas-layer').length, 1);
    assert.equal(nodes(scene, n => n.props?.className === 'canvas-layer')[0].props.style.opacity, 1);
    assert.equal(nodes(scene, n => n.props?.className === 'video-viewport')[0].props.style.opacity, undefined);
  }
  const [outgoing, incoming] = scenes.map(n => n.props.style.opacity);
  assert.deepEqual([outgoing * (1 - incoming), 0, incoming], [.5, 0, .5]);
  c.h.unmount();
});

test('source audio crossfade gain remains separate from visual scene opacity', () => {
  const c = context(() => Promise.resolve({}));
  const { Preview } = c.load('src/components/Preview.jsx');
  const p = project();
  p.assets.forEach(a => { a.media = 'video'; a.duration = 5; });
  let cursor = 1.7;
  c.h.mount(() => Preview(previewProps(p, cursor)));
  const videos = nodes(c.h.current, n => n.type === 'video');
  const elements = videos.map(() => ({ duration: 5, readyState: 4, currentTime: 0,
    pause() {}, play: () => Promise.resolve() }));
  videos.forEach((node, index) => node.props.ref(elements[index]));
  cursor = 1.75;
  c.h.mount(() => Preview(previewProps(p, cursor)));
  assert.deepEqual(elements.map(video => video.volume), [.4, .4]);
  assert.deepEqual(nodes(c.h.current, n => n.props?.className === 'canvas-scene')
    .map(n => n.props.style.opacity), [1, .5]);
  c.h.unmount();
});

test('caption handoff at overlap shows the incoming caption and leaves no stacked interval', () => {
  const c = context(() => Promise.resolve({}));
  const { Preview } = c.load('src/components/Preview.jsx');
  const { timelineCaptionGroups } = c.load('src/timelineMath.js');
  const p = project();
  const groups = timelineCaptionGroups(p.clips, [], 6);
  assert.deepEqual(groups.map(({ start, end, text }) => ({ start, end, text })), [
    { start: 0, end: 1.5, text: 'First' }, { start: 1.5, end: 3.5, text: 'Second' },
  ]);
  c.h.mount(() => Preview(previewProps(p, 1.5)));
  const caption = nodes(c.h.current, n => n.props?.className === 'canvas-caption');
  assert.equal(caption.length, 1);
  assert.deepEqual(caption[0].props.children, ['Second']);
  c.h.unmount();
});

test('caption handoff to a blank clip restores voice cue and preserves word-weight grouping', () => {
  const c = context(() => Promise.resolve({}));
  const { timelineCaptionGroups } = c.load('src/timelineMath.js');
  const p = project();
  p.clips[1].caption = '';
  const groups = timelineCaptionGroups(p.clips, [{ start: 0, end: 3.5, text: 'A longer word.' }], 2);
  assert.equal(groups[0].text, 'First');
  assert.equal(groups[0].end, 1.5);
  assert.equal(groups[1].start, 1.5);
  assert.equal(groups[1].text, 'A longer');
  assert.equal(groups[2].text, 'word.');
  assert.ok(Math.abs(groups[1].end - (1.5 + 2 * 7 / 12)) < 1e-9);
});

function button(tree, label) {
  return nodes(tree, n => n.type?.name === 'Button' &&
    n.props.children.flat(Infinity).includes(label))[0];
}

test('AI review displays the normalized candidate and warns before voice removal', async () => {
  const c = context(() => Promise.resolve({}));
  const { AssistantPanel } = c.load('src/components/EditorPanels.jsx');
  const p = project(); p.voice_id = 'voice-original';
  const candidate = structuredClone(p);
  candidate.script = 'New'; candidate.voice_id = ''; candidate.cues_stale = true;
  const proposal = { proposal_id: 'proposal', revision: p.revision, message: 'Fixture',
    script: 'New', clip_changes: [] };
  const requests = [];
  const props = { p, locked: false, dirty: false, proposal, run() {}, onSettings() {},
    onPreview: async request => { requests.push(request); return { project: candidate,
      preflight: { ok: true, issues: [] }, can_apply: true, preview_token: 'token-12345678901234567890' }; },
    onApply: request => requests.push(request) };
  c.h.mount(() => AssistantPanel(props));
  await button(c.h.current, 'Xem trước & preflight').props.onClick();
  await drain(c);
  const candidateNode = nodes(c.h.current, n => n.type?.name === 'AIProposalPreview')[0];
  assert.equal(candidateNode.props.project.voice_id, '');
  assert.equal(p.voice_id, 'voice-original');
  assert.ok(nodes(c.h.current, n => n.type === 'p')
    .some(n => n.props.children.flat(Infinity).some(text => typeof text === 'string' && text.includes('gỡ giọng đọc'))));
  button(c.h.current, 'Áp dụng mục đã chọn').props.onClick();
  assert.equal(requests[1].preview_token, 'token-12345678901234567890');
  assert.equal(requests[1].script, 'New');
  c.h.unmount();
});

test('changing selected AI items invalidates its preview and applies only chosen clips', async () => {
  const c = context(() => Promise.resolve({}));
  const { AssistantPanel } = c.load('src/components/EditorPanels.jsx');
  const p = project();
  const proposal = { proposal_id: 'proposal', revision: p.revision, message: 'Fixture',
    clip_changes: p.clips.map(clip => ({ id: clip.id, title: 'New' })) };
  const requests = [];
  const props = { p, proposal, locked: false, dirty: false, run() {}, onSettings() {}, onApply() {},
    onPreview: async request => { requests.push(request); return { project: p, preflight: { issues: [] },
      can_apply: true, preview_token: 'token-12345678901234567890' }; } };
  c.h.mount(() => AssistantPanel(props));
  const checkboxes = nodes(c.h.current, n => n.type === 'input' && n.props.type === 'checkbox');
  checkboxes[1].props.onChange({ target: { checked: false } }); c.h.flush();
  await button(c.h.current, 'Xem trước & preflight').props.onClick(); await drain(c);
  assert.deepEqual(requests[0].clip_changes.map(change => change.id), ['clip-red']);
  nodes(c.h.current, n => n.type === 'input' && n.props.type === 'checkbox')[1]
    .props.onChange({ target: { checked: true } }); c.h.flush();
  assert.equal(button(c.h.current, 'Áp dụng mục đã chọn'), undefined);
  c.h.unmount();
});
