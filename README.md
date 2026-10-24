# TotalSegmentator GPU API

Upload a ZIP containing CT DICOM files and receive a complete, standalone **`viewer.html`** file. The service runs TotalSegmentator on an NVIDIA GPU and supports all **117 regions** in the CT `total` task: organs, vessels, muscles and bones.

The direct-upload workflow uses this server's GPU and temporary disk space. It does **not** use PostgreSQL, Redis or MinIO. Your main application can receive the HTML response and store it wherever you choose.

## What the output looks like

![Standalone CT viewer with multiple selected regions, slice controls, windowing and volume information](docs/images/viewer.png)

*Screenshot of the actual HTML viewer, populated with a synthetic phantom and illustrative masks. It contains no patient data and does not demonstrate model accuracy. The clinical interface is currently Persian and right-to-left; this guide is in English.*

The response is one HTML file containing:

- CT slices and segmentation labels embedded inside the file.
- Multiple organ checkboxes, search, select all/none, and jump to an organ's slice.
- Slice navigation and cine playback.
- Soft-tissue, lung, bone and brain window presets, manual HU windowing and inversion.
- Zoom, pan, fullscreen, an in-plane millimetre ruler and a slice snapshot button.
- Overlay opacity, mask contours, region volumes and voxel spacing.
- Embedded CSS from [`theme.css`](theme.css), including the available theme palettes.

Open the downloaded file in a browser. It works offline without this API, the original project, external JavaScript or a GPU. This version displays **axial slices**, not three-plane reconstruction or a 3D surface viewer. Regions outside the scan's coverage will not have nonempty masks. Automatic segmentation requires clinical review.

## Quick launch on the prepared GPU server

The current server already has the Python environment, model weights and private configuration. From the repository directory:

```bash
export SEG_PYTHON="$HOME/Music/whisper/.venv/bin/python"
bash run-native.sh check-local
bash run-native.sh start-local
```

`check-local` verifies checkpoint hashes, the installed model version, CUDA execution and imports. `start-local` launches the API and local GPU worker as **systemd user services**. They continue running after the terminal closes. Automatic startup after reboot is not configured; run `start-local` again after signing in.

| Address | Purpose |
| --- | --- |
| `http://127.0.0.1:8096/docs` | Swagger UI on the GPU server |
| `http://<GPU_SERVER_IP>:8096/docs` | Swagger UI from another machine |
| `http://<GPU_SERVER_IP>:8096/v1/upload/` | ZIP upload; returns the final HTML |
| `http://<GPU_SERVER_IP>:8096/health/live` | API liveness |

Use the server's LAN IP, shown by `hostname -I`, in place of `<GPU_SERVER_IP>`. `0.0.0.0` is a listening address, not the address to enter on another machine.

For LAN access, set these values in the local **`.env`** file:

```dotenv
SEG_API_HOST=0.0.0.0
SEG_API_PORT=8096
SEG_GPU_DEVICE=0
```

Restart the API after changing its host or port:

```bash
systemctl --user restart sharif-segmentation-gpu-api.service
```

The example configuration defaults to `127.0.0.1` for local access. The prepared server has been configured to listen on `0.0.0.0`.

### Service management

```bash
# Check the two services used by direct uploads.
systemctl --user is-active \
  sharif-segmentation-gpu-api.service \
  sharif-segmentation-gpu-local-worker.service

# View local-worker logs.
journalctl --user -u sharif-segmentation-gpu-local-worker.service -n 100 --no-pager

# Check API liveness without contacting external services.
curl --fail http://127.0.0.1:8096/health/live

# Stop only the direct-upload API and worker.
systemctl --user stop \
  sharif-segmentation-gpu-api.service \
  sharif-segmentation-gpu-local-worker.service
```

`/health/ready` and `run-native.sh status` also check the connected clinic database/workflow. Use `/health/live` and the two service checks above for an independent direct-upload deployment.

## Setup on a new GPU server

### 1. Clone and prepare Python

Use Linux with systemd user services, Python 3.12 and an NVIDIA driver that supports your CUDA-enabled PyTorch installation. The existing deployment has been exercised on an RTX 3060 with 12 GB VRAM; processing time and memory usage depend on the scan.

