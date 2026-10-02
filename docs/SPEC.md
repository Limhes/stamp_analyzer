# Stamp Analyzer: specification

Version 0.1, 2026-10-02. Author: René, drafted with Claude.

## 1. Purpose and scope

The application measures postage stamps from flatbed scans with known geometric and
colorimetric calibration. For every stamp it determines:

1. **Perforation gauge per side**, as teeth per 20 mm, or imperforate.
2. **Cancellation**, as an optional segmentation mask with its colour.
3. **Colour**: paper colour and N ink colours (usually N = 1) in CIELAB/LCh D50,
   with dispersion statistics, ΔE00 values and ISCC–NBS names.
4. **Geometry**: stamp dimensions in mm.

It then produces a PDF report with one page per stamp and an overview page.

Out of scope for v1: syncopated or irregular perforation patterns, watermark
detection, plate flaws, OCR of the design, and multi-scanner management.

## 2. Physical setup and known limitations

**Scanner.** Epson Perfection 1200U, controlled by SANE (`epson`/`epson2` backend;
verify which one with `scanimage -L`). The working resolution is 1200 dpi (≈ 0.0212 mm/px,
so one perforation hole at gauge 12 is ≈ 79 px). Use 16 bit per channel if the backend
offers it, and verify with `scanimage -A`. All automatic corrections stay off: auto
exposure, auto gamma, colour correction, sharpening and descreening. Gamma is fixed
(linear if possible). The exact option set is recorded (§4.1).

**Sample.** A black card with stamps held behind glassine strips (a stock card).
Consequences:

- The background is black but not uniform. The glassine adds haze (lifted blacks),
  and the strip edges show up as near-horizontal bright lines that can cross stamps.
  Detection must tolerate both.
- Stamps may slightly overlap strip edges or each other.
- **Known limitation (accepted for v1):** the glassine changes the colour (haze, tint,
  specular effects), but the IT8 reference is scanned without glassine. This gives
  a systematic colour error. The app must state this on the report as
  "measured through glassine; not corrected". A later experiment (IT8 behind glassine
  vs. bare) will quantify the error. The design must allow a "glassine
  correction" to be added later without restructuring.

**Reference sheet.** A Wolf Faust IT8.7/2 reflective target (batch R240103, 5×7",
FUJICOLOR Crystal Archive, glossy) plus a metal ruler with black tick marks. The ruler
type is not yet decided, so the ruler algorithm must be generic (§4.2).
The IT8 reference data (CGATS, 288 patches A1–L22 + GS0–GS23, XYZ and Lab D50 2°,
plus per-patch STDEV and MEAN_DE) is at `reference_data/R240103.txt`.

Another known limitation, documented on the report but not corrected: the target is
glossy photographic paper while stamp paper is matte, which can give a small
gloss-dependent bias.

## 3. Architecture

```
stampan/
  core/                 # pure Python, no Qt
    cgats.py            # CGATS/IT8 parser
    io.py               # 16-bit TIFF read/write, metadata
    scanner.py          # SANE wrapper: option capture, apply, verify, acquire
    calibration/
      geometry.py       # ruler → px↔mm (x and y)
      it8.py            # patch location + RGB extraction
      characterisation.py   # RGB→XYZ model fit, validation, apply
      reference.py      # Reference object: save/load/verify
    detection.py        # stamp detection + rotated crops
    perforation.py
    cancellation.py
    colour.py           # masks, clustering, statistics
    naming.py           # Munsell + ISCC–NBS
    project.py          # sample/project persistence
    report/             # Jinja2 templates + WeasyPrint
  cli.py                # `stampan` command (typer or argparse)
  gui/                  # PySide6 app (`stampan-gui`)
tests/
scripts/                # diagnostic runners used during development
reference_data/R240103.txt
docs/
```

Each analysis function has the form `analyse_x(inputs, params) -> XResult`.
`XResult` is a dataclass holding:

- the numeric results,
- a `confidence` in the range 0–1 plus a list of `warnings`,
- the parameters used,
- a diagnostic image (or a function that renders one).

