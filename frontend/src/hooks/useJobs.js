import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';

export function useJobs(projectId) {
  const [jobs, setJobsState] = useState([]);
  const jobsRef = useRef([]);
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;
  const setJobs = useCallback((value) => {
    const next = typeof value === 'function' ? value(jobsRef.current) : value;
    jobsRef.current = next;
    setJobsState(next);
  }, []);

  const refresh = useCallback(async () => {
    if (!projectId) return [];
    const list = await api('/jobs?pid=' + projectId);
    if (projectIdRef.current === projectId) setJobs(list);
    return list;
  }, [projectId, setJobs]);

  const cancel = useCallback((id) => api('/jobs/' + id + '/cancel', {}), []);

  useEffect(() => {
    if (!projectId) {
      setJobs([]);
      return undefined;
    }
    let cancelled = false;
    let pending = false;
    const tick = async () => {
      if (pending) return;
      pending = true;
      try {
        const list = await api('/jobs?pid=' + projectId);
        if (!cancelled) setJobs(list);
      } catch {
        // Keep the last known job state while the local service reconnects.
      } finally {
        pending = false;
      }
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [projectId, setJobs]);

  const active = jobs.find((job) => ['queued', 'running', 'cancelling'].includes(job.status));
  return { jobs, setJobs, active, refresh, cancel };
}
