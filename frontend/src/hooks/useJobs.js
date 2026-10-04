import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';

export function useJobs(projectId) {
  const [jobs, setJobsState] = useState([]);
  const [connectionState, setConnectionState] = useState(projectId ? 'connecting' : 'idle');
  const [lastSuccessAt, setLastSuccessAt] = useState(null);
  const [failureCount, setFailureCount] = useState(0);
  const jobsRef = useRef([]);
  const projectIdRef = useRef(projectId);
  const epochRef = useRef(0);
  const requestRef = useRef(0);
  const mountedRef = useRef(true);
  const failuresRef = useRef(0);
  const controllers = useRef(new Set());
  if (projectIdRef.current !== projectId) epochRef.current += 1;
  projectIdRef.current = projectId;
  const setJobs = useCallback((value) => {
    const next = typeof value === 'function' ? value(jobsRef.current) : value;
    jobsRef.current = next;
    setJobsState(next);
  }, []);

  const refresh = useCallback(async () => {
    if (!projectId) return [];
    const epoch = epochRef.current,
      request = ++requestRef.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    const valid = () =>
      mountedRef.current && epoch === epochRef.current && request === requestRef.current;
    try {
      const list = await api('/jobs?pid=' + projectId, undefined, 'GET', {
        signal: controller.signal,
        timeoutMs: 12000,
      });
      if (valid()) {
        setJobs(list);
        setConnectionState('connected');
        setLastSuccessAt(new Date().toISOString());
        setFailureCount(0);
        failuresRef.current = 0;
      }
      return list;
    } catch (error) {
      if (valid() && error?.name !== 'AbortError') {
        failuresRef.current += 1;
        setFailureCount(failuresRef.current);
        setConnectionState(failuresRef.current >= 3 ? 'offline' : 'reconnecting');
      }
      throw error;
    } finally {
      controllers.current.delete(controller);
    }
  }, [projectId, setJobs]);

  const cancel = useCallback((id) => api('/jobs/' + id + '/cancel', {}), []);

  useEffect(() => {
    mountedRef.current = true;
    failuresRef.current = 0;
    if (!projectId) {
      setJobs([]);
      setConnectionState('idle');
      setFailureCount(0);
      setLastSuccessAt(null);
      return undefined;
    }
    setJobs([]);
    setConnectionState('connecting');
    setFailureCount(0);
    setLastSuccessAt(null);
    let cancelled = false;
    let pending = false;
    let timer;
    const schedule = (delay) => {
      if (cancelled) return;
      clearTimeout(timer);
      timer = setTimeout(tick, delay);
    };
    const tick = async () => {
      if (cancelled || pending) return;
      pending = true;
      try {
        await refresh();
      } catch {
        // refresh keeps the last successful data and records only current failures.
      } finally {
        pending = false;
        const delay = failuresRef.current
          ? Math.min(15000, 1000 * 2 ** Math.min(failuresRef.current - 1, 4))
          : document.visibilityState === 'hidden'
            ? 6000
            : 1000;
        schedule(delay);
      }
    };
    tick();
    const onVisibility = () => {
      if (document.visibilityState === 'visible' && !pending) schedule(0);
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelled = true;
      mountedRef.current = false;
      ++epochRef.current;
      ++requestRef.current;
      clearTimeout(timer);
      for (const controller of controllers.current) controller.abort();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [projectId, setJobs, refresh]);

  const active = jobs.find((job) => ['queued', 'running', 'cancelling'].includes(job.status));
  return {
    jobs,
    setJobs,
    active,
    refresh,
    cancel,
    connectionState,
    lastSuccessAt,
    failureCount,
    isStale: failureCount > 0,
  };
}