Results serialise to JSON. Manual user edits (marks, overrides, mask edits) are stored
as separate fields next to the automatic result, never by overwriting it. That way
"auto" vs "manual" is always traceable.

### 3.1 Persistence

```
<library_root>/
  references/<ref_id>/
    reference.json      # name, date, scanner options, scales, model, validation
    reference_scan.tif  # raw 16-bit scan
    model.npz           # characterisation coefficients / LUT
    diagnostics/*.png
  projects/<project_id>/
    project.json        # name, date, ref_id, notes
    sample_<n>.tif      # raw scans
    stamps/<stamp_id>/
      crop.tif          # rotated, full-res crop with black margin
      stamp.json        # all results + manual edits + metadata (catalogue no., notes)
      diagnostics/*.png
  stampan.sqlite        # index of all stamps and key results, for cross-project queries
```

The library root can be configured. The default is `~/StampAnalyzer`. **It must not live inside
a sync folder that touches files while they are being written.**

## 4. Reference (calibration)

### 4.1 Scanner settings

- Capture the complete `scanimage -A` option list and the device name (`-L`) when
  creating a reference.
- Store the subset of options that affect geometry or colour: resolution, mode, depth,
  gamma/correction options, scan area, and any "auto" options. The scan area may differ
  between reference and sample; everything else must match.
- When a reference is loaded, apply its options before each sample scan. Then read them
  back and **refuse to scan (or warn loudly)** if any stored option differs.
- Record the lamp warm-up time. Default: wait 60 s after the first scan of a session, or
  do a dummy pre-scan. Store the scan timestamp in every TIFF and JSON.

### 4.2 Geometric calibration (ruler)

- The user marks the approximate ruler region and its orientation, or the software finds it.
- Extract an intensity profile along the ruler's tick region and detect the tick
  centres with sub-pixel precision (centroid or parabolic peak fit on the
  tick-darkness profile).
- Assign mm values to the ticks, using the known tick spacing (a parameter: 1 mm or
  0.5 mm). Fit a linear regression of position on mm. Report the scale (px/mm), the
  standard error, the residuals (max, RMS) and a non-linearity check (quadratic term).
- **The x and y scales are calibrated separately.** Either the ruler is placed in
  both orientations (two ruler regions on the reference sheet, or two reference
  scans), or the software warns that it is assuming x = y. The residual plot is a
  diagnostic image.
- Acceptance target: scale uncertainty < 0.05 % (it limits perforation-gauge accuracy).

### 4.3 Colour characterisation (IT8)

**Patch location.** The user clicks the four corner-patch centres (A1, A22, L1, L22)
on the scan; the software then interpolates the grid and the GS row. As a cross-check
when it's installed, ArgyllCMS `scanin` with `it8.cht` can be used. Per patch, take
the central ~60 % of the area and compute a trimmed mean of the RGB values (reject dust
and scratches with a robust estimator). Record the patch std as a quality indicator.

**Model.** Linear scanner RGB maps to XYZ (D50, 2°). Implement and compare at least:

- (a) a 3×3 matrix (baseline),
- (b) root-polynomial regression of degree 2 and 3 (Finlayson et al.; available in
  `colour.characterisation`),
- (c) optionally, an ArgyllCMS ICC input profile (`colprof`) applied via ArgyllCMS
  (`cctiff`/`xicclu`), as an external cross-check;
- (d) as a reference point, piecewise-linear (Delaunay) interpolation of RGB → Lab
  (`scipy.interpolate.LinearNDInterpolator`). René explored this earlier (see §13). It
  has no smoothing (patch noise passes straight through) and is undefined outside the
  convex hull of the patches. The cross-validation should show whether it helps.

The existing LUT profile `epson1200u.icc` (see §13) can be applied with LittleCMS as a
further cross-check. Its scan settings are unknown, so it is never the reference.

If the scanner output is not linear (gamma applied), first linearise it with the GS
grey-ramp patches (1-D curve per channel, monotone spline).

**Validation (mandatory, shown in the GUI and on every report):**

