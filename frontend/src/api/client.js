export async function api(path, body, method, extra = {}) {
  const opts = { method: method || (body === undefined ? 'GET' : 'POST'), ...extra };
  if (body !== undefined) {
    opts.body = body instanceof FormData ? body : JSON.stringify(body);
    if (!(body instanceof FormData)) {
      opts.headers = { 'Content-Type': 'application/json', ...extra.headers };
    }
  }
  const response = await fetch('/api' + path, opts);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(
      typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data),
    );
    error.status = response.status;
    error.detail = data.detail;
    throw error;
  }
  return data;
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
