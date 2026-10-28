"""Generate API and source-result packages, verify numeric assets and direct-opening assets."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from app.export.preview import generate_preview
from tools.repackage_viewer import repackage
from tools.test_uploads import UploadTests

ROOT=Path('work/viewer-package-verification').resolve()


def verify_legacy(source, package):
    payload, _=json.JSONDecoder().raw_decode(source.read_text().split('const data=',1)[1])
    assets=[]
    with zipfile.ZipFile(package) as archive:
        manifest=json.loads(archive.read('viewer/data/manifest.json'))
        for field in ('frames','overlays'):
            for encoded,name in zip(payload[field],manifest[field],strict=True):
                assert base64.b64decode(encoded)==archive.read('viewer/'+name)
                assets.append(name)
        pre=json.loads(archive.read('viewer/data/advanced.json'))
        assert json.loads(archive.read('viewer/data/measurements.json'))==payload['precomputed']['measurements']
        for kind,plane in payload['precomputed']['mpr']['planes'].items():
            for encoded,name in zip(plane['frames'],pre['mpr']['planes'][kind]['frames'],strict=True):
                assert base64.b64decode(encoded)==archive.read('viewer/'+name)
                assets.append(name)
        for key,mesh in payload['precomputed']['surfaces']['meshes'].items():
            if 'png' in mesh:
                name=pre['surfaces']['meshes'][key]['png']
                assert base64.b64decode(mesh['png'])==archive.read('viewer/'+name)
                assets.append(name)
        for name in assets:
            companion=archive.read('viewer/'+name+'.js').decode()
            key,value=json.loads('['+companion.removeprefix('window.ViewerLoader.register(').removesuffix(');\n')+']')
            assert key==name and base64.b64decode(value)==archive.read('viewer/'+name)
        for name in ('manifest.json','measurements.json','advanced.json'):
            companion=archive.read('viewer/data/'+name+'.js').decode()
            key,value=json.loads('['+companion.removeprefix('window.ViewerLoader.register(').removesuffix(');\n')+']')
            assert key=='data/'+name and value==json.loads(archive.read('viewer/data/'+name))
        for field in ('geometry','source_identity','original_fingerprint','hu_offset','hu_step','labels','positions'):
            assert payload[field]==manifest[field],field
        assert archive.testzip() is None
        return {'numeric_pngs_compared_byte_for_byte':len(assets),'measurements_exact':True,'direct_open_companions_match_originals':True,
                'geometry_identity_fingerprint_encoding_unchanged':True,'zip_integrity':True,
                'index_bytes':len(archive.read('viewer/index.html')),'zip_bytes':package.stat().st_size}


def main():
    ROOT.mkdir(exist_ok=True,parents=True)
    fixture=UploadTests();fixture.setUp()
    try:
        with fixture.local_worker():response=fixture.upload()
        assert response.status_code==200
        (ROOT/'server-upload.zip').write_bytes(response.content)
        fixture.assert_clean();fixture.tearDown()
        report={'upload':{'status':response.status_code,'content_type':response.headers['content-type'],
            'content_disposition':response.headers['content-disposition'],
            'inference':'synthetic deterministic stub; actual upload endpoint, job worker and generator',
            'minio_calls':len(fixture.store.mock_calls),'database_calls':len(fixture.repository.mock_calls)}}
    finally:fixture.doCleanups()
    known=Path('work/viewer-verification/known')
    artifact=generate_preview(known/'ct.nii.gz',known/'masks.nii.gz',ROOT,
        {'1':'kidney_left','2':'kidney_right','3':'liver','4':'spleen','117':'unused'},standalone_only=True)
    source=Path('exports/dicom-viewer.html')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    package=repackage(source,Path('exports'))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    with tempfile.NamedTemporaryFile(dir='exports',prefix='.saved-viewer-',delete=False) as temporary:
        candidate=Path(temporary.name)
    try:
        shutil.copyfile(package,candidate);candidate.chmod(0o600)
        os.replace(candidate,'exports/dicom-viewer.zip')
    finally:candidate.unlink(missing_ok=True)
    report['existing_server_result']=verify_legacy(source,package)
    report['existing_server_result']['original_html_sha256_unchanged']=True
    for archive_path,name in [(ROOT/'server-upload.zip','server upload'),(ROOT/artifact['viewer'],'known'),(package,'جابه جایی scan')]:
        destination=ROOT/name
        if destination.exists():shutil.rmtree(destination)
        with zipfile.ZipFile(archive_path) as archive:archive.extractall(destination)
    with zipfile.ZipFile(package) as archive:
        report['no_launchers_or_python']=not any(Path(name).suffix in ('.py','.bat','.sh','.cmd','.ps1') for name in archive.namelist())
        assert report['no_launchers_or_python']
    (ROOT/'verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