- Training ΔE00: mean, median, 95th percentile and max over all 288 patches.
- **Leave-one-out or k-fold cross-validated ΔE00.** This is the realistic number.
- A per-patch error map rendered on the target layout (diagnostic image).
- Pick the model with the best cross-validated mean ΔE00, without overfitting
  (degree 3 must beat degree 2 clearly).

Pixel transform: scanner RGB (float, linearised) → XYZ D50 → CIELAB D50 (reference
white = D50 perfect diffuser, Y = 100 scaling consistent with the CGATS data).

### 4.4 Reference object

A reference has a name, a creation date, notes, scanner options, the x/y scale with
uncertainties, the colour model and its validation statistics, and the raw scan.
Operations are list, load, rename, archive and compare. A sample always stores the
`ref_id` it was measured with.

A reference can be reused only with matching scanner options. Optionally the user
may put a small check patch (e.g. a few grey patches cut from an old target, or a
neutral tile) on every sample sheet. If it's present, the software measures it and
warns when it deviates from the reference by more than ΔE00 1.0. Design the hook now,
implement it later.

## 5. Sample scanning and stamp extraction

- Scan the full sample sheet with the reference options. Store it as a 16-bit TIFF.
- **Automatic detection.** The background is dark (in L* or the max channel). Find the
  stamp foreground with Otsu or adaptive thresholding on a smoothed image, plus
  morphological closing (to fill the perforation teeth). Remove thin bright line
  structures (glassine strip edges) by morphology or line detection. Take the
  connected components, filter by plausible size (8–80 mm per side) and fill ratio,
  and fit a `minAreaRect`.
- Touching stamps: split them with a distance-transform watershed. If that fails, flag
  the region for manual splitting.
- **Manual.** The user draws, moves, rotates and deletes rotated rectangles. Detected
  rectangles are editable the same way.
- **Output per stamp:** a crop rotated upright (bicubic, 16-bit) with a black margin
  of at least 3 mm around the tooth tips, plus the transform back to sample coordinates.
  The user can rotate the crop by 90° steps and mirror it (never needed in practice,
  but keep the transform general).

## 6. Perforation

Definition: gauge = number of perforation holes (equivalently, teeth) per 20 mm.
The plausible range is 6–18. **Imperforate** sides are reported as `imperf` (gauge 0)
and straight-edged sheet-margin sides as `straight edge`.

### 6.1 Algorithm per side

1. Segment the stamp, paper included, against the black background (threshold on the
   crop, morphological cleanup, keep the largest component). Get the outer contour at
   sub-pixel precision (e.g. `skimage.measure.find_contours`).
2. Fit the four sides: robust line fits through the **tooth tips** (outer envelope) and
   through the **hole bottoms** (inner envelope). The depth of the perforation zone is
   their difference.
3. Exclude the corner zones, about one hole pitch from each corner.
4. Build a 1-D profile: for points along the fitted side line, take the contour
   distance perpendicular to the line, sampled uniformly at e.g. 0.01 mm steps using
   the calibrated scale for that direction.
5. Estimate the period with **two independent estimators**:
   - **(A) Spectral.** Detrend, apply a window, zero-pad, FFT. Take the dominant
     peak in the 6–18 gauge band and refine it with a parabolic/Gaussian peak fit
     or a sinusoid least-squares fit.
   - **(B) Hole positions.** Detect the hole centres (local maxima of indentation, or
     the circle fit for each hole arc). Regress the hole index on position. The
     period is the slope; the standard error comes from the regression.
6. Gauge = 20 / period_mm. Report:
   - the value to two decimals, with the uncertainty from (B);
   - the nearest catalogue notation in quarter steps (e.g. 11½, 11¾, 12¼);
   - the hole count and the measured length;
   - spacing regularity (std of hole-to-hole distances, max deviation). This is
     useful for spotting fakes and re-perforation;
   - the mean hole diameter (circle fit) and the tooth-tip roughness (clean-cut vs
     rough perforation).
7. **Confidence.** High when (A) and (B) agree within 0.1 gauge, the spectral peak is
   dominant, there are at least 8 holes and the spacing is regular. Low otherwise. Low
   confidence is flagged in the GUI and on the report.
