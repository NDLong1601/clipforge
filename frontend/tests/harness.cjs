const fs = require('node:fs');
const path = require('node:path');
const { webcrypto } = require('node:crypto');
const esbuild = require('esbuild');

function storage(initial = []) {
  const values = new Map(initial);
  return {
    get length() { return values.size; },
    key: index => [...values.keys()][index] ?? null,
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    entries: () => [...values.entries()],
  };
}

function harness() {
  let index = 0, cells = [], effects = [], rerender = false, output, renderFn;
  const hooks = {
    useState(initial) {
      const i = index++;
      if (!(i in cells)) cells[i] = typeof initial === 'function' ? initial() : initial;
      return [cells[i], value => {
        const next = typeof value === 'function' ? value(cells[i]) : value;
        if (!Object.is(next, cells[i])) { cells[i] = next; rerender = true; }
      }];
    },
    useRef(initial) {
      const i = index++;
      if (!(i in cells)) cells[i] = { current: initial };
      return cells[i];
    },
    useCallback(fn, deps) {
      const i = index++;
      const old = cells[i];
      if (!old || deps.some((v, j) => !Object.is(v, old.deps[j]))) cells[i] = { fn, deps };
      return cells[i].fn;
    },
    useMemo(fn, deps) {
      const i = index++;
      const old = cells[i];
      if (!old || deps.some((v, j) => !Object.is(v, old.deps[j]))) cells[i] = { value: fn(), deps };
      return cells[i].value;
    },
    useEffect(fn, deps) {
      const i = index++;
      const old = cells[i];
      if (!old || deps.some((v, j) => !Object.is(v, old.deps[j]))) {
        effects.push(() => { old?.cleanup?.(); cells[i] = { deps, cleanup: fn() }; });
      }
    },
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
  };
  function flush() {
    while (rerender) {
      rerender = false; index = 0; output = renderFn();
      const run = effects; effects = []; run.forEach(fn => fn());
    }
    return output;
  }
  return {
    hooks, mount(fn) { renderFn = fn; rerender = true; return flush(); }, flush,
    get current() { return output; },
    unmount() { cells.forEach(cell => cell?.cleanup?.()); },
  };
}

function context(api, local = storage(), session = storage()) {
  const h = harness(), modules = new Map(), timers = new Map(), listeners = new Map();
  let now = Date.UTC(2026, 9, 1), timerId = 0;
  const clock = {
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    advance(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const [id, timer] = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0] || [];
        if (!timer) break;
        now = timer.at; timers.delete(id); timer.fn(); h.flush();
      }
      now = end;
    },
    get count() { return timers.size; },
  };
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const window = {
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: name => listeners.delete(name),
  };
  function load(file) {
    const absolute = path.resolve(__dirname, '..', file);
    if (modules.has(absolute)) return modules.get(absolute).exports;
    const js = esbuild.transformSync(fs.readFileSync(absolute, 'utf8'), {
      loader: absolute.endsWith('.jsx') ? 'jsx' : 'js', format: 'cjs', jsx: 'transform',
    }).code;
    const module = { exports: {} }; modules.set(absolute, module);
    const req = name => {
      if (name === 'react') return h.hooks;
      if (name === 'lucide-react') return new Proxy({}, { get: (_, key) => key });
      if (name.endsWith('/api/client.js')) return {
        api, clone: structuredClone, aid: (p, id) => p.assets.find(a => a.id === id),
        fmt: String, thumbUrl: (_, name) => `thumb:${name}`,
        mediaUrl: (_, id) => `media:${id}`, exportUrl: () => 'export:',
        roleNames: { source: 'Tư liệu nguồn', voice: 'Giọng đọc', music: 'Nhạc nền',
          overlay: 'Lớp ảnh', reference: 'Video mẫu' },
      };
      return load(path.relative(path.resolve(__dirname, '..'), path.resolve(path.dirname(absolute), name)));
    };
    new Function('require', 'module', 'exports', 'React', 'localStorage', 'sessionStorage',
      'setTimeout', 'clearTimeout', 'Date', 'window', 'crypto', 'ResizeObserver',
      'requestAnimationFrame', 'cancelAnimationFrame', js)(
      req, module, module.exports, h.hooks, local, session,
      clock.setTimeout, clock.clearTimeout, ClockDate, window, webcrypto,
      class { observe() {} disconnect() {} },
      fn => clock.setTimeout(() => fn(now), 16), clock.clearTimeout,
    );
    return module.exports;
  }
  return { h, local, session, clock, listeners, load };
}

function nodes(node, predicate, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (predicate(node)) out.push(node);
  for (const child of node.props?.children?.flat(Infinity) || []) nodes(child, predicate, out);
  return out;
}

async function drain(...contexts) {
  for (let i = 0; i < 12; i++) { await Promise.resolve(); contexts.forEach(c => c.h.flush()); }
}

module.exports = { context, storage, nodes, drain };
