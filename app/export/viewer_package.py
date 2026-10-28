"""Stage, validate and atomically publish the portable, lazy CT viewer ZIP."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from app.export.theme import browser_theme

ASSETS = Path(__file__).parent
README = '''CT Viewer / نمایشگر سی‌تی

Extract the entire ZIP, then double-click viewer/index.html.
No Python, launcher, installation, server or directory selection is required.
Keep the HTML, assets/ and data/ together. You may move or rename the folder.
The viewer loads only the requested slices and advanced sections.
PNG files preserve the original numeric data. Their .png.js companions let
browsers read the same lossless bytes when opening index.html directly.
JSON metadata also has a .json.js companion for direct local opening.
For hosted viewing, serve the folder over HTTP/HTTPS and open index.html.

ZIP را کامل استخراج کنید و index.html را در مرورگر باز کنید.
به پایتون، اجراکننده، نصب برنامه، سرور یا انتخاب پوشه نیازی نیست.
فایل‌های پشتیبان را کنار index.html نگه دارید؛ تغییرنام و جابه‌جایی مجاز است.
تصاویر، اندازه‌گیری، سه‌نما و سطح‌ها فقط پس از درخواست بارگذاری می‌شوند.
ماسک اصلی تغییر نمی‌کند. پیش از بستن صفحه، نسخهٔ ویرایشی را دریافت کنید.
این نتیجهٔ خودکار نیازمند بازبینی است و اطلاعات منبع را حفظ می‌کند.
'''


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


def write_companion(root, path, value):
    encoded=json.dumps(value, ensure_ascii=True, separators=(',', ':'))
    (root/(path+'.js')).write_text('window.ViewerLoader.register('+json.dumps(path)+','+encoded+');\n', encoding='utf-8')


def validate_package(root):
    """Every packaged data/script/style reference must exist before publication."""
    required = ['index.html', 'data/manifest.json', 'data/measurements.json', 'README.txt']
    required += ['assets/' + name for name in ('viewer.css', 'native-viewer.js', 'lazy-loader.js',
                 'advanced-viewer.js', 'surface-viewer.js', 'mask-worker.js')]
    def references(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from references(item)
        elif isinstance(value, list):
            for item in value:
                yield from references(item)
        elif isinstance(value, str) and value.startswith(('data/', 'assets/')):
            yield value
    for path in (root/'data').rglob('*'):
        if path.suffix in ('.json', '.png'):
            required.append(str(path.relative_to(root))+'.js')
    if any(path.suffix.lower() in ('.py', '.bat', '.sh', '.cmd', '.ps1') for path in root.rglob('*')):
        raise ValueError('UNEXPECTED_VIEWER_LAUNCHER')
    for path in (root/'data').glob('*.json'):
        required += list(references(json.loads(path.read_text(encoding='utf-8'))))
    html = (root/'index.html').read_text(encoding='utf-8')
    required += re.findall(r'(?:src|href)="((?:assets|data)/[^"#]+)"', html)
    if any(token in html for token in ('data:image/', 'webkitdirectory', '__EXTENSION_', '__DATA__')):
        raise ValueError('INVALID_VIEWER_ENTRYPOINT')
    for name in required:
        path = root/name
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('INCOMPLETE_VIEWER_PACKAGE: ' + name)


def build_viewer_package(payload, directory):
    """Separate encoded data without changing numeric PNG bytes or precision.

    All work happens in a private temporary directory. An existing result is
    replaced only after reference validation, ZIP CRC validation and closure.
    """
    directory = Path(directory)
    result_id = re.sub(r'[^A-Za-z0-9_-]+', '-', str(payload['job_id'])).strip('-')[:80] or 'result'
    destination = directory / ('ct-viewer-' + result_id + '.zip')
    with tempfile.TemporaryDirectory(prefix='.viewer-', dir=directory) as staging:
        stage = Path(staging)
        root = stage/'viewer'
        (root/'assets').mkdir(parents=True)
        (root/'data').mkdir()
        manifest = copy.deepcopy(payload)
        def extract(encoded, name):
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded, validate=True))
            write_companion(root, name, encoded)
            return name
        for field, folder in [('frames', 'ct'), ('overlays', 'masks')]:
            manifest[field] = [extract(value, f'data/{folder}/{i:04d}.png')
                               for i, value in enumerate(manifest[field])]
        pre = manifest.pop('precomputed')
        measurement_rows = pre.pop('measurements') if pre else {}
        write_json(root/'data/measurements.json', measurement_rows)
        if pre:
            for kind, plane in pre['mpr']['planes'].items():
                plane['frames'] = [extract(value, f'data/mpr/{kind}/{i:04d}.png')
                                   for i, value in enumerate(plane['frames'])]
            pre['surfaces']['structures'] = {key: {field: row[field] for field in ('count', 'lower', 'upper')}
                                             for key, row in measurement_rows.items()}
            for key, mesh in pre['surfaces']['meshes'].items():
                if 'png' in mesh:
                    mesh['png'] = extract(mesh['png'], f'data/surfaces/{key}.png')
            write_json(root/'data/advanced.json', pre)
            manifest['precomputed'] = {'schema_version': 1, 'metadata_url': 'data/advanced.json',
                                       'measurements_url': 'data/measurements.json'}
        else:
            manifest['precomputed'] = None
        write_json(root/'data/manifest.json', manifest)
        html = (ASSETS/'viewer_template.html').read_text().replace(
            '__EXTENSION_HTML__', (ASSETS/'viewer_extension.html').read_text())
        (root/'index.html').write_text(html, encoding='utf-8')
        (root/'assets/viewer.css').write_text(browser_theme()+'\n'+(ASSETS/'viewer_extension.css').read_text(), encoding='utf-8')
        for source, target in [('viewer_native.js','native-viewer.js'),
                               ('viewer_lazy_loader.js','lazy-loader.js'),
                               ('viewer_extension.js','advanced-viewer.js'),
                               ('viewer_surface.js','surface-viewer.js'),
                               ('viewer_worker.js','mask-worker.js')]:
            (root/'assets'/target).write_bytes((ASSETS/source).read_bytes())
        for path in (root/'data').glob('*.json'):
            write_companion(root, str(path.relative_to(root)), json.loads(path.read_text(encoding='utf-8')))
        (root/'README.txt').write_text(README, encoding='utf-8')
        validate_package(root)
        candidate = stage/'complete.zip'
        with zipfile.ZipFile(candidate, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(stage), compress_type=(
                        zipfile.ZIP_STORED if path.suffix == '.png' else zipfile.ZIP_DEFLATED))
        with zipfile.ZipFile(candidate) as archive:
            if archive.testzip() is not None:
                raise ValueError('INVALID_VIEWER_ZIP')
        candidate.chmod(0o600)
        os.replace(candidate, destination)
    return destination