8. **Imperforate detection.** The profile shows no significant periodic component
   (the peak is not above the noise floor), and the edge is straight with small
   amplitude. A straight edge on one side combined with perforated sides is
   reported as such.

Compound notation: horizontal × vertical (top/bottom × left/right), e.g. "11½ × 12".
When opposite sides differ, report all four.

### 6.2 Manual mode

On the dedicated perforation view, the user can:

- click tooth tips or hole centres (snap to the nearest local extremum). The gauge is
  then computed by the same regression as estimator (B);
- override the value per side, with a mandatory reason field;
- mark a side as imperf or straight edge.

### 6.3 Diagnostic image (also used in the report)

The stamp crop with the fitted tip and bottom lines, detected hole centres (numbered
every 5th), excluded corners, and per-side labels (gauge ± uncertainty, auto/manual,
confidence). Next to it, per-side mini-plots of the profile and its spectrum with the
chosen peak.

### 6.4 Validation

- **Synthetic tests:** generate stamp images with known gauge (10–16 in 0.25 steps),
  random rotation of ±3° before cropping, noise, blur (scanner MTF), and rough hole
  edges. Requirement: |error| < 0.05 gauge for all sides with at least 10 holes.
- **Real tests:** compare with René's hand-gauged stamps (§11).

## 7. Cancellation (optional per stamp)

Cancellation inks are mostly black or dark grey, sometimes blue/violet or red, and
sometimes faint. They overlie both the design ink and the paper.

### 7.1 Classical pipeline (v1)

1. Work in CIELAB (from the characterised image) inside the stamp interior mask.
2. Optional user hint: an ellipse or circle where the cancellation (datestamp) is.
   Inside the hint, the detector's prior is raised; outside it, lowered.
3. Candidate pixels are those that are not explained by the paper or design-ink
   clusters. Initialise the clusters from the colour module (§8) with k = paper +
   inks, then flag pixels with low likelihood under those clusters, or pixels
   matching a cancel-colour prior (low L* with low C* for black, or a hue window
   for blue/violet/red).
4. Shape cues: Hough circles and arcs for circular datestamps, line and bar detection
   for obliterators, and the connectedness and stroke width of the candidate regions.
5. Combine the evidence into a probability map and threshold it with hysteresis. The
   threshold can be adjusted with a slider in the GUI.
6. Faint cancellations: local contrast normalisation on the Lab difference from the
   expected ink/paper colour.

When the cancel colour is close to the ink colour (black cancel on dark ink), the
problem is ill-posed. The module must report low confidence in that case and not
pretend.

### 7.2 Manual editing

The user can add or remove mask regions with a brush or lasso. The mask is stored as
a PNG next to the auto mask.

### 7.3 Outputs

- The mask, plus the mean colour of the cancellation (Lab/LCh) and a cancel-colour
  class (black / blue / violet / red / other).
- A visualisation: the stamp rendered very light and desaturated (L* raised, C*
  reduced), with the cancellation in its own colour, darkened, on top. Also an
  "isolated cancellation" image on white.

### 7.4 Future

The mask interface must let a learned segmenter replace the classical one later
(SAM-assisted annotation, or a small U-Net trained on corrected masks). Store every
manually corrected mask together with its crop, so a training set accumulates for free.

## 8. Colour

### 8.1 Region of analysis

- Interior mask: the stamp mask eroded inward past the perforation zone (hole-bottom
  line plus a margin of ≥ 0.3 mm). Imperforate sides use an equivalent margin.
- Exclusions: the cancellation mask dilated by about 0.2 mm (if the cancellation step
  ran; otherwise the cancellation counts as ink and the report says so), any
  user-drawn exclusion zones (repairs, stains, hinge remnants showing through), and
  specular or glassine artefacts (pixels near saturation, strip-edge lines).
- The user can also draw an **inclusion ROI** to restrict the analysis, e.g. to the
  frame only.

### 8.2 Colour space

All pixels are converted to CIELAB D50 through the reference model (§4.3).
Presentation uses **LCh(ab)**: L* lightness, C*ab chroma and h_ab hue angle. That is
the cylindrical, HSL-like view René asked for. ΔE00 is the difference metric.