```bash
git clone https://github.com/shosseinj/total-segmentator.git
cd total-segmentator
```

If you already have a prepared environment, set `SEG_PYTHON` to its Python executable. On the current GPU server, use `$HOME/Music/whisper/.venv/bin/python`; no dependency installation is needed there.

For a **new environment only**:

```bash
python3.12 -m venv .venv
export SEG_PYTHON="$PWD/.venv/bin/python"
"$SEG_PYTHON" -m pip install -r requirements.lock -r requirements-inference.txt
"$SEG_PYTHON" -c 'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
```

The inference requirements pin TotalSegmentator **2.11.0**, nnU-Net **2.6.2** and PyTorch **2.8.0**. An existing compatible CUDA PyTorch installation can be used, as on the prepared server. Match your PyTorch CUDA build to the installed NVIDIA driver; runtime inference requires CUDA and does not silently fall back to CPU.

### 2. Provision model weights

Weights are **not stored in Git**. The required layout is:

```text
models/
├── checkpoint-manifest.json
└── nnunet/results/
    ├── Dataset291_...  # standard: organs
    ├── Dataset292_...  # standard: vertebrae
    ├── Dataset293_...  # standard: cardiac/vessels
    ├── Dataset294_...  # standard: muscles
    ├── Dataset295_...  # standard: ribs
    ├── Dataset297_...  # fast total model
    └── Dataset298_...  # coarse localization used by subsets
```

Copy an approved prepared `models/` directory, including its manifest, or download the CT weights once using the pinned package. **Skip the following command when the models and manifest are already installed.** This setup download requires Internet access; the inference service itself disables model downloads and usage telemetry.

```bash
TOTALSEG_WEIGHTS_PATH="$PWD/models/nnunet/results" \
TOTALSEG_DISABLE_USAGE_STATS=1 \
"$SEG_PYTHON" - <<'PY'
import hashlib
import importlib.metadata
import json
from pathlib import Path
from totalsegmentator.libs import download_pretrained_weights

assert importlib.metadata.version('TotalSegmentator') == '2.11.0'
for task_id in (291, 292, 293, 294, 295, 297, 298):
    download_pretrained_weights(task_id)

root = Path('models')
entries = {}
for path in sorted((root / 'nnunet/results').rglob('*')):
    if path.is_file():
        with path.open('rb') as stream:
            entries[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
assert entries, 'No model files found'
(root / 'checkpoint-manifest.json').write_text(json.dumps(entries, sort_keys=True, indent=2) + '\n')
PY
```

The manifest records the exact model-file checksums. The private configuration stores the SHA-256 of that manifest, and the runtime verifies the files before inference.

### 3. Create local configuration

```bash
bash run-native.sh init
```

This creates `.env` if it does not exist. Configure its API host, port and GPU as described above.

The API currently shares its settings schema with the optional clinic workflow, so `connection.env` must contain the database, Redis and S3 settings even for direct uploads. **Local uploads never connect to these services**; standalone installations can use the unused addresses below. Keep an existing clinic-supplied `connection.env` instead of replacing it.

For a fresh standalone installation, this command creates a private file with a random service token and the correct manifest digest. It refuses to overwrite an existing file:

```bash
"$SEG_PYTHON" - <<'PY'
import hashlib
import os
from pathlib import Path
import secrets

values = {
    'SEG_DATABASE_URL': 'postgresql+psycopg://unused:unused@127.0.0.1:15432/unused',
    'SEG_REDIS_URL': 'redis://127.0.0.1:16379/0',
    'SEG_SERVICE_TOKEN': secrets.token_urlsafe(48),
    'SEG_S3_ENDPOINT': 'http://127.0.0.1:19000',
    'SEG_S3_ACCESS_KEY': 'unused',
    'SEG_S3_SECRET_KEY': 'unused',
    'SEG_INPUT_BUCKET': 'unused-input',
    'SEG_OUTPUT_BUCKET': 'unused-output',
    'SEG_TENANT_ID': 'local-clinic',
    'SEG_SOURCE_ID': 'local-upload',
    'SEG_CHECKPOINT_DIGEST': hashlib.sha256(Path('models/checkpoint-manifest.json').read_bytes()).hexdigest(),
    'SEG_SWAGGER_AUTO_AUTHORIZE': 'false',
}
fd = os.open('connection.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
PY
```

