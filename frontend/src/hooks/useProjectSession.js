import { useCallback, useEffect, useRef, useState } from 'react';
import { api, clone } from '../api/client.js';
import { rebaseChanges } from './projectChanges.js';
import { readDraft, removeDraft, removeSavedDrafts, saveDraft } from './projectDrafts.js';

const SAVE_DELAY_MS = 1300;
const GROUP_WINDOW_MS = 900;

function changedPath(before, after, path = '') {
  if (Object.is(before, after)) return [];
  if (
    !before ||
    !after ||
    typeof before !== 'object' ||
    typeof after !== 'object' ||
    Array.isArray(before) !== Array.isArray(after)
  )
    return [path || '$'];
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const result = [];
  for (const key of keys)
    result.push(...changedPath(before[key], after[key], path ? `${path}.${key}` : key));
  return result;
}

export function useProjectSession() {
  const [p, setPState] = useState(null);
  const [dirty, setDirtyState] = useState(false);
  const [saveStatus, setSaveStatus] = useState('saved');
  const [conflict, setConflictState] = useState(null);
  const [editedGeneration, setEditedGeneration] = useState(0);
  const pRef = useRef(null);
  const dirtyRef = useRef(false);
  const generationRef = useRef(0);
  const inFlightRef = useRef(new Map());
  const operationRef = useRef(null);
  const conflictRef = useRef(null);

  const setP = useCallback((value) => {
    const next = typeof value === 'function' ? value(pRef.current) : value;
    pRef.current = next;
    setPState(next);
  }, []);

  const clearConflict = useCallback(() => {
    conflictRef.current = null;
    setConflictState(null);
  }, []);

  const setDirty = useCallback(
    (value) => {
      const next = typeof value === 'function' ? value(dirtyRef.current) : value;
      dirtyRef.current = next;
      setDirtyState(next);
      if (next) {
        setSaveStatus('draft');
      } else {
        const project = pRef.current;
        if (project) removeDraft(project.id);
        setSaveStatus('saved');
        clearConflict();
      }
    },
    [clearConflict],
  );

  const edit = useCallback((fn) => {
    if (conflictRef.current) return;
    const before = pRef.current;
    if (!before) return;
    const next = clone(before);
    fn(next);
    const paths = changedPath(before, next);
    if (!paths.length) return;
    const key =
      paths.length === 1
        ? paths[0]
        : paths
            .map((path) => path.split('.').slice(0, 2).join('.'))
            .sort()
            .join('|');
    const time = Date.now();
    if (
      !operationRef.current ||
      operationRef.current.key !== key ||
      time - operationRef.current.at > GROUP_WINDOW_MS
    ) {
      operationRef.current = { id: crypto.randomUUID(), key, at: time };
    } else {
      operationRef.current.at = time;
    }
    generationRef.current += 1;
    setEditedGeneration(generationRef.current);
    pRef.current = next;
    setPState(next);
    dirtyRef.current = true;
    setDirtyState(true);
    setSaveStatus('draft');
    saveDraft(next, operationRef.current);
  }, []);

  const save = useCallback(
    async ({ flush = true } = {}) => {
      while (true) {
        if (conflictRef.current) return null;
        const project = pRef.current;
        if (!project) return null;
        if (!dirtyRef.current) return project;

        const pending = inFlightRef.current.get(project.id);
        if (pending) {
          const result = await pending;
          if (!result) return null;
          continue;
        }

        const generation = generationRef.current;
        const snapshot = clone(project);
        const operationId = operationRef.current?.id || '';
        setSaveStatus('saving');
        const request = api(`/projects/${snapshot.id}`, snapshot, 'PUT', {
          headers: operationId ? { 'X-History-Operation': operationId } : {},
        });
        inFlightRef.current.set(snapshot.id, request);
        let saved;
        try {
          saved = await request;
        } catch (error) {
          if (inFlightRef.current.get(snapshot.id) === request)
            inFlightRef.current.delete(snapshot.id);
          if (error.status === 409) {
            const retained = pRef.current?.id === snapshot.id ? pRef.current : snapshot;
            saveDraft(retained, operationRef.current);
            try {
              const latest = await api(`/projects/${snapshot.id}`);
              const draft = {
                project: pRef.current?.id === snapshot.id ? pRef.current : snapshot,
                baseRevision: snapshot.revision,
                server: latest,
              };
              conflictRef.current = draft;
              setConflictState(draft);
              setSaveStatus('conflict');
            } catch {
              const draft = { project: retained, baseRevision: snapshot.revision, server: null };
              conflictRef.current = draft;
              setConflictState(draft);
              setSaveStatus('conflict');
            }
          } else {
            setSaveStatus('draft');
          }
          throw error;
        }
        if (inFlightRef.current.get(snapshot.id) === request)
          inFlightRef.current.delete(snapshot.id);
        removeSavedDrafts(snapshot);

        const current = pRef.current;
        if (current?.id === snapshot.id) {
          if (generationRef.current === generation) {
            pRef.current = saved;
            setPState(saved);
            dirtyRef.current = false;
            setDirtyState(false);
            setSaveStatus('saved');
            clearConflict();
            return saved;
          }
          const rebased = rebaseChanges(snapshot, current, saved);
          pRef.current = rebased;
          setPState(rebased);
          dirtyRef.current = true;
          setDirtyState(true);
          saveDraft(rebased, operationRef.current);
          setSaveStatus('draft');
          if (!flush) return rebased;
        } else {
          const local = readDraft(snapshot.id);
          if (local) {
            local.baseRevision = saved.revision;
            local.project.revision = saved.revision;
            saveDraft(local.project, operationRef.current);
          }
        }
        // A caller starting a job or changing projects must wait until the latest
        // generation is on disk. Autosave shares this same single-flight path.
      }
    },
    [clearConflict],
  );

  const open = useCallback(
    async (id) => {
      if (dirtyRef.current) {
        const saved = await save();
        if (!saved) return null;
      }
      const server = await api('/projects/' + id);
      const draft = readDraft(id);
      generationRef.current += 1;
      setEditedGeneration(generationRef.current);
      operationRef.current = null;
      if (draft && draft.baseRevision === server.revision) {
        const restored = { ...draft.project, revision: server.revision };
        pRef.current = restored;
        setPState(restored);
        dirtyRef.current = true;
        setDirtyState(true);
        operationRef.current = draft.operationId
          ? {
              id: draft.operationId,
              key: draft.operationKey || 'restored',
              at: Date.now(),
            }
          : null;
        setSaveStatus('draft');
        clearConflict();
        saveDraft(restored, operationRef.current);
        return restored;
      }
      if (draft && draft.baseRevision !== server.revision) {
        const retained = { ...draft.project, revision: draft.baseRevision };
        pRef.current = retained;
        setPState(retained);
        dirtyRef.current = true;
        setDirtyState(true);
        saveDraft(retained, operationRef.current);
        const details = { project: retained, baseRevision: draft.baseRevision, server };
        conflictRef.current = details;
        setConflictState(details);
        setSaveStatus('conflict');
        return retained;
      }
      pRef.current = server;
      setPState(server);
      dirtyRef.current = false;
      setDirtyState(false);
      setSaveStatus('saved');
      clearConflict();
      return server;
    },
    [clearConflict, save],
  );

  const loadLatest = useCallback(async () => {
    const currentConflict = conflictRef.current;
    if (!currentConflict) return;
    const latest = await api(`/projects/${currentConflict.project.id}`);
    removeDraft(latest.id);
    generationRef.current += 1;
    setEditedGeneration(generationRef.current);
    operationRef.current = null;
    pRef.current = latest;
    setPState(latest);
    dirtyRef.current = false;
    setDirtyState(false);
    setSaveStatus('saved');
    clearConflict();
  }, [clearConflict]);

  useEffect(() => {
    if (!dirty || conflict) return undefined;
    const scheduledProjectId = p?.id;
    const timer = setTimeout(() => {
      if (pRef.current?.id === scheduledProjectId) save({ flush: false }).catch(() => {});
    }, SAVE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [dirty, p?.id, editedGeneration, conflict, save]);

  return {
    p,
    setP,
    dirty,
    setDirty,
    edit,
    save,
    open,
    saveStatus,
    conflict,
    loadLatest,
    pRef,
    dirtyRef,
  };
}