### 8.3 Clustering

- k = 1 (paper) + N inks. N is chosen by the user, default 1. Optionally include a
  cancellation class if no cancellation mask exists.
- Method: a **Gaussian mixture model** in Lab (full covariance), initialised by
  k-means. Subsample for fitting (e.g. 200k pixels), then predict all pixels.
- Cluster identity: paper is the cluster with the highest L* and lowest C*. The user
  can relabel.
- **Mixed pixels.** Pixels with posterior < τ (default 0.9) count as mixed (ink/paper
  edge pixels, which are common in engraved and typographed stamps) and are excluded
  from the statistics. τ is adjustable.
- **Solid-ink estimate.** For fine-line designs the ink cluster is biased toward the
  paper colour by scanner MTF. Also report the "core ink" colour: pixels whose
  distance to the paper centroid lies in the top 25 % within the ink cluster.
  Document this as an estimator choice.

### 8.4 Interaction (GUI)

- **3D view** (pyqtgraph.opengl): pixels as points (subsampled), each drawn in its
  own display sRGB colour. Axes are a*, b*, L*, with an optional cylindrical LCh grid.
  Cluster centroids are large markers, and 1σ/2σ covariance ellipsoids are drawn as
  wireframes. The view can be rotated and zoomed.
- **Linked 2D view:** the stamp with the per-cluster mask overlay (toggle per
  cluster), plus the mixed-pixel overlay.
- Adjustments: N, τ, dragging a centroid (moving a centroid's position in Lab
  re-runs the assignment with fixed means), merge/split. Every change updates both
  views live (< 0.5 s on a typical stamp).
- When the user clicks **Accept**, the final statistics are computed and frozen with
  the parameters used.

### 8.5 Outputs per cluster

- Pixel count and area fraction.
- Mean and median Lab and LCh. For the mean hue, use a circular mean, reported with
  C*-weighting.
- Std per axis, the covariance matrix, and the mean ΔE00 of the pixels to the
  centroid (spread).
- ΔE00 between ink and paper; ink chroma and hue.
- XYZ and xyY, an approximate display sRGB swatch (labelled as out-of-gamut when
  applicable), and the Munsell notation (§9).
- Calibration context: the reference ID and the model's cross-validated ΔE00, so the
  reader can judge significance.

### 8.6 Shade comparison (prepare now, implement later)

The SQLite index stores the final Lab values per stamp and cluster. A later feature
will offer "find stamps within ΔE00 < x" and shade-group assignment for a series.
René also has 1200-dpi Epson scans of a Stanley Gibbons colour key. He will copy
them into `testdata/colour_key/` when needed. They can become a named reference-shade
set once they are measured with the same pipeline. Do **not** reuse the existing
`color_key_calibrated.csv` (§13): its Lab values are systematically biased.

## 9. Colour naming (ISCC–NBS)

Use the proper ISCC–NBS block system, not the nearest centroid:

1. Lab D50 → XYZ D50 → chromatic adaptation (CAT02 or Bradford; document which) to
   **illuminant C** → xyY.
2. xyY → Munsell (`colour.notation.munsell.xyY_to_munsell_colour`, based on the
   renotation data). Handle failures outside the renotation gamut gracefully: report
   "outside Munsell renotation range".
3. Munsell → ISCC–NBS Level 1/2/3 category and name with Centore's block-boundary
   implementation. The sources are in `reference_data/iscc_nbs/`:
   - `MunsellToISCCNBS.m`: Centore's MATLAB original (**the authority**)
   - `MunsellToISCCNBS_adapt.m` and `MunsellToISCCNBS_adapt.py`: René's adaptation and
     Python port
   - `ISCCNBSnameFromNumber.m` and `color_names.txt`: Level 1/2/3 designators and
     abbreviations
   - `xyYtoMunsell.m`: Centore's xyY → Munsell, as a cross-check for colour-science

   A second, **diverging** Python port exists in
   `../ColorIdentification/color_detect_gui/stampcolor/color_func.py`
   (`munsellToISCCNBS`). It returns different Level-3 indices for the same hue band, so at
   least one of the two ports is wrong. Port fresh from the MATLAB original (or verify
   one existing port line by line against it), and wrap it in a clean module with tests.

   Regression test: Centore's sRGB → Munsell → ISCC–NBS table,
   `../rnd_projects/rgb_to_munsell/sRGBToMunsellAndISCCNBS.csv` (~135k rows). It tests
   the whole chain sRGB → XYZ → illuminant C → Munsell → ISCC–NBS.
