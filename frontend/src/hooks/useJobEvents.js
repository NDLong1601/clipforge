import { useEffect, useRef } from 'react';
import { api } from '../api/client.js';

export function terminalKey(job) {
  // Legacy records have no attempt. Progress/state_version is not an event ID.
  return `${job.pid}:${job.id}:${job.attempt || 1}:${job.status}`;
}

export function useJobEvents(options) {
  const latest = useRef(options);
  latest.current = options;
  const seen = useRef(new Set());
  const pending = useRef(new Set());
  const scope = useRef({ pid: options.projectId, epoch: 0, mounted: true });
  if (scope.current.pid !== options.projectId) {
    scope.current.pid = options.projectId;
    scope.current.epoch += 1;
  }
  useEffect(() => {
    scope.current.mounted = true;
    return () => {
      scope.current.mounted = false;
      scope.current.epoch += 1;
    };
  }, []);
  useEffect(() => {
    const { projectId: pid, jobs } = options;
    if (!pid || options.enabled === false) return;
    const epoch = scope.current.epoch;
    const valid = () => scope.current.mounted && scope.current.epoch === epoch;
    for (const job of jobs) {
      if (
        job.pid !== pid ||
        !['done', 'error', 'cancelled', 'interrupted', 'blocked'].includes(job.status)
      )
        continue;
      const key = terminalKey(job);
      if (seen.current.has(key) || pending.current.has(key)) continue;
      if (job.status !== 'done') {
        seen.current.add(key);
        if (['error', 'interrupted', 'blocked'].includes(job.status))
          latest.current.onError(job.message);
        continue;
      }
      if (job.kind === 'assistant') {
        seen.current.add(key);
        latest.current.onProposal(job.result);
        continue;
      }
      // The real session can merge disjoint server changes into a draft. If it
      // cannot, leave this event pending until saving/conflict resolution succeeds.
      if (latest.current.dirtyRef.current && !latest.current.syncJobResult) continue;
      pending.current.add(key);
      (async () => {
        try {
          const o = latest.current;
          const updated = o.syncJobResult
            ? await o.syncJobResult(pid)
            : await api(`/projects/${pid}`);
          if (!valid() || !updated) return;
          if (!o.syncJobResult) {
            if (o.dirtyRef.current) return;
            o.setP(updated);
          }
          if (job.kind === 'upload' && job.result?.auto_assemble) {
            if (o.dirtyRef.current) return;
            const nextJob = await api(`/projects/${pid}/assemble-audio`, { upload_job_id: job.id });
            if (!valid()) return;
            latest.current.onAutoAssemble(nextJob);
          }
          if (job.kind === 'render' || job.kind === 'portrait') {
            const exportId = job.result?.export_id || job.result?.id || job.id;
            const exported = updated.exports?.find((item) => item.id === exportId);
            if (!exported) return; // A delayed project response must not consume the event.
            latest.current.onPreview(exported);
          }
          seen.current.add(key);
          latest.current.onDone(job);
        } catch (error) {
          if (valid()) latest.current.onError(error.message);
        } finally {
          pending.current.delete(key);
        }
      })();
    }
  }, [
    options.jobs,
    options.projectId,
    options.dirty,
    options.revision,
    options.conflict,
    options.enabled,
  ]);
  return (jobs, project) => {
    for (const job of jobs) {
      if (
        job.pid !== project.id ||
        !['done', 'error', 'cancelled', 'interrupted', 'blocked'].includes(job.status)
      )
        continue;
      if (job.status === 'done') {
        if (job.kind === 'upload' && job.result?.auto_assemble) continue;
        if (
          (job.kind === 'render' || job.kind === 'portrait') &&
          !project.exports?.some(
            (item) => item.id === (job.result?.export_id || job.result?.id || job.id),
          )
        )
          continue;
        if (job.result?.revision == null || job.result.revision > project.revision) continue;
      }
      seen.current.add(terminalKey(job));
    }
  };
}