Treat the file as literal configuration data: **do not `source` it**. The native runner maps scratch space to `work/` and the model cache to `models/` automatically. Then run the quick-launch commands above.

## Upload from Swagger

Swagger includes links to view or download a sample above the form and in the `/v1/upload/` response documentation. The sample is a complete interactive HTML viewer built with synthetic voxels and illustrative masks; it contains no patient data or model predictions. You can open it without uploading a ZIP or running inference.

1. Open `/docs` and expand **POST `/v1/upload/`**.
2. If automatic authorization is disabled, click **Authorize** and paste `SEG_SERVICE_TOKEN` from your private configuration, without the `Bearer` prefix.
3. Click **Try it out**, select a ZIP, keep `structures=all` and `series_index=-1`, then click **Execute**.
4. Watch the processing stages above the Swagger form. The finished `viewer.html` downloads automatically; open it from your browser's downloads folder.

For temporary testing, `SEG_SWAGGER_AUTO_AUTHORIZE=true` makes Swagger fill the token automatically. This exposes the token to anyone who can open that docs page. Keep it `false` for normal integration and restart the API after changing it. API clients always need a Bearer token regardless of this UI setting.

The actual HTTP response is **HTML**. Swagger displays a small JSON download receipt to avoid rendering a large embedded CT file in its response panel; that receipt is not the API's wire response.

After changing the viewer, regenerate the bundled sample with `"$SEG_PYTHON" -m tools.generate_viewer_demo --swagger-sample --output app/web/samples`.

To download the manually saved `exports/dicom-viewer.html` from Swagger, expand **GET `/v1/exports/dicom-viewer.html`** under **Saved results**, then click **Try it out → Execute → Download file**. This endpoint requires the service token and serves the saved file without rerunning inference. Swagger receives a binary download so the complete HTML is not rendered in its response panel. The endpoint returns `404` if that saved file is absent; uploads still do not create a result history automatically.

## Call the API from your application

```bash
read -rsp 'Service token: ' SEG_SERVICE_TOKEN
printf '\n'
curl --fail --show-error --max-time 3700 \
  'http://<GPU_SERVER_IP>:8096/v1/upload/' \
  -H "Authorization: Bearer $SEG_SERVICE_TOKEN" \
  -F 'file=@/path/to/ct-dicom.zip;type=application/zip' \
  -F 'structures=all' \
  -F 'quality=standard' \
  -F 'series_index=-1' \
  -F 'wait_timeout=3600' \
  --output viewer.html
unset SEG_SERVICE_TOKEN
```

This is a `multipart/form-data` request. A successful response has:

```http
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
Content-Disposition: attachment; filename="viewer.html"
Cache-Control: no-store
X-Job-Id: <temporary-request-id>
X-Selected-Series-Index: <selected-index>
X-Selected-Series-Slices: <slice-count>
```

For example, a Python backend can save the response without loading it all into client memory:

```python
import os
from pathlib import Path
import httpx

with Path('ct-dicom.zip').open('rb') as source:
    with httpx.stream(
        'POST',
        'http://<GPU_SERVER_IP>:8096/v1/upload/',
        headers={'Authorization': 'Bearer ' + os.environ['SEG_SERVICE_TOKEN']},
        files={'file': ('ct-dicom.zip', source, 'application/zip')},
        data={'structures': 'all', 'series_index': '-1', 'wait_timeout': '3600'},
        timeout=3700,
    ) as response:
        response.raise_for_status()
        with Path('viewer.html').open('wb') as target:
            for chunk in response.iter_bytes():
                target.write(chunk)
```

Store the returned HTML in your own application or MinIO if you need permanent storage. Configure any reverse proxy/client with a timeout long enough for inference; the upload response waits for completion.

### Request options

| Field | Default | Meaning |
| --- | --- | --- |
| `file` | Required | ZIP of DICOM files; nested folders are supported |
| `structures` | `all` | All 117 supported regions, or comma-separated names such as `liver,spleen` |
| `quality` | `standard` | `standard` or the lower-resolution `fast` mode |
| `series_index` | `-1` | Automatically choose the eligible CT series with most slices; ties use the lowest index. `0` and higher select an explicit series |
| `wait_timeout` | `3600` | HTTP wait in seconds; allowed range is 1–86400 |