4. Output: the Level 3 name (e.g. "dark grayish bluish green"), the abbreviation, the
   Level 1/2 names and the Munsell notation (e.g. "7.5BG 3.2/2.1").

Note on the report that ISCC–NBS assumes illuminant C viewing.

## 10. Report

Generated with Jinja2 + WeasyPrint, A4.

**Overview page(s):** project name, date, reference used (ID, date, colour-model CV
ΔE00, scale uncertainty), and the known limitations (glassine, gloss). Then a table
of all stamps: thumbnail, ID/catalogue number, gauge notation, ink name, ink LCh,
paper LCh, cancellation yes/no, and flags (low confidence, manual override).

**One page per stamp (as much information as fits cleanly):**

- Header: stamp ID, user fields (catalogue number(s), country/issue, notes), and the
  scan date and time.
- Large calibrated image of the stamp, with its dimensions in mm (tip-to-tip and
  hole-bottom-to-hole-bottom).
- Perforation: the annotated diagnostic image, a per-side table (gauge ± u, notation,
  hole count, regularity, hole diameter, auto/manual, confidence), and the compound
  notation.
- Cancellation (if run): the isolated cancellation image plus its colour and class.
- Colour: swatches for paper and ink(s); a table of L*, C*, h, a*, b* (mean ± std),
  the ΔE00 spread, ink-paper ΔE00, Munsell and ISCC–NBS name; a small a*b* scatter
  plot and a C*–L* plot with the cluster ellipses; the mixed-pixel fraction; and the τ
  used.
- Footer: software version, reference ID, and a hash of the analysis parameters.

Also export all results as CSV and JSON.

## 11. Test data, ground truth, validation

- Test data location (outside the project folder):
  `/home/limhes/git/stamps/testdata/`
  - `reference/`: the IT8 + ruler scan(s), with the `scanimage -A` dump
  - `samples/`: sample sheet scans
  - `ground_truth.yaml`: per stamp, the hand-gauged perforation per side, imperf
    sides, whether it's cancelled, the expected shade name/catalogue colour, and notes
- Read the test-data path from the env var `STAMPAN_TESTDATA`. Tests that need real
  data are skipped when it's not set.
- Every phase adds a script in `scripts/` that runs its module over all test data and
  writes diagnostic images plus a summary table to `out/<phase>/`, for René's review.

## 12. GUI (PySide6)

- **Main window.** The left side has a library tree (References / Projects → Samples
  → Stamps). The centre has an image viewer (QGraphicsView): smooth zoom and pan,
  a pixel readout (px, mm, RGB, Lab), and overlay layers. The right side has a
  context panel with tabs: Detection, Perforation, Cancellation, Colour, Notes.
- **Viewer tools:** rotated rectangle, ellipse, polygon, point markers, brush/lasso
  (add/remove), and inclusion/exclusion ROIs. Every tool has undo/redo.
- **Workflow mode:** a step bar (Reference → Scan → Detect → per stamp: Perf → Cancel
  → Colour → Report), with per-stamp status icons (todo / auto / reviewed / manual).
- Long computations run in worker threads with progress and cancel.
- Display colour management: display sRGB is computed from Lab (assume an sRGB
  monitor). The display never feeds back into measurements.

## 13. Prior work in `/home/limhes/git/stamps/`

The project lives in `/home/limhes/git/stamps/stamp_analyzer/`. Its sibling folders hold
René's earlier experiments. **Use them for ideas, UI patterns and test images, not as a
foundation.** Most of the analysis code there is prototype quality, and some of the
colour maths is wrong for this application. Paths below are relative to `stamp_analyzer/`.

