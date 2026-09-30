import copy
import json
import os
import re
import hashlib
import shutil
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .models import AIProfile, Project, Settings
from .project_validation import validate_project, validate_project_files

ROOT = Path(os.environ.get('CLIPFORGE_DATA', Path(__file__).resolve().parents[1] / 'data')).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
SCHEMA_VERSION = 3
SECRET_MASK = '••••••••'
_instance_handle = None
_instance_guard = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def _flush_file(path: Path):
    with path.open('rb+') as file:
        file.flush()
        os.fsync(file.fileno())


def _write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_backup_id(source_version: int, target_version: int = SCHEMA_VERSION,
                      fingerprint: str = '') -> str:
    suffix = f'-{fingerprint[:16]}' if fingerprint else ''
    return f'schema-v{source_version}-to-v{target_version}{suffix}'


def _schema_backup_restore_steps() -> str:
    return ('Dừng ClipForge. Lưu riêng thư mục dự án hiện tại. Giải nén gói này, chép project.json '
            'vào thư mục dự án và chép assets/ vào thư mục assets/ tương ứng; sau đó mở dự án '
            'bằng phiên bản ClipForge cũ cần khôi phục.')


def _read_valid_schema_backup(path: Path):
    manifest_path = path / 'manifest.json'
    project_path = path / 'project.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('backup_id') != path.name:
        return None
    if _sha256_file(project_path) != manifest.get('project_json_sha256'):
        return None
    assets_root = (path / 'assets').resolve()
    for asset in manifest.get('assets', []):
        if not asset.get('available'):
            continue
        filename = asset.get('filename', '')
        target = (assets_root / filename).resolve()
        if target.parent != assets_root or not target.is_file():
            return None
        if _sha256_file(target) != asset.get('sha256'):
            return None
    return manifest


