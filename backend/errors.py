"""Small, backward-compatible error envelopes for asynchronous jobs."""

from __future__ import annotations

import errno
import re

from .models import uid
from .store import now


class ProviderOutcomeUnknown(Exception):
    """The request may have reached a provider, but no response was persisted."""


class InputBlocked(ValueError):
    """Saved inputs cannot safely be used for a new attempt."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def safe_message(value: object, limit: int = 1200) -> str:
    """Remove common secrets, URLs, and machine-specific paths from user text."""
    text = str(value or "")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [đã ẩn]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[\w-]{20,}|hf_[\w-]{12,})\b", "[đã ẩn]", text)
    text = re.sub(r"(?i)(?:api[_-]?key|x-api-key|token|secret|password)\s*[:=]\s*[^\s,;]+", "[thông tin xác thực đã ẩn]", text)
    text = re.sub(r"https?://[^\s\]\[<>\"]+", "[địa chỉ dịch vụ đã ẩn]", text, flags=re.I)
    text = re.sub(r"(?i)([A-Z]:\\[^\s\"<>|]+|\\\\[^\s\"<>|]+)", "[đường dẫn đã ẩn]", text)
    text = re.sub(r"(?i)(/(?:Users|home|private|tmp|var|mnt)/[^\s\"<>]+)", "[đường dẫn đã ẩn]", text)
    return text[:limit]


class ResourcePressure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def job_error(exc: Exception, *, job_id: str, step_id: str = "") -> dict:
    message = "Tác vụ gặp lỗi nội bộ. Hãy dùng mã chẩn đoán để tra cứu."
    code, category, retryable, actions = "INTERNAL_ERROR", "internal", False, ["view_diagnostics"]
    http_status = None
    if isinstance(exc, ResourcePressure):
        code, category, retryable = exc.code, "resource", True
        message = {"DISK_LOW": "Ổ data/temp còn quá ít dung lượng; đã dừng an toàn và giữ checkpoint.",
                   "MEMORY_LOW": "Máy còn quá ít RAM; đã dừng an toàn và giữ checkpoint.",
                   "TELEMETRY_UNAVAILABLE": "Không đọc được telemetry tài nguyên; kiểm tra cài đặt rồi tiếp tục.",
                   "CPU_SLOT_UNAVAILABLE": "Chưa có slot CPU cho encoder dự phòng; có thể tiếp tục khi có tài nguyên."}.get(code, message)
        actions = ["check_storage", "resume_when_ready", "view_diagnostics"] if code == "DISK_LOW" else ["resume_when_ready", "view_diagnostics"]
    elif isinstance(exc, InputBlocked):
        code, category, actions = exc.code, "conflict", ["create_new_job", "view_diagnostics"]
        message = safe_message(exc)
    elif isinstance(exc, ProviderOutcomeUnknown):
        code, category, retryable, actions = "PROVIDER_OUTCOME_UNKNOWN", "provider", False, ["confirm_provider_retry"]
        message = safe_message(exc)
    elif isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        code, category, message, actions = "DISK_FULL", "storage", "Ổ đĩa không còn đủ dung lượng để hoàn tất tác vụ.", ["check_storage", "view_diagnostics"]
    elif isinstance(exc, FileNotFoundError):
        code, category, message, actions = "MEDIA_MISSING", "input", "Không tìm thấy tư liệu hoặc checkpoint cần cho tác vụ. Hãy chọn lại tư liệu hoặc tạo tác vụ mới.", ["replace_input", "view_diagnostics"]
    elif isinstance(exc, PermissionError):
        code, category, message, actions = "FILE_ACCESS_DENIED", "storage", "ClipForge không thể đọc hoặc ghi một tệp cần thiết. Kiểm tra quyền truy cập và dung lượng lưu trữ.", ["check_storage", "view_diagnostics"]
    elif isinstance(exc, (ValueError, OSError)):
        raw = str(exc)
        match = re.search(r"\bHTTP\s+(\d{3})\b", raw, flags=re.I)
        if match:
            http_status = int(match.group(1))
        lowered = raw.casefold()
        if any(term in lowered for term in ("no space left on device", "not enough space", "disk is full", "ổ đĩa không còn đủ dung lượng")):
            code, category, message, actions = "DISK_FULL", "storage", "Ổ đĩa không còn đủ dung lượng để hoàn tất tác vụ.", ["check_storage", "view_diagnostics"]
        elif http_status in (401, 403):
            code, category, message, actions = "PROVIDER_AUTH_REQUIRED", "provider", "Dịch vụ AI từ chối xác thực. Kiểm tra khóa, model và hồ sơ API rồi thử lại.", ["open_api_settings", "view_diagnostics"]
        elif http_status == 402 or any(term in lowered for term in ("quota exceeded", "billing limit", "insufficient quota", "hết hạn mức")):
            code, category, message, actions = "PROVIDER_QUOTA_EXCEEDED", "provider", "Hồ sơ AI đã hết hạn mức hoặc cần kiểm tra thanh toán.", ["check_provider_quota", "view_diagnostics"]
        elif http_status == 429:
            code, category, retryable, message, actions = "PROVIDER_RATE_LIMITED", "provider", True, "Dịch vụ AI đang giới hạn tốc độ yêu cầu. Chờ một lúc rồi tiếp tục tác vụ.", ["resume_when_ready", "view_diagnostics"]
        elif http_status in (408, 425) or (http_status is not None and http_status >= 500):
            code, category, retryable, message, actions = "PROVIDER_UNAVAILABLE", "provider", True, "Dịch vụ AI tạm thời không khả dụng. Hãy kiểm tra kết nối rồi tiếp tục tác vụ.", ["resume_when_ready", "view_diagnostics"]
        elif "checkpoint" in lowered or "fingerprint" in lowered or "snapshot" in lowered:
            code, category, message, actions = "CHECKPOINT_INVALID", "input", "Checkpoint không còn khớp với dữ liệu hiện tại. Tạo tác vụ mới để chạy lại các bước phụ thuộc.", ["create_new_job", "view_diagnostics"]
        elif "revision" in lowered or "đã thay đổi" in lowered:
            code, category, message, actions = "REVISION_CONFLICT", "conflict", "Dự án đã thay đổi trong khi tác vụ đang xử lý. Tải lại dự án rồi tạo tác vụ mới.", ["reload_project", "create_new_job"]
        elif any(term in lowered for term in ("không chứa hình ảnh", "không có luồng", "không giải mã được", "không hợp lệ", "invalid data found when processing input", "error while decoding")):
            code, category, message, actions = "MEDIA_INVALID", "input", "Một tư liệu không hợp lệ hoặc không đúng định dạng. Mở chi tiết để xác định bước lỗi, rồi thay tư liệu đó.", ["replace_input", "view_diagnostics"]
        elif "no such file or directory" in lowered or "cannot find the file" in lowered:
            code, category, message, actions = "MEDIA_MISSING", "input", "Không tìm thấy tư liệu cần cho tác vụ. Hãy chọn lại tư liệu hoặc tạo tác vụ mới.", ["replace_input", "view_diagnostics"]
        elif "encoder" in lowered and any(term in lowered for term in ("not found", "unavailable", "unknown encoder", "cannot initialize")):
            code, category, message, actions = "ENCODER_UNAVAILABLE", "render", "Encoder video không khả dụng trên máy này. ClipForge đã thử phương án CPU nếu hỗ trợ; hãy kiểm tra diagnostics nếu lỗi còn tiếp diễn.", ["view_diagnostics"]
        else:
            code, category, message, actions = "VALIDATION_ERROR", "input", safe_message(raw), ["view_diagnostics"]
    diagnostic_id = uid()
    return {
        "code": code,
        "category": category,
        "message": message,
        "severity": "error",
        "job_id": job_id,
        "step_id": step_id or None,
        "retryable": retryable,
        "actions": actions,
        "diagnostic_id": diagnostic_id,
        "safe_details": {"exception_type": type(exc).__name__, **({"http_status": http_status} if http_status else {})},
        "occurred_at": now(),
        # Kept while clients still read the previous job.message/detail shape.
        "detail": message,
    }