`GET /v1/uploads/capabilities` lists supported region names. The default ZIP limit is 10 GiB and the extracted-size limit is 30 GiB; server limits are configurable through `SEG_MAX_ARCHIVE`, `SEG_MAX_EXPANDED`, `SEG_MAX_ENTRIES` and `SEG_MAX_RATIO` in `connection.env`.

Only `Authorization: Bearer <token>` is required for local uploads. Tenant and service-actor context are derived automatically. An optional `Idempotency-Key` prevents concurrent duplicate requests with the same key; completed results are not retained for replay.

### Progress, retention and errors

The pipeline receives the ZIP, inventories DICOM series, waits for the shared GPU lock, runs the model, aligns masks and builds the standalone viewer. Swagger polls `/v1/upload/progress` with its automatically generated request key. An authenticated `GET /v1/uploads/{id}` can report a request while it still exists.

**No result history or mask cache is retained.** Before sending a successful response, the API reads the HTML into memory and deletes the entire request directory: original ZIP, extracted DICOM, CT, masks, model intermediates and HTML. There are no companion ZIP, SEG, NIfTI or JSON downloads in this mode. Model weights remain installed. A new upload runs inference again.

Failure also triggers cleanup. Queued timeouts/disconnections remove the input immediately; active inference is marked for cancellation and cleaned after the child exits. The model cancellation guard checks at most every 30 seconds. Worker/API recovery also reaps orphaned temporary requests. After cleanup, the request status returns `404`.

| HTTP status / code | Meaning / next step |
| --- | --- |
| `401 UNAUTHORIZED` | Supply the configured service token |
| `409 UPLOAD_IN_PROGRESS` | A request with that key is still active |
| `413 ARCHIVE_LIMIT` | The archive exceeds configured limits |
| `422 CORRUPT_ZIP` / `EMPTY_ZIP` | Supply a valid nonempty ZIP |
| `422 NO_ELIGIBLE_CT_SERIES` | The archive has no eligible 3D CT series |
| `422 ITEM_NOT_ELIGIBLE` | Try `series_index=-1` or a valid explicit index |
| `422 MODEL_FAILED` / `GPU_OOM` | Check the local worker, installed models and GPU resources |
| `504 RESULT_WAIT_TIMEOUT` | The wait expired; work is cancelled/cleaned. Re-upload to try again |

Errors return JSON, not an incomplete HTML file. `POST /v1/uploads` is a compatibility alias that also returns the final HTML; it no longer returns an asynchronous JSON result link.

## Optional connected-clinic workflow

The repository also contains the original `/v1/scans`, `/v1/batches` and `/v1/jobs` workflow. It uses the existing clinic's PostgreSQL, Redis and MinIO, with an SSH tunnel when needed. It is separate from direct ZIP uploads and retains its tenant/actor/operator header requirements.

For that deployment, configure `CLINIC_SSH_TARGET` and `CLINIC_ROOT` in `.env` or the runner environment, provision SSH key access, and use the clinic's export/handoff process before `run-native.sh start`. `start` runs connection checks and migrations and requires a valid `handoff.json`; **use `start-local` for the independent upload API**. `run.sh` and `compose.gpu.yaml` cover the original clinic Docker deployment and do not currently launch the local-upload worker.

## Development checks

```bash
"$SEG_PYTHON" -m unittest \
  tools.test_uploads tools.test_api_docs tools.test_preview \
  tools.test_measurements tools.test_native

# Optional browser integration checks; Firefox is required.
"$SEG_PYTHON" -m tools.check_swagger_browser
"$SEG_PYTHON" -m tools.check_upload_browser
"$SEG_PYTHON" -m tools.check_upload_browser --auto-authorize

# Recreate the documentation screenshot; Chromium is required.
"$SEG_PYTHON" -m tools.render_readme_preview
```

The screenshot tool creates synthetic CT/masks, renders the actual standalone viewer and retains only `docs/images/viewer.png`. It does not run GPU inference or use patient files.

Private configuration, installed models, temporary medical data, generated result directories, caches and deployment receipts are excluded by [`.gitignore`](.gitignore). Upstream packages and weights retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