| Location | What it is | Reuse | Do not reuse |
|---|---|---|---|
| `../rnd_projects/stampspec/stampspec_v1..v3` | Perforation and ink prototypes (tkinter/Qt), with deskew via `minAreaRect`, Otsu, an edge-band intensity profile, and autocorrelation/peak spacing | The general idea of an edge profile. Test images `stampspec_v2/tests/data/testimage_<n>_<dpi>.jpg` (300/600 dpi JPEG) as early smoke tests. They have no gauge ground truth; ask René. | Integer-pixel periods, a fixed 20 px edge band, dpi parsed from the filename, top+bottom averaged into one value (which hides compound perfs), HSV thresholds on 8-bit sRGB. The ink hex labels in `labels.csv` are uncalibrated. |
| `../rnd_projects/absolute_color_kiro` (and the older `absolute_color`) | "Stamp Color Scanner": the `colorlib` library plus a PySide6 GUI, with Kiro specs (`.kiro/specs`) and hypothesis tests | GUI patterns: `SelectableGraphicsView` (wheel zoom, right-drag pan, rubber band), the circular-dropper picker, a thumbnail table with hover preview, an a\*b\* plot with the sRGB gamut boundary at a chosen L\*, and the stamp metadata fields (name, code, country, year, condition Mint/Used/CTO/Other, notes). Also the light Qt theme, if René likes it. | The colour maths. It uses D65 everywhere, while the IT8 data is D50. It assumes a 24×16 = 384-patch grid with a CSV reference (the real target is CGATS, 288 patches: 22×12 + 24 GS). It samples only the central 10 % of each patch, works in 8 bit, uses ΔE76, fits Lab directly by degree-3 polynomial with no cross-validation, and persists with pickle. `historic_names.py` holds ~200 Lab values of unknown provenance that look approximate; don't use them as reference data unless they are sourced. Its perceptual naming is an ad-hoc scheme; ISCC–NBS is the authority. |
| `../ColorIdentification/color_detect_gui` | PyQt5 tool: k-means on a selection, an ISCC–NBS lookup by rounding sRGB to steps of 5 in Centore's table, and the nearest Stanley Gibbons key colour | Nothing beyond ideas | The second `munsellToISCCNBS` port (see §9), and colour conversions that treat ColorMatchRGB output as sRGB. |
| `../ColorIdentification/color_key` | Stanley Gibbons colour key run through `epson1200u.icc` → ColorMatchRGB, then converted to Lab *as if it were sRGB* | The 200 colour names and the idea | `color_key_calibrated.csv`: its Lab values are biased (wrong primaries and gamma, D65/D50 mix, 8 bit). Re-measure the key with this pipeline. |
| `../ColorIdentification/color_detect_web` | JS/plotly version | 3D plotly ideas only | n/a |
| `../ColorIdentification/color_detect_gui/icc_profiles/epson1200u.icc` | A 105 kB LUT input profile (scnrRGB → Lab, description "it8-target-scan", Perfection 1200U), made earlier from an IT8 scan with unknown settings | A cross-check for the new characterisation (§4.3) | It is not the reference. `../rnd_projects/icc_profile/icc_profiles/icc_profile_epson1200u.icm` (16 kB) is a different, simpler profile. |
| `../rnd_projects/color_spaces/calibration.ipynb` | René's notes on SANE raw capture (all automatic processing off) and a `LinearNDInterpolator` idea | The capture checklist (it matches §2/§4.1). The interpolator becomes candidate model (d) in §4.3. | n/a |
| `../rnd_projects/rgb_to_munsell/sRGBToMunsellAndISCCNBS.csv` | Centore's conversion table | The regression test for §9 | n/a |
| `../rnd_projects/stamp_feature_detect`, `../__android` | Machin template matching; an Android skeleton with a mocked perforation result | Out of scope | n/a |

Do not modify anything outside `stamp_analyzer/` (and `testdata/`).

## 14. Future (design hooks only)

Glassine correction, a check-patch drift monitor, a shade library with Stanley
Gibbons key references, learned cancellation segmentation, syncopated perforation,
watermark imaging, and batch re-analysis when a reference is replaced.
