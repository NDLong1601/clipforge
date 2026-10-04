export async function api(path, body, method, extra = {}) {
  const { timeoutMs = 15000, signal: callerSignal, ...requestOptions } = extra;
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener('abort', abortFromCaller, { once: true });
  const timer =
    Number.isFinite(timeoutMs) && timeoutMs > 0
      ? setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, timeoutMs)
      : null;
  const opts = {
    ...requestOptions,
    signal: controller.signal,
    method: method || (body === undefined ? 'GET' : 'POST'),
  };
  if (body !== undefined) {
    opts.body = body instanceof FormData ? body : JSON.stringify(body);
    if (!(body instanceof FormData)) {
      opts.headers = { 'Content-Type': 'application/json', ...requestOptions.headers };
    }
  }
  try {
    const response = await fetch('/api' + path, opts);
    if (response.status === 204) return null;
    const raw = await response.text();
    let data = null;
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        data = { detail: raw.slice(0, 800) };
      }
    }
    if (!response.ok) {
      const detail = data?.detail ?? data;
      const error = new Error(
        typeof detail === 'string'
          ? detail
          : detail?.message || JSON.stringify(detail || `HTTP ${response.status}`),
      );
      error.status = response.status;
      error.detail = detail;
      error.code = detail?.code || data?.code;
      error.category = detail?.category || data?.category;
      error.retryable = detail?.retryable === true;
      error.diagnosticId = detail?.diagnostic_id;
      throw error;
    }
    return data;
  } catch (cause) {
    if (cause?.status) throw cause;
    if (timedOut) {
      const error = new Error(
        'ClipForge phản hồi quá chậm. Trạng thái gần nhất được giữ lại và sẽ đồng bộ lại.',
      );
      error.code = 'REQUEST_TIMEOUT';
      error.cause = cause;
      throw error;
    }
    if (callerSignal?.aborted || cause?.name === 'AbortError') throw cause;
    const error = new Error(
      'Không kết nối được với ClipForge. Kiểm tra ứng dụng rồi thử đồng bộ lại.',
    );
    error.code = 'NETWORK_ERROR';
    error.cause = cause;
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
    callerSignal?.removeEventListener('abort', abortFromCaller);
  }
}

export function uploadWithProgress(path, form, onProgress, signal) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api' + path);
    xhr.responseType = 'json';
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress?.(Math.round((event.loaded * 100) / event.total));
    });
    xhr.addEventListener('load', () => {
      const data = xhr.response || {};
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        const error = new Error(
          typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data),
        );
        error.status = xhr.status;
        error.detail = data.detail;
        reject(error);
      }
    });
    xhr.addEventListener('error', () =>
      reject(new Error('Không gửi được file. Kiểm tra kết nối với ClipForge.')),
    );
    xhr.addEventListener('abort', () => reject(new DOMException('Đã hủy tải lên', 'AbortError')));
    if (signal) {
      if (signal.aborted) return xhr.abort();
      signal.addEventListener('abort', () => xhr.abort(), { once: true });
    }
    xhr.send(form);
  });
}

export const clone = (value) => structuredClone(value);
export const fmt = (n) =>
  `${Math.floor(n / 60)
    .toString()
    .padStart(2, '0')}:${(n % 60).toFixed(1).padStart(4, '0')}`;
export const aid = (project, id) => project.assets.find((asset) => asset.id === id);
export const mediaUrl = (project, id) => `/api/projects/${project.id}/assets/${id}/file`;
export const thumbUrl = (project, name) =>
  name ? `/api/projects/${project.id}/thumbs/${name}` : '';
export const exportUrl = (project, item, name) =>
  `/api/projects/${project.id}/exports/${item.id}/${name || item.filename}`;
export const roleNames = {
  source: 'Nguồn dựng',
  reference: 'Video mẫu',
  voice: 'Giọng đọc',
  music: 'Nhạc nền',
  overlay: 'Logo / biểu tượng',
};