def _create_schema_backup(pid: str, raw_bytes: bytes, project: Project, source_version: int):
    """Publish exact legacy JSON and a copy of its available media before migration writes."""
    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    asset_records = []
    asset_sources = []
    name_hashes = {}
    for asset in project.assets:
        source = asset_path(pid, asset)
        record = {'id': asset.id, 'filename': asset.filename, 'available': False,
                  'backup_path': '', 'sha256': ''}
        if source.is_file():
            content_hash = _sha256_file(source)
            if asset.filename in name_hashes and name_hashes[asset.filename] != content_hash:
                raise ValueError(f'Không thể sao lưu asset trùng tên {asset.filename!r} có nội dung khác nhau.')
            name_hashes[asset.filename] = content_hash
            record.update(available=True, backup_path=f'assets/{asset.filename}', sha256=content_hash)
        asset_records.append(record)
        asset_sources.append((source, record))

    identity = {
        'project_json_sha256': source_hash,
        'assets': [(record['id'], record['filename'], record['available'], record['sha256'])
                   for record in asset_records],
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    backup_id = _schema_backup_id(source_version, fingerprint=fingerprint)
    backup_root = project_dir(pid) / 'backups'
    destination = backup_root / backup_id
    if destination.is_dir():
        try:
            manifest = _read_valid_schema_backup(destination)
            if manifest.get('backup_fingerprint') == fingerprint:
                return destination
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        raise ValueError(f'Bản sao nâng schema {backup_id} đã tồn tại nhưng không hợp lệ; project.json được giữ nguyên.')

    backup_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f'.{backup_id}.', dir=backup_root))
    assets_dir = temporary / 'assets'
    assets_dir.mkdir()
    try:
        for source, record in asset_sources:
            if not record['available']:
                continue
            target = assets_dir / record['filename']
            if not target.exists():
                shutil.copyfile(source, target)
                _flush_file(target)
                if _sha256_file(target) != record['sha256']:
                    raise OSError(f'Asset {record["filename"]} đã thay đổi trong lúc sao lưu.')
        _write_bytes(temporary / 'project.json', raw_bytes)
        manifest = {
            'backup_id': backup_id,
            'backup_fingerprint': fingerprint,
            'source_schema_version': source_version,
            'target_schema_version': SCHEMA_VERSION,
            'created_at': now(),
            'project_json_sha256': source_hash,
            'assets': asset_records,
            'missing_assets': [item['id'] for item in asset_records if not item['available']],
            'restore_steps': _schema_backup_restore_steps(),
        }
        _write_bytes(temporary / 'manifest.json',
                     json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
        # The destination is required to be absent. A same-volume rename is
        # atomic and also works for directories on Windows, where replace()
        # can fail with Access Denied even when the target does not exist.
        os.rename(temporary, destination)
        if os.name != 'nt':
            try:
                directory_fd = os.open(backup_root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return destination


def schema_backup_info(pid: str):
    root = project_dir(pid) / 'backups'
    backups = []
    if root.is_dir():
        for path in root.glob('schema-v*-to-v*'):
            try:
                manifest = _read_valid_schema_backup(path)
                if manifest is None:
                    continue
                backups.append({
                    'id': manifest['backup_id'],
                    'source_schema_version': manifest['source_schema_version'],
                    'target_schema_version': manifest['target_schema_version'],
                    'created_at': manifest['created_at'],
                    'asset_count': sum(bool(asset.get('available')) for asset in manifest.get('assets', [])),
                    'missing_assets': manifest.get('missing_assets', []),
                })
            except (OSError, ValueError, TypeError, KeyError):
                continue
    backups.sort(key=lambda item: (item['source_schema_version'], item['created_at']))
    return {'available': bool(backups), 'backups': backups,
            'restore_steps': _schema_backup_restore_steps() if backups else ''}


def create_schema_backup_archive(pid: str, backup_id: str | None = None) -> Path:
    root = project_dir(pid) / 'backups'
    info = schema_backup_info(pid)
    if not info['available']:
        raise FileNotFoundError('Dự án chưa có bản sao trước nâng schema.')
    selected = backup_id or info['backups'][0]['id']
    if not re.fullmatch(r'schema-v\d+-to-v\d+(?:-[a-f0-9]{16})?', selected):
        raise ValueError('ID bản sao không hợp lệ.')
    source = root / selected
    if not source.is_dir() or not any(item['id'] == selected for item in info['backups']):
        raise FileNotFoundError('Không tìm thấy bản sao trước nâng schema.')
    download_root = ROOT / 'backup_downloads'
    download_root.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f'{pid}-{selected}-', suffix='.zip',
                                         dir=download_root, delete=False)
    archive = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in source.rglob('*'):
                if path.is_file():
                    zf.write(path, path.relative_to(source).as_posix())
        _flush_file(archive)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    return archive


def atomic(path: Path, data):
    """Durably write JSON using a unique temporary file beside the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         prefix=f'.{path.name}.', suffix='.tmp',
                                         dir=path.parent, delete=False) as tmp:
            temp_path = Path(tmp.name)
            json.dump(data, tmp, ensure_ascii=False, indent=2, allow_nan=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(temp_path, path)
        if os.name != 'nt':
            try:
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def project_dir(pid):
    if not re.fullmatch(r'[a-f0-9]{16}', pid):
        raise ValueError('ID dự án không hợp lệ')
    return ROOT / 'projects' / pid


def _migrate_project(raw):
    if not isinstance(raw, dict):
        raise ValueError('project.json: dữ liệu dự án phải là JSON object')
    data = copy.deepcopy(raw)
    version = data.get('schema_version', 0)
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValueError('schema_version: phiên bản dữ liệu không hợp lệ')
    if version > SCHEMA_VERSION:
        raise ValueError(f'schema_version: dữ liệu phiên bản {version} mới hơn ứng dụng này')
    if version == 0:
        for asset in data.get('assets', []):
            if isinstance(asset, dict):
                asset.setdefault('deleted', False)
        data['schema_version'] = 1
        version = 1
    if version == 1:
        # Older projects did not record whether estimated cues were edited by hand.
        # Preserve existing cue payloads conservatively when migrating.
        data.setdefault('cues_edited', bool(data.get('cues')))
        data.setdefault('cues_stale', False)
        data['schema_version'] = 2
        version = 2
    if version == 2:
        # Legacy layer editors retained asset_id after changing an image layer
        # to text/shape. It has no rendering effect, so normalize it on read.
        template = data.get('template')
        if isinstance(template, dict):
            layer_groups = [template.get('layers', [])]
            layer_groups.extend(template.get('slot_layers', []))
            for clip in data.get('clips', []):
                if isinstance(clip, dict):
                    layer_groups.append(clip.get('layers', []))
            for layers in layer_groups:
                if not isinstance(layers, list):
                    continue
                for layer in layers:
                    if isinstance(layer, dict) and layer.get('kind', 'text') != 'image':
                        layer['asset_id'] = ''
        data['schema_version'] = 3
    return data


def _read_project_file(path, expected_id=None):
    raw = json.loads(path.read_text(encoding='utf-8'))
    project = Project.model_validate(_migrate_project(raw))
    validate_project(project)
    if expected_id is not None and project.id != expected_id:
        raise ValueError(f"id: project.json thuộc dự án {project.id}, không phải {expected_id}")
    return project


def read(pid):
    return _read_project_file(project_dir(pid) / 'project.json', expected_id=pid)


def validate_files(p):
    validate_project_files(p, project_dir(p.id))


def save(p: Project, history=True):
    with LOCK:
        try:
            validated = Project.model_validate(p.model_dump())
        except ValidationError as exc:
            first = exc.errors()[0]
            location = '.'.join(str(part) for part in first.get('loc', ())) or 'project'
            raise ValueError(f"{location}: dữ liệu dự án không hợp lệ ({first.get('msg', 'lỗi schema')})") from exc
        for field, value in validated.model_dump().items():
            setattr(p, field, getattr(validated, field))
        validate_project(p)
        directory = project_dir(p.id)
        current_path = directory / 'project.json'
        previous = None
        if current_path.exists():
            raw_bytes = current_path.read_bytes()
            if history:
                previous = read(p.id)
            try:
                raw_current = json.loads(raw_bytes.decode('utf-8'))
                source_version = raw_current.get('schema_version', 0) if isinstance(raw_current, dict) else SCHEMA_VERSION
            except (UnicodeDecodeError, json.JSONDecodeError):
                source_version = SCHEMA_VERSION
            if (isinstance(source_version, int) and not isinstance(source_version, bool)
                    and 0 <= source_version < SCHEMA_VERSION):
                backup_project = previous or _read_project_file(current_path, expected_id=p.id)
                _create_schema_backup(p.id, raw_bytes, backup_project, source_version)
        if history and current_path.exists():
            previous = previous or read(p.id)
            # Persist the recovery point first. If replacing project.json fails,
            # both the prior file and this independent snapshot remain available.
            atomic(directory / 'history' / f'{previous.revision:08d}.json', previous.model_dump())
        p.revision += 1
        p.updated_at = now()
        atomic(current_path, p.model_dump())
        if history:
            history_paths = sorted((directory / 'history').glob('*.json'))
            for old_path in history_paths[:-25]:
                old_path.unlink(missing_ok=True)
    return p


def _valid_history(pid):
    directory = project_dir(pid) / 'history'
    for path in sorted(directory.glob('*.json'), reverse=True):
        try:
            yield path, _read_project_file(path, expected_id=pid)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError):
            continue


def _recovery_record(path):
    try:
        pid = path.parent.name
        for _, _project in _valid_history(pid):
            return True
    except Exception:
        pass
    return False


def _preserve_corrupt_current(pid):
    path = project_dir(pid) / 'project.json'
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    preserved = path.with_name(f'project.json.corrupt-{stamp}')
    preserved.write_bytes(path.read_bytes())
    return preserved


def projects():
    out = []
    for directory in (ROOT / 'projects').glob('*'):
        if not directory.is_dir() or not re.fullmatch(r'[a-f0-9]{16}', directory.name):
            continue
        path = directory / 'project.json'
        if not path.exists():
            out.append({'id': directory.name, 'name': 'Dự án cần phục hồi', 'mode': '',
                        'updated_at': '', 'clips': 0, 'corrupt': True,
                        'error': 'project.json không tồn tại',
                        'recovery_available': _recovery_record(path)})
            continue
        try:
            project = _read_project_file(path, expected_id=directory.name)
            out.append({'id': project.id, 'name': project.name, 'mode': project.mode,
                        'updated_at': project.updated_at, 'clips': len(project.clips),
                        'corrupt': False, 'recovery_available': False})
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            out.append({'id': directory.name, 'name': 'Dự án cần phục hồi', 'mode': '',
                        'updated_at': '', 'clips': 0, 'corrupt': True,
                        'error': str(exc)[:300], 'recovery_available': _recovery_record(path)})
    return sorted(out, key=lambda item: item['updated_at'], reverse=True)


def _merge_history_assets(project, current):
    if current is None:
        return
    history_assets = {asset.id: asset for asset in project.assets}
    # Restore the historical version of existing assets, while preserving any
    # files imported after that snapshot.
    project.assets = [copy.deepcopy(asset) for asset in project.assets]
    project.assets.extend(copy.deepcopy(asset) for asset in current.assets if asset.id not in history_assets)
    project.exports = copy.deepcopy(current.exports)
    project.revision = current.revision


def undo(pid):
    with LOCK:
        latest = next(_valid_history(pid), None)
        if latest is None:
            raise ValueError('Chưa có phiên bản hợp lệ để hoàn tác')
        history_path, restored = latest
        try:
            current = read(pid)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError):
            current = None
        if current is None:
            _preserve_corrupt_current(pid)
        _merge_history_assets(restored, current)
        saved = save(restored, history=False)
        history_path.unlink(missing_ok=True)
        return saved


def recover(pid):
    """Restore the newest valid history snapshot, preserving corrupt bytes."""
    with LOCK:
        try:
            read(pid)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError):
            pass
        else:
            raise ValueError('Dự án hiện vẫn đọc được; không cần phục hồi lịch sử')
        latest = next(_valid_history(pid), None)
        if latest is None:
            raise ValueError('Không có phiên bản lịch sử hợp lệ để phục hồi')
        _, restored = latest
        # Copy, never move, so a failed restore leaves the broken file intact.
        _preserve_corrupt_current(pid)
        restored.id = pid
        return save(restored, history=False)


def _stored_settings_raw():
    path = ROOT / 'settings.json'
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('settings.json phải là JSON object')
    return raw


def _stored_settings():
    raw = _stored_settings_raw()
    return Settings.model_validate(raw)


def settings():
    """Effective configuration: persisted values overlaid with environment secrets."""
    raw = _stored_settings_raw()
    s = Settings.model_validate(raw)
    openai_env = os.environ.get('OPENAI_API_KEY', '')
    azure_env = os.environ.get('AZURE_SPEECH_KEY', '')
    if not s.ai_key and openai_env:
        s.ai_key = openai_env
    if not s.azure_key and azure_env:
        s.azure_key = azure_env
    if not s.ai_profiles and s.ai_key:
        provider = 'openai' if s.ai_base_url.rstrip('/') == 'https://api.openai.com/v1' else 'compatible'
        s.ai_profiles = [AIProfile(id='legacy', name='API hiện có', provider=provider,
                                   api_key=s.ai_key, model=s.ai_model, base_url=s.ai_base_url)]
        s.active_ai_profile_id = 'legacy'
    for profile in s.ai_profiles:
        if profile.id == 'legacy' and not profile.api_key and openai_env:
            profile.api_key = openai_env
    return s


def _secret_sources(raw, effective):
    disk_profiles = {item.get('id'): item for item in raw.get('ai_profiles', []) if isinstance(item, dict)}
    sources = {
        'ai_key': 'saved' if raw.get('ai_key') else 'environment' if effective.ai_key and os.environ.get('OPENAI_API_KEY') else 'none',
        'azure_key': 'saved' if raw.get('azure_key') else 'environment' if effective.azure_key and os.environ.get('AZURE_SPEECH_KEY') else 'none',
        'ai_profiles': {},
    }
    for profile in effective.ai_profiles:
        disk_profile = disk_profiles.get(profile.id, {})
        if disk_profile.get('api_key'):
            source = 'saved'
        elif profile.id == 'legacy' and profile.api_key and os.environ.get('OPENAI_API_KEY'):
            source = 'environment'
        elif profile.id == 'legacy' and raw.get('ai_key'):
            source = 'saved'
        else:
            source = 'none'
        sources['ai_profiles'][profile.id] = source
    return sources


def public_settings():
    raw = _stored_settings_raw()
    effective = settings()
    data = effective.model_dump()
    for key in ('ai_key', 'azure_key'):
        data[key] = SECRET_MASK if data[key] else ''
    for profile in data['ai_profiles']:
        profile['api_key'] = SECRET_MASK if profile['api_key'] else ''
    data['secret_sources'] = _secret_sources(raw, effective)
    data['environment_available'] = {
        'ai': bool(os.environ.get('OPENAI_API_KEY')),
        'azure': bool(os.environ.get('AZURE_SPEECH_KEY')),
    }
    return data


def valid_api_url(url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ['https', 'http'] or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Địa chỉ API không hợp lệ')
    if parsed.scheme == 'http' and parsed.hostname not in ['localhost', '127.0.0.1', '::1']:
        raise ValueError('API từ xa cần HTTPS')


def save_settings(data):
    with LOCK:
        raw = _stored_settings_raw()
        current = Settings.model_validate(raw).model_dump()
        sources = data.get('secret_sources', {}) if isinstance(data, dict) else {}
        profile_sources = sources.get('ai_profiles', {}) if isinstance(sources, dict) else {}
        previous_profiles = {item['id']: item for item in current['ai_profiles']}
        for key, value in data.items():
            if key == 'secret_sources' or key == 'environment_available':
                continue
            if key in ('ai_key', 'azure_key') and value == SECRET_MASK:
                source = sources.get(key) if isinstance(sources, dict) else None
                if source == 'environment' and not current[key]:
                    current[key] = ''
                continue
            if key == 'ai_profiles':
                if not isinstance(value, list):
                    raise ValueError('Danh sách API không hợp lệ')
                profiles = []
                for item in value:
                    if not isinstance(item, dict):
                        raise ValueError('Cấu hình API không hợp lệ')
                    profile = dict(item)
                    if profile.get('api_key') == SECRET_MASK:
                        previous = previous_profiles.get(profile.get('id'))
                        source = profile_sources.get(profile.get('id')) if isinstance(profile_sources, dict) else None
                        if previous and previous.get('api_key'):
                            profile['api_key'] = previous['api_key']
                        elif profile.get('id') == 'legacy' and current['ai_key']:
                            profile['api_key'] = current['ai_key']
                        elif source == 'environment':
                            profile['api_key'] = ''
                        else:
                            raise ValueError('Khóa API mới cần được nhập')
                    profiles.append(profile)
                value = profiles
            current[key] = value
        if 'ai_profiles' in data:
            # A legacy key is moved into its profile once the UI saves profiles.
            current['ai_key'] = ''
        try:
            saved = Settings.model_validate(current)
        except ValidationError as exc:
            raise ValueError('Cài đặt API không hợp lệ. Kiểm tra tên, model, khóa và cấu hình ưu tiên.') from exc
        if not saved.ai_profiles and saved.ai_key and 'ai_profiles' not in data:
            provider = 'openai' if saved.ai_base_url.rstrip('/') == 'https://api.openai.com/v1' else 'compatible'
            saved.ai_profiles = [AIProfile(id='legacy', name='API hiện có', provider=provider,
                                           api_key=saved.ai_key, model=saved.ai_model,
                                           base_url=saved.ai_base_url)]
            saved.active_ai_profile_id = 'legacy'
        valid_api_url(saved.ai_base_url)
        for profile in saved.ai_profiles:
            if profile.provider == 'compatible':
                valid_api_url(profile.base_url)
        atomic(ROOT / 'settings.json', saved.model_dump())
        return public_settings()


def use_environment_secret(secret):
    """Explicitly move a legacy secret to its named environment variable."""
    with LOCK:
        raw = _stored_settings_raw()
        current = Settings.model_validate(raw).model_dump()
        if secret == 'ai':
            if not os.environ.get('OPENAI_API_KEY'):
                raise ValueError('OPENAI_API_KEY chưa được thiết lập trong môi trường')
            current['ai_key'] = ''
            profiles = current['ai_profiles']
            legacy = next((item for item in profiles if item['id'] == 'legacy'), None)
            if legacy is None:
                provider = 'openai' if current['ai_base_url'].rstrip('/') == 'https://api.openai.com/v1' else 'compatible'
                legacy = {'id': 'legacy', 'name': 'API môi trường', 'provider': provider,
                          'api_key': '', 'model': current['ai_model'],
                          'base_url': current['ai_base_url'], 'enabled': True}
                profiles.insert(0, legacy)
            else:
                legacy['api_key'] = ''
                legacy['name'] = 'API môi trường'
                legacy['enabled'] = True
            current['active_ai_profile_id'] = 'legacy'
        elif secret == 'azure':
            if not os.environ.get('AZURE_SPEECH_KEY'):
                raise ValueError('AZURE_SPEECH_KEY chưa được thiết lập trong môi trường')
            current['azure_key'] = ''
        else:
            raise ValueError('Loại khóa môi trường không hợp lệ')
        saved = Settings.model_validate(current)
        atomic(ROOT / 'settings.json', saved.model_dump())
        return public_settings()


def asset_path(pid, asset):
    directory = (project_dir(pid) / 'assets').resolve()
    path = (directory / asset.filename).resolve()
    if path.parent != directory:
        raise ValueError('Đường dẫn tư liệu không hợp lệ')
    return path


def get_asset(p, aid, include_deleted=False):
    asset = next((item for item in p.assets if item.id == aid and (include_deleted or not item.deleted)), None)
    if not asset:
        raise ValueError('Không tìm thấy tư liệu đang hoạt động')
    return asset


def acquire_instance_lock():
    """Hold an OS-level exclusive lock scoped to the configured data directory."""
    global _instance_handle
    with _instance_guard:
        if _instance_handle is not None:
            return
        ROOT.mkdir(parents=True, exist_ok=True)
        handle = (ROOT / 'instance.lock').open('a+b')
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise RuntimeError('Một ClipForge khác đang sử dụng thư mục dữ liệu này.') from exc
        _instance_handle = handle


def release_instance_lock():
    global _instance_handle
    with _instance_guard:
        if _instance_handle is None:
            return
        handle, _instance_handle = _instance_handle, None
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
