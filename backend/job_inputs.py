"""Public computation identities and admission checks; credentials stay in memory."""

import hashlib
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import job_store, store
from .errors import InputBlocked

OPERATION_VERSION = 1
PROJECT_RESULT_VERSION = 2
TRANSCRIPTION_VERSION = 2


def endpoint_id(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').casefold()
    if ':' in host:
        host = '[' + host + ']'
    if parts.port:
        host += ':' + str(parts.port)
    # Keys in URL credentials/query do not identify the computation.
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not any(word in key.casefold().replace('-', '_') for word in ('key', 'token', 'secret', 'password', 'auth', 'signature'))]
    normalized = urlunsplit((parts.scheme.casefold(), host, parts.path.rstrip('/'), urlencode(sorted(query)), ''))
    return hashlib.sha256(normalized.encode()).hexdigest()


def config_identity(kind, settings=None):
    from . import media, providers, render, face_filter
    settings = settings if settings is not None else store.settings()
    config = {'identity_version': 2, 'operation_version': OPERATION_VERSION, 'kind': kind}
    profiles = [{
        'id': item.id, 'provider': item.provider, 'model': item.model,
        'endpoint_id': endpoint_id(providers.ai_endpoint(item)), 'enabled': item.enabled,
    } for item in settings.ai_profiles]
    if kind in {'analyze', 'plan', 'template', 'assistant', 'assemble-audio', 'transcribe', 'whiteboard-plan'}:
        config['ai'] = {'active_profile_id': settings.active_ai_profile_id,
            'auto_fallback': settings.ai_auto_fallback, 'profiles': profiles,
            'legacy_endpoint_id': endpoint_id(settings.ai_base_url), 'legacy_model': settings.ai_model,
            'version': 'ai-json-label-v3'}
    if kind in {'voice', 'transcribe', 'assemble-audio'}:
        # audio_connection chooses an enabled OpenAI-compatible route with a key.
        # Persist its identity, never its credential or the original URL.
        try:
            route, _ = providers.audio_connection(settings)
        except ValueError:
            route = settings.ai_base_url
        config['audio_endpoint_id'] = endpoint_id(route)
    if kind == 'voice':
        config['tts'] = {name: getattr(settings, name) for name in (
            'tts_provider', 'tts_model', 'tts_voice', 'tts_speed', 'azure_region', 'azure_voice', 'windows_voice')}
        config['tts']['version'] = 'tts-sentence-v2'
    if kind in {'transcribe', 'assemble-audio'}:
        config['asr'] = {name: getattr(settings, name) for name in (
            'transcription_provider', 'transcription_model', 'local_whisper_model', 'language')}
        config['asr']['version'] = TRANSCRIPTION_VERSION
    if kind in {'analyze', 'template', 'assemble-audio', 'upload'}:
        config['analysis_version'] = media.ANALYSIS_VERSION
    if kind in {'filter-faces', 'plan', 'analyze', 'assemble-audio'}:
        config['face_version'] = face_filter.VERSION
    if kind in {'plan', 'assemble-audio'}:
        config['planner_version'] = 'planner-v1'
    if kind == 'portrait':
        from . import portrait
        config['portrait_version'] = portrait.VERSION
    if kind == 'whiteboard-images':
        config['images'] = {'profile_id': settings.image_profile_id, 'model': settings.image_model,
            'quality': settings.image_quality, 'active_profile_id': settings.active_ai_profile_id,
            'profiles': profiles, 'version': 'whiteboard-images-v1'}
    if kind in ('whiteboard-plan', 'whiteboard-images', 'whiteboard-render'):
        from .whiteboard import engine
        config['whiteboard_version'] = engine.VERSION
    if kind in ('render', 'portrait', 'whiteboard-render'):
        binary = Path(media.ffmpeg()).resolve()
        stat = binary.stat()
        config['renderer'] = {'version': render.RENDER_VERSION,
            'ffmpeg_path_id': hashlib.sha256(str(binary).encode()).hexdigest(),
            'ffmpeg_size': stat.st_size, 'ffmpeg_mtime_ns': stat.st_mtime_ns, 'audio_clock_version': 1}
    return config


def config_fingerprint(kind, settings=None):
    return job_store.fingerprint(config_identity(kind, settings))


def validate_revision(job):
    # Rendering a saved snapshot publishes an export onto the latest project;
    # operations changing project content must still target the submitted revision.
    if job.kind in ('render', 'portrait', 'whiteboard-render') or job.input_revision is None or not job.snapshot_ref:
        return
    with store.LOCK:
        if store.read(job.pid).revision != job.input_revision:
            raise InputBlocked('REVISION_CONFLICT', 'Dự án đã thay đổi. Tạo tác vụ từ bản hiện tại; checkpoint cũ được giữ lại.')


def validate_before_run(job, *, capture_settings=False):
    validate_revision(job)
    if not job.config_fingerprint:
        return  # Legacy/non-persisted runtime handlers have no config contract.
    settings = store.settings().model_copy(deep=True)
    if config_fingerprint(job.kind, settings) != job.config_fingerprint:
        raise InputBlocked('CONFIG_CHANGED', 'Cấu hình hoặc phiên bản xử lý đã đổi. Tạo tác vụ từ bản hiện tại; checkpoint cũ được giữ lại.')
    if capture_settings:
        job._settings = settings


def media_identity(project):
    from . import media
    result = []
    for asset in sorted((asset for asset in project.assets if not asset.deleted), key=lambda asset: asset.id):
        path = store.asset_path(project.id, asset)
        result.append({'id': asset.id, 'filename': asset.filename,
            'sha256': media.file_sha256(path) if path.is_file() else None})
    return result


def cached_project(value, original):
    """A cache miss is safe; malformed/cross-project results never get applied."""
    from .models import Project
    from .project_validation import validate_project
    try:
        if not isinstance(value, dict) or value.get('schema_version') != PROJECT_RESULT_VERSION:
            return None
        project = validate_project(Project.model_validate(value['project']))
        assets = {asset.id: asset for asset in project.assets}
        if (project.id != original.id or project.revision != original.revision
                or project.content_revision != original.content_revision
                or project.job_commit_tokens != original.job_commit_tokens or project.exports != original.exports
                or any(asset.id not in assets or any(getattr(assets[asset.id], name) != getattr(asset, name)
                    for name in ('filename', 'role', 'media', 'deleted')) for asset in original.assets)
                or value.get('media') != media_identity(project)
                or any(item['sha256'] is None for item in value['media'])):
            return None
        return project
    except (OSError, ValueError, TypeError, KeyError):
        return None


def project_result(project):
    return {'schema_version': PROJECT_RESULT_VERSION, 'project': project.model_dump(), 'media': media_identity(project)}


def asset_result(project, asset):
    from . import media
    return {'schema_version': PROJECT_RESULT_VERSION, 'asset': asset.model_dump(),
        'source_sha256': media.file_sha256(store.asset_path(project.id, asset))}


def cached_asset(value, project, original):
    from . import media
    from .models import Asset
    from .project_validation import validate_project
    try:
        if not isinstance(value, dict) or value.get('schema_version') != PROJECT_RESULT_VERSION:
            return None
        asset = Asset.model_validate(value['asset'])
        if (asset.id != original.id or asset.filename != original.filename or asset.role != original.role
                or asset.media != original.media or asset.duration != original.duration
                or value.get('source_sha256') != media.file_sha256(store.asset_path(project.id, original))):
            return None
        candidate = project.model_copy(deep=True)
        candidate.assets[candidate.assets.index(original)] = asset
        validate_project(candidate)
        return asset
    except (OSError, ValueError, TypeError, KeyError):
        return None
