import { clone } from '../api/client.js';

const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

// Apply only edits made after the request snapshot to the normalized server
// response. Unchanged local fields must keep the value returned by the server.
export function rebaseChanges(before, after, saved) {
  if (JSON.stringify(before) === JSON.stringify(after)) return clone(saved);
  if (Array.isArray(before) && Array.isArray(after) && Array.isArray(saved)) {
    if ([...before, ...after, ...saved].every((item) => object(item) && item.id)) {
      const original = new Map(before.map((item) => [item.id, item]));
      const normalized = new Map(saved.map((item) => [item.id, item]));
      return after.map((item) =>
        original.has(item.id) && normalized.has(item.id)
          ? rebaseChanges(original.get(item.id), item, normalized.get(item.id))
          : clone(item),
      );
    }
    if (before.length === after.length && saved.length === before.length)
      return after.map((item, index) => rebaseChanges(before[index], item, saved[index]));
    return clone(after);
  }
  if (object(before) && object(after) && object(saved)) {
    const result = clone(saved);
    for (const field of new Set([...Object.keys(before), ...Object.keys(after)])) {
      if (JSON.stringify(before[field]) === JSON.stringify(after[field])) continue;
      if (!Object.hasOwn(after, field)) delete result[field];
      else result[field] = rebaseChanges(before[field], after[field], saved[field]);
    }
    return result;
  }
  return clone(after);
}
