"""Portable template packages and safe cross-project image remapping."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

from . import media, store
from .models import Project, Template, uid


class MissingTemplateAssets(ValueError):
    def __init__(self, missing):
        self.missing = missing
        super().__init__('Template cần chọn ảnh thay thế cho logo/tài nguyên bị thiếu.')


def _image_refs(template: Template) -> list[str]:
    layers = list(template.layers)
    for group in template.slot_layers:
        layers.extend(group)
    return list(dict.fromkeys(layer.asset_id for layer in layers
                              if layer.kind == 'image' and layer.asset_id))


def _remap(template: Template, mapping: dict[str, str]) -> Template:
    result = template.model_copy(deep=True)
    groups = [result.layers, *result.slot_layers]
    for group in groups:
        for layer in group:
            if layer.kind == 'image' and layer.asset_id:
                layer.asset_id = mapping[layer.asset_id]
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_index():
    found = {}
    for item in store.projects():
        if item.get('corrupt'):
            continue
        try:
            project = store.read(item['id'])
        except (OSError, ValueError, TypeError, ValidationError):
            continue
        for asset in project.assets:
            if asset.media == 'image' and not asset.deleted:
                path = store.asset_path(project.id, asset)
                if path.is_file():
                    found.setdefault(asset.id, (project, asset, path))
    return found


def _unresolved(template: Template, resources: dict, index: dict) -> list[dict]:
    missing = []
    for asset_id in _image_refs(template):
        if asset_id in resources:
            resource = resources[asset_id]
            root = resource.get('_root')
            filename = resource.get('filename', '')
            path = (root / filename).resolve() if root and filename else None
            if path and path.parent == (root / 'assets').resolve() and path.is_file():
                if not resource.get('sha256') or _hash(path) == resource['sha256']:
                    continue
        if asset_id in index:
            continue
        missing.append({'id': asset_id, 'name': resources.get(asset_id, {}).get('name', 'Logo / biểu tượng')})
    return missing


def list_packages() -> list[dict]:
    root = store.ROOT / 'templates'
    root.mkdir(parents=True, exist_ok=True)
    index = _asset_index()
    result = []
    for directory in root.iterdir():
        if not directory.is_dir() or not (directory / 'manifest.json').is_file():
            continue
        try:
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            template = Template.model_validate_json((directory / 'template.json').read_text(encoding='utf-8'))
            resources = manifest.get('resources', {})
            for record in resources.values():
                record['_root'] = directory
            missing = _unresolved(template, resources, index)
            result.append({'id': manifest['id'], 'name': template.name, 'template': template,
                           'saved': True, 'built_in': False, 'missing_assets': missing})
        except (OSError, ValueError, TypeError, KeyError, ValidationError, json.JSONDecodeError):
            continue
    for path in root.glob('*.json'):
        try:
            template = Template.model_validate_json(path.read_text(encoding='utf-8'))
            missing = _unresolved(template, {}, index)
            result.append({'id': template.id, 'name': template.name, 'template': template,
                           'saved': True, 'built_in': False, 'legacy': True,
                           'missing_assets': missing})
        except (OSError, ValueError, TypeError, ValidationError):
            continue
    return sorted(result, key=lambda item: item['name'].casefold())


def save_package(project: Project, template: Template) -> dict:
    template = Template.model_validate(template.model_dump())
    package_id = uid()
    root = store.ROOT / 'templates'
    root.mkdir(parents=True, exist_ok=True)
    final = root / package_id
    temporary = Path(tempfile.mkdtemp(prefix=f'.{package_id}.', dir=root))
    try:
        assets_dir = temporary / 'assets'
        assets_dir.mkdir()
        resources = {}
        for asset_id in _image_refs(template):
            asset = store.get_asset(project, asset_id)
            if asset.media != 'image':
                raise ValueError(f'Template image {asset_id} không trỏ tới ảnh đang hoạt động.')
            source = store.asset_path(project.id, asset)
            if not source.is_file():
                raise FileNotFoundError(f'Thiếu file logo/ảnh {asset.name}.')
            filename = f'{asset_id}{source.suffix.lower()}'
            destination = assets_dir / filename
            shutil.copyfile(source, destination)
            resources[asset_id] = {
                'name': asset.name, 'filename': f'assets/{filename}',
                'sha256': _hash(destination), 'media': 'image',
            }
        (temporary / 'template.json').write_text(
            json.dumps(template.model_dump(), ensure_ascii=False, indent=2), encoding='utf-8')
        manifest = {'schema_version': 1, 'id': package_id, 'name': template.name,
                    'resources': resources}
        (temporary / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, final)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return {'id': package_id, 'name': template.name, 'template': template,
            'saved': True, 'built_in': False, 'missing_assets': []}


def _load_package(package_id: str):
    root = store.ROOT / 'templates'
    package_dir = root / package_id
    if package_dir.is_dir() and (package_dir / 'manifest.json').is_file():
        manifest = json.loads((package_dir / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('schema_version') != 1 or manifest.get('id') != package_id:
            raise ValueError('Manifest template không hợp lệ.')
        template = Template.model_validate_json((package_dir / 'template.json').read_text(encoding='utf-8'))
        resources = manifest.get('resources', {})
        for record in resources.values():
            relative = Path(record.get('filename', ''))
            if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != 'assets':
                raise ValueError('Đường dẫn tài nguyên template không hợp lệ.')
            path = (package_dir / relative).resolve()
            if path.parent != (package_dir / 'assets').resolve():
                raise ValueError('Đường dẫn tài nguyên template không hợp lệ.')
            record['_path'] = path if path.is_file() and _hash(path) == record.get('sha256') else None
        return template, resources, None
    legacy_path = root / f'{package_id}.json'
    if legacy_path.is_file():
        template = Template.model_validate_json(legacy_path.read_text(encoding='utf-8'))
        return template, {}, _asset_index()
    raise FileNotFoundError('Không tìm thấy template đã lưu.')


def apply_package(project: Project, package_id: str, asset_map: dict[str, str] | None = None) -> Project:
    if not re.fullmatch(r'[a-f0-9]{16}', package_id):
        raise ValueError('ID template không hợp lệ.')
    asset_map = asset_map or {}
    template, resources, legacy_assets = _load_package(package_id)
    references = _image_refs(template)
    mapping = {}
    copies = []
    registered = []
    project_assets = {asset.id: asset for asset in project.assets}
    source_index = legacy_assets if legacy_assets is not None else _asset_index()
    try:
        unresolved = []
        for old_id in references:
            replacement = asset_map.get(old_id)
            if replacement:
                candidate = project_assets.get(replacement)
                if not candidate or candidate.deleted or candidate.media != 'image':
                    raise ValueError(f'Ảnh thay thế {replacement} không phải logo/ảnh đang hoạt động trong dự án.')
                mapping[old_id] = replacement
                continue
            record = resources.get(old_id)
            source_path = record.get('_path') if record else None
            name = record.get('name', 'Logo template') if record else 'Logo template'
            if source_path is None and old_id in source_index:
                _source_project, source_asset, source_path = source_index[old_id]
                name = source_asset.name
            if source_path is None or not Path(source_path).is_file():
                unresolved.append({'id': old_id, 'name': name})
                continue
            suffix = Path(source_path).suffix.lower() or '.png'
            destination = store.project_dir(project.id) / 'assets' / f'{uid()}{suffix}'
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            copies.append(destination)
            new_asset = media.register(project.id, destination, name, 'overlay')
            registered.append(new_asset)
            mapping[old_id] = new_asset.id
        if unresolved:
            raise MissingTemplateAssets(unresolved)
        project.assets.extend(registered)
        project.template = _remap(template, mapping)
        project.mode = 'template'
        return store.save(project)
    except BaseException:
        for path in copies:
            path.unlink(missing_ok=True)
        for asset in registered:
            if asset.thumbnail:
                (store.project_dir(project.id) / 'thumbs' / asset.thumbnail).unlink(missing_ok=True)
        raise
