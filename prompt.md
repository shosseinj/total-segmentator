Extend the existing CT segmentation viewer with ONLY the features specified below.

TotalSegmentator and the existing infrastructure already work. Implement these additions in the viewer generator so future generated HTML files include them automatically.

## Scope and preservation rules

1. Locate the actual viewer-generation code, templates, and associated viewer assets in the repository. 
2. Inspect the existing implementation and identify which requested features already exist, are incomplete, or are new. Reuse working functionality.
3. Preserve:
   - Persian interface and RTL layout.
   - Existing themes.
   - Authentication and login.
   - CT display and slice navigation.
   - Structure selection and overlays.
   - Opacity and contour controls.
   - Ruler, window/level, zoom, and pan.
4. Do not change MinIO configuration, buckets, storage paths, database schemas, existing API contracts, authentication, model inference, or deployment configuration.
5. Limit changes to viewer generation, viewer assets, and the minimal additive propagation of existing geometry metadata required by these features.
6. Preserve self-contained offline HTML exports and avoid runtime CDN dependencies.
7. Do not implement HU analysis, reporting, chat assistants, additional models, longitudinal comparison, or other features outside this request.
8. Distinguish the 117 available classes from the number of nonempty masks. An empty mask does not establish the absence of an organ or disease.

## Feature 1: Automatic measurements

When a structure is selected, display:

- Volume in mL.
- Three-dimensional bounding-box dimensions in mm, with clearly defined axes.
- Maximum axial cross-sectional area in mm².
- The corresponding slice and a button to navigate to it.
- A chart of cross-sectional area across slices; clicking the chart navigates to that slice.
- Measurement markers showing where the displayed measurements were obtained.
- An option to save a selected measurement locally.

Calculate measurements using segmentation voxels and validated physical geometry. Never derive physical measurements from screen dimensions, zoom, or canvas scaling.

Define whether the section measurements refer to native acquisition slices or anatomically reconstructed axial slices. Do not silently treat an oblique acquisition plane as a patient-axial plane.

Use physical voxel boundaries for bounding-box extents rather than only distances between voxel centers.

Do not label bounding-box dimensions as anatomical length or RECIST diameter.

For empty or unavailable masks, show an explicit unavailable/empty state rather than a misleading zero. Flag potentially incomplete structures touching the scan boundary.

### Paired-structure comparison

Provide a table for available paired structures, such as the kidneys, showing:

- Left and right volumes.
- Absolute difference.
- Percentage difference.
- Available dimensions.
- Navigation to each structure.

Use an explicitly displayed formula and reference. For example:

Percentage difference = 100 × (right volume − left volume) / left volume.

Handle missing values and a zero reference without producing Infinity or NaN. Do not interpret asymmetry as disease.

## Feature 2: Physician mask editing

Add an explicit editing mode with:

- Additive brush and eraser.
- Brush size in mm using validated in-plane spacing.
- Undo and redo.
- Original-versus-edited mask comparison.
- Updated volumes, dimensions, and area charts after edits.
- Review states: approved, rejected, and needs correction.
- Reset to the original mask.

Implement current-slice editing first and label it accurately. Do not describe a 2D brush as volumetric editing.

Keep original model masks immutable. Maintain edits as a separate version, and make the active original/edited version clear.

Prevent unintended overwriting of another structure. Define and display how brush overlap and erasing behave.

Changing an approved mask must invalidate its previous approval and require another review.

### Saving edited versions

Save a separate downloadable edit package containing:

- Schema version.
- Source result identity and available study/series identifiers.
- Original-data fingerprint.
- Dimensions and geometry.
- Label mapping.
- Changed voxels or edited masks in a documented format.
- Creation time.
- Available authenticated user identity.
- Review state and relationship to the original result.

Do not fabricate a user identity when it is unavailable.

Reuse existing persistence interfaces only if they already support this operation without infrastructure changes. Otherwise, use local download/import and clearly state that edits have not been saved to the server.

Validate source identity and geometry before importing an edit package. Never overwrite the original model result.

## Feature 3: Synchronized multiplanar views

Add:

- Axial, coronal, and sagittal views.
- Synchronized crosshairs.
- Aligned segmentation overlays in every view.
- Correct physical aspect ratios.
- Coordinate mapping between views.
- Consistent selected-structure highlighting.

Use origin, orientation/direction, spacing, and affine geometry from existing source data.

Inspect the generation pipeline and propagate available metadata into the HTML payload through additive fields. Do not change inference behavior or existing infrastructure.

Do not invent missing geometry or patient orientation. If reliable metadata is unavailable, identify the missing information and disable the affected anatomical functionality with a clear explanation.

Keep overlays aligned after reorientation or resampling. Use label-preserving nearest-neighbor interpolation for masks.

Reuse suitable libraries already present in the project rather than rebuilding the viewer.

## Feature 4: Interactive 3D structures

Add an optional 3D surface view with:

- Selected structures rendered in their assigned colors.
- Independent visibility and opacity controls.
- Picking a structure to highlight it in the 2D views.
- Mapping a picked surface point to synchronized crosshairs where supported.
- Updates after mask edits.
- Camera reset and basic rotation/zoom controls.

Build geometry using physical spacing and orientation.

Continue calculating measurements from voxel masks, not from potentially smoothed display meshes.

Initialize expensive 3D processing only when requested. Keep rendering responsive and release unused meshes and buffers.

## Performance and state management

- Use Web Workers or equivalent background processing for expensive calculations where appropriate.
- Cache measurements and update only affected results after editing.
- Avoid retaining every decoded image indefinitely.
- Show progress and support cancellation for expensive operations.
- Ignore stale asynchronous results after the selected structure or dataset changes.
- Preserve existing keyboard controls and mobile responsiveness.
- Keep added panels collapsible.
- Keep the original viewer usable even if an optional 3D capability is unavailable.

## Implementation order

1. Audit the viewer generator and available geometry.
2. Implement automatic measurements and paired-structure comparison.
3. Implement mask editing, undo/redo, review states, and local version export/import.
4. Implement synchronized multiplanar views.
5. Implement the optional 3D view and picking.
6. Generate a fresh HTML export and verify the complete workflow.

## Verification and acceptance

Verify:

- Existing viewer features and themes still work.
- A newly generated HTML file includes the additions.
- The exported HTML works offline.
- Original embedded CT data and original masks remain unchanged.
- Known synthetic masks produce correct volume, dimensions, and section areas.
- Zoom and pan do not affect measurements or brush coordinates.
- Brush size remains physically correct with anisotropic pixels.
- Editing updates measurements and charts; undo/redo restores the expected results.
- Edited versions can be exported and safely re-imported.
- Oblique geometry and orientation changes preserve CT-mask alignment.
- Crosshairs refer to the same physical location in all three views.
- 3D picking selects the correct structure.
- Missing geometry, empty masks, and incomplete coverage produce clear states.
- There are no new console errors or production-build failures.

Validate the additions on a real generated export as well as synthetic geometry fixtures. Do not treat mocked rendering or placeholder buttons as completed features.

Deliver:
1. The generator/template changes.
2. A newly generated HTML demonstrating the features.
3. Relevant verification results.
4. A concise list of completed features and any remaining metadata-related limitations.

Keep MinIO, the backend workflow, and model inference unchanged.
