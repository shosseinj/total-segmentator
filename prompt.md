Update the existing server-side viewer generator. (/v1/upload/)

Currently, the server generates one large, self-contained HTML file containing all CT images, segmentation masks, measurements, reconstructed views, and 3D surfaces.

Change its output to a ZIP package containing a lightweight HTML viewer and separate supporting files. Preserve the existing viewer features and load data only when needed.

## 1. Scope

- Locate and modify the actual viewer generator and its templates/assets.
- Apply this change to future generated results, not just one exported HTML.
- Preserve TotalSegmentator, inference, approval, job processing, authentication, and existing MinIO configuration.
- Do not change MinIO credentials, endpoints, bucket configuration, or original input files.
- Reuse the existing result-generation and download workflow.
- Do not rerun inference solely to repackage an existing result.

The intended change is the generated artifact and its loading mechanism.

## 2. ZIP structure

Generate a package with a structure similar to:

viewer/
  index.html
  assets/
    viewer.css
    native-viewer.js
    lazy-loader.js
    advanced-viewer.js
    surface-viewer.js
    mask-worker.js
  data/
    manifest.json
    measurements.json
    ct/
      0000.png
      0001.png
      ...
    masks/
      0000.png
      0001.png
      ...
    mpr/
      axial/
      coronal/
      sagittal/
    surfaces/
      <structure-id>.png
      ...
  START_VIEWER.py
  START_VIEWER.bat
  START_VIEWER.sh
  README.txt

Adapt filenames to the existing implementation when appropriate. Include every required dependency and data file.

Do not include the original large HTML inside the ZIP.

## 3. No folder selection

The HTML must never ask the doctor to select, locate, or upload its supporting directory.

- Remove directory pickers, folder-selection dialogs, webkitdirectory inputs, and manual asset-location settings.
- Resolve every supporting file relative to index.html.
- Use document.baseURI or an equivalent reliable mechanism.
- Do not use absolute filesystem paths, developer-machine paths, root-relative application paths, or expiring URLs inside the package.
- The package must continue working when its parent directory is renamed or moved.
- Support paths containing spaces and non-English characters.

All required files are already inside the extracted package.

## 4. Supported opening methods

### Hosted viewing

When the extracted directory is served over HTTP or HTTPS, opening index.html must load its sibling assets automatically.

No folder chooser or manual configuration is permitted.

### Local viewing

Include launchers that:

- Determine their own directory automatically.
- Serve that directory using Python 3’s standard library.
- Bind only to 127.0.0.1.
- Choose an available port.
- Automatically open index.html in the default browser.
- Require no package installation and no folder-selection step.

If index.html is opened directly using file:// and browser restrictions prevent loading its supporting files, show a short instruction to run the included launcher.

Do not bypass browser security, require insecure browser flags, or pretend that an HTML page can start a local server by itself.

## 5. Lazy-loading behavior

Initial loading must retrieve only:

- The viewer interface and lightweight manifest.
- One initial CT slice.
- Its corresponding segmentation mask.

Do not load measurements, reconstructed images, 3D surfaces, or the entire volume at startup.

Afterward:

- Slice navigation loads only the requested CT slice and mask.
- Opening measurements loads measurements.json.
- Opening the three-plane section loads only the currently visible reconstructed slices.
- Moving a reconstruction slider loads the newly required slices.
- Opening 3D initially loads only the focused structure.
- Additional surfaces load only after an explicit user selection/request.
- Opening editing loads the editing engine and necessary label data.
- Editing must not trigger a full CT-volume download unless a specific operation genuinely requires it.

Shared lightweight metadata may be loaded when an advanced section needs it.

Deduplicate concurrent requests, cache reusable results, bound decoded-image memory, and ignore stale results after navigation.

Provide loading indicators, useful errors, and retry behavior. A failed slice request must not leave another slice displayed as though it were the requested image.

## 6. Preserve data and features

Preserve:

- Persian text, RTL layout, themes, and existing controls.
- CT window/level, zoom, pan, rulers, and slice navigation.
- Structure selection, overlays, contours, and opacity.
- Automatic measurements and bilateral comparison.
- Mask editing, undo/redo, original-versus-edited comparison, and edit export/import.
- Synchronized multiplanar views and 3D interaction.

Preserve numeric CT encoding, label values, geometry, source identity, fingerprints, and measurement precision.

Do not convert numeric-data PNGs to JPEG or apply lossy compression.

The existing PNG-encoded surface buffers may remain in that lossless representation, stored separately per structure.

Keep original masks immutable and edited versions separate.

## 7. Server response and publication

Return the completed ZIP through the existing download mechanism.

For a direct download response, use:

Content-Type: application/zip
Content-Disposition: attachment; filename="ct-viewer-<safe-result-id>.zip"

If the application uses asynchronous artifact generation, publish the ZIP through that existing mechanism after generation completes.

Update only consumers that currently expect an HTML artifact so they correctly handle the ZIP.

Generate the complete package in a temporary staging directory, validate it, and publish the ZIP only after successful completion.

A failed build must not replace a valid artifact with a partial ZIP.

Use the existing configured output destination. Do not introduce new MinIO infrastructure.

## 8. Verification

Verify that:

1. New server-generated results return ZIP files.
2. Each ZIP includes all referenced assets.
3. index.html contains no embedded scan-sized payload.
4. No folder picker or asset-location prompt appears.
5. The extracted package works under an arbitrary HTTP path.
6. Renaming its directory does not break relative URLs.
7. The local launcher opens the viewer without asking for a directory.
8. Startup requests only the initial slice and mask.
9. Measurements, MPR images, and surfaces load only when requested.
10. Existing editing and measurement features remain functional.
11. Extracted numeric assets match the original data.
12. Missing-file errors and retries work correctly.
13. Original MinIO and inference workflows remain unchanged.
14. The ZIP passes an integrity check and the viewer produces no new JavaScript errors.

Test an actual server-generated package, not only a manually assembled example.

Deliver the generator changes, a newly generated ZIP, and a concise verification report. Do not stop after providing a plan.
