const owner = crypto.randomUUID();
let previousOwner = null;
try {
  previousOwner = sessionStorage.getItem('clipforge-draft-owner');
  // A duplicated tab inherits sessionStorage. Give each document its own owner,
  // while retaining the previous owner to recover drafts after a reload.
  sessionStorage.setItem('clipforge-draft-owner', owner);
} catch {
  // Local drafts still work when sessionStorage is unavailable.
}

const prefix = (id) => `clipforge-draft:${id}`;
const key = (id) => `${prefix(id)}:${owner}`;

function read(key, id) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null');
    return value?.project?.id === id ? value : null;
  } catch {
    return null;
  }
}

export function readDraft(id) {
  const current = read(key(id), id);
  if (current) return current;
  // A known document with no draft has saved/discarded it. Do not pick up
  // another tab's unrelated draft when reloading that document.
  if (previousOwner) return read(`${prefix(id)}:${previousOwner}`, id) || read(prefix(id), id);
  const drafts = [read(prefix(id), id)];
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const storedKey = localStorage.key(index);
      if (storedKey?.startsWith(`${prefix(id)}:`)) drafts.push(read(storedKey, id));
    }
  } catch {
    return null;
  }
  // Recover an orphaned draft when opening a new tab after a closed/crashed tab.
  return (
    drafts
      .filter(Boolean)
      .sort((a, b) => String(b.editedAt || '').localeCompare(String(a.editedAt || '')))[0] || null
  );
}

export function saveDraft(project, operation) {
  if (!project) return;
  try {
    localStorage.setItem(
      key(project.id),
      JSON.stringify({
        owner,
        projectId: project.id,
        baseRevision: project.revision,
        editedAt: new Date().toISOString(),
        operationId: operation?.id || '',
        operationKey: operation?.key || '',
        project,
      }),
    );
    const legacy = read(prefix(project.id), project.id);
    if (legacy && JSON.stringify(legacy.project) === JSON.stringify(project))
      localStorage.removeItem(prefix(project.id));
  } catch {
    // The in-memory edit remains available and beforeunload still guards it.
  }
}

export function removeDraft(id) {
  try {
    localStorage.removeItem(key(id));
  } catch {
    // Storage can be disabled by browser policy.
  }
}

export function removeSavedDrafts(snapshot) {
  const serialized = JSON.stringify(snapshot);
  const matched = [];
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const storedKey = localStorage.key(index);
      if (storedKey !== prefix(snapshot.id) && !storedKey?.startsWith(`${prefix(snapshot.id)}:`))
        continue;
      const draft = read(storedKey, snapshot.id);
      // Only remove the exact version confirmed by this PUT. A different tab's
      // newer edits, and newer edits in this tab, must remain recoverable.
      if (draft && JSON.stringify(draft.project) === serialized) matched.push(storedKey);
    }
    for (const storedKey of matched) localStorage.removeItem(storedKey);
  } catch {
    // A retained duplicate is safer than removing an unconfirmed draft.
  }
}
