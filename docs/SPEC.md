# Stamp Analyzer: specification

Version 0.1, 2026-10-02. Author: René, drafted with Claude.

## 1. Purpose and scope

The application measures postage stamps from flatbed scans with known geometric and
colorimetric calibration. For every stamp it determines:

1. **Perforation gauge per side**, as teeth per 20 mm, or imperforate.
2. **Colour**: paper colour and N ink colours (usually N = 1) in CIELAB/LCh D50,
   with dispersion statistics, ΔE00 values and a philatelic colour name (§9, not
   ISCC–NBS), measured over a user-selected inclusion area so a cancellation doesn't
   contaminate the reading (§7: automatic cancellation segmentation was tried and
   dropped).
3. **Geometry**: stamp dimensions in mm.

It then produces a PDF report with one page per stamp and an overview page.

Out of scope for v1: syncopated or irregular perforation patterns, watermark
detection, plate flaws, OCR of the design, and multi-scanner management.

## 2. Physical setup and known limitations

**Scanner.** Epson Perfection 1200U, controlled by SANE (confirmed: the `epson2`
backend, via `scanimage -L`). The working resolution is 1200 dpi (≈ 0.0212 mm/px,
so one perforation hole at gauge 12 is ≈ 79 px). Use 16 bit per channel if the backend
offers it, and verify with `scanimage -A`. **Confirmed on the real device: this
backend only ever offers 8-bit depth** (`--depth` reports the single fixed value
"8bit", marked inactive); `core.scanner.acquire_scan()` detects this up front and
omits the `--depth` flag (passing it aborts the scan outright) rather than failing,
with a warning. `write_tiff()` still enforces the pipeline's uint16 convention, so
an 8-bit capture is losslessly upscaled (×257, so 0→0, 255→65535) before being
written -- skipping this (e.g. a raw `.astype(uint16)`) leaves values still
numerically in 0–255, silently outside the domain any reference's colour model was
trained on; found exactly this way on a real sample.

All automatic corrections stay off: auto exposure, auto gamma, colour correction,
sharpening and descreening. Gamma is fixed (linear if possible). **Confirmed on the
real device: this backend's `--gamma-correction Default` is *not* linear/off -- it's
the backend's own contrast-enhancing curve, a real colorimetric distortion** (its
cross-validated ΔE00 was ~6-8, vs. ~2 with an identity table on an otherwise
identical scan). The actual linear/off equivalent is `--gamma-correction=User
defined` with an identity table (`output[i] = i`) loaded via
`--{red,green,blue}-gamma-table`. Also confirmed: those three gamma-table options
are **write-only** on this backend -- `scanimage -A` never echoes a value for them,
active or not -- so `scanner.apply_and_verify()` treats them as applied-but-
unverifiable rather than failing the normal "read back and refuse on mismatch"
check it applies to every other option (§4.1). The exact option set is recorded
(§4.1) regardless.

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
FUJICOLOR Crystal Archive, glossy). No ruler is used (§4.2 -- removed once a real
scanner was available to test against: the scanner's own resolution setting, read
back and verified, is simpler and no less trustworthy for a flatbed scanner).
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
      scanin.py         # ArgyllCMS `scanin` wrapper: automatic patch location + RGB
      it8.py            # patch layout constants (ROWS/N_COLS/N_GS), PatchSample, std heatmap
      characterisation.py   # RGB→XYZ model fit, validation, apply
      reference.py      # Reference object: save/load/verify (dpi → px/mm, scanin → patches)
    detection.py        # stamp detection + rotated crops
    perforation.py
    colour.py           # masks, clustering, statistics
    naming.py           # Munsell notation
    philatelic_colours.py   # Lab -> philatelic colour name (§9, replaces ISCC–NBS)
    project.py          # sample/project persistence
    workflow.py         # end-to-end steps shared by CLI and GUI (capture, ingest,
                        #   place/measure stamp, batch perforation, report export)
    report/             # Jinja2 templates + WeasyPrint
  cli.py                # `stampan` command -- thin layer over core.workflow
  gui/                  # PySide6 app (`stampan-gui`) -- thin layer over core.workflow
    main_window.py      # menus, toolbars, docks, navigation, status bar
    pages/              # inspector pages: project, sheet, stamp (+ tabs), reference
    dialogs/            # Add Sheet dialog, New Reference wizard
    viewer.py, library_tree.py
    theme.py, icons.py, widgets.py   # look and feel: stylesheet, SVG icons, shared widgets
    tasks.py, workers.py             # background tasks, one busy indicator
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
- **Confirmed on the real device: apply and read-back must happen in a single
  `scanimage` invocation.** A direct-USB SANE backend (confirmed: `epson2` on the
  1200U) does not persist option state across separate `scanimage` processes, so a
  set-then-dump done as two calls always reads back the backend's power-on defaults,
  not what was just requested. `core.scanner.apply_and_verify()` does `-n -A`
  (set, don't scan; dump resulting state) in one call for this reason.
- Record the lamp warm-up time. Default: wait 60 s after the first scan of a session, or
  do a dummy pre-scan. Store the scan timestamp in every TIFF and JSON.

### 4.2 Geometric calibration (scanner DPI, not a ruler)

**Status (2026-10-09): changed from a ruler fit to the scanner's own resolution
setting**, at René's explicit direction once a real scanner was available to test
against. A ruler regression made sense for an uncalibrated camera distance; a flatbed
scanner already reports its own geometric sampling rate (dpi) directly, and that
value is read back and confirmed by `scanner.apply_and_verify()` (§4.1) before the
scan is taken -- it is a verified hardware setting, not an inference from the image.

- `px_per_mm = dpi / 25.4`, computed separately for x and y (`dpi_y` optional --
  omitting it assumes an isotropic scanner, true of virtually all flatbeds).
- `dpi_source` is recorded for traceability: `"verified scanner setting"` for the
  GUI's live-scan path (where `apply_and_verify` already confirmed the value), vs.
  `"typed by hand"` for an externally-captured scan, where it is only as trustworthy
  as whoever supplied it.
- **Uncertainty is 0.0, by construction, not by measurement**: there is no
  regression here to report a statistical uncertainty from. This reflects that the
  dpi value itself is taken as exact (only as good as `dpi_source` says), not a
  characterised mechanical positioning tolerance of the scan head -- flagged as
  such rather than inventing a number (CLAUDE.md: never invent measurement results).
- No reference scan with no dpi given skips geometric calibration entirely (with a
  warning), rather than falling back to a hard-coded or guessed value.

The old ruler-based module (`core/calibration/geometry.py`) and its ruler-strip
reference-sheet component have been deleted; see §2's reference-sheet note.

### 4.3 Colour characterisation (IT8)

**Patch location (2026-10-09: automatic, via ArgyllCMS `scanin`).** An earlier
version had the user click the four corner-patch centres (A1, A22, L1, L22) and
optionally the GS row's own corners, then bilinearly interpolated the rest of the
grid. Replaced at René's explicit direction: that approach needed pixel-precise
manual clicks whose small errors compounded across the 22×12 grid, and its point
labels were visually ambiguous enough to cause a real misclick (row L read as row I
in the UI font, mistaken for a geometry bug for several iterations before being
traced to the label text). `core/calibration/scanin.py` instead shells out to
ArgyllCMS's `scanin` with the standard `it8.cht` chart (shipped by the `argyll-ref`
system package, not vendored -- AGPL-3), which locates the chart's own printed
fiducial marks and patch grid directly from the scan via edge/line detection and
geometric feature matching -- no manual input, and no assumption that the target is
axis-aligned or unrotated.

- **Required system dependency, not merely a cross-check any more:**
  `sudo apt install argyll argyll-ref` (Debian/Ubuntu). `scanin` locates every
  patch; there is no fallback patch-location path.
- **Pre-crop requirement, confirmed necessary:** `scanin`'s recognition is tuned for
  a scan already reasonably cropped to the target, not a full scanner-bed capture
  (confirmed: on an uncropped scan the rotation/scale fit diverged completely --
  x/y scale differing by 30×, match confidence 0.54; cropped to the target plus a
  margin, the same scan fit cleanly, confidence 1.02). `scanin.py` auto-crops via a
  saturation-based content bounding box before invoking `scanin`, so this needs no
  manual step (the ArgyllCMS-only workflow this project started from did this crop
  by hand, in XSane).
- **Scale note:** `scanin`'s `.ti3` output reports device RGB as 0–100, normalised
  to the *scanned TIFF's own bit depth*. Every TIFF this app writes is uint16
  (including the ×257 upscale for 8-bit-only backends, see §4.1), so 100 always
  means 65535 here; `scanin.py` converts back to that native 0–65535 scale
  immediately, so nothing downstream (a fitted colour model, stamp-crop prediction)
  needs to know `scanin` was involved at all.
- `scanin`'s own `-dipon` diagnostic image (every patch box drawn and labelled on
  the scan) is persisted as the reference's `it8_grid.png` diagnostic, satisfying
  "every analysis step produces a diagnostic image" directly from the authoritative
  tool's own output rather than a hand-rolled re-rendering.

Per patch, `scanin` takes the central portion of the area and computes a robust mean
of the RGB values (reject dust and scratches); record the patch std as a quality
indicator (`it8_std_heatmap.png`).

**Model.** Linear scanner RGB maps to XYZ (D50, 2°). Implement and compare at least:

- (a) a 3×3 matrix (baseline),
- (b) root-polynomial regression of degree 2 and 3 (Finlayson et al.; available in
  `colour.characterisation`),
- (c) optionally, an ArgyllCMS ICC input profile (`colprof`) applied via ArgyllCMS
  (`cctiff`/`xicclu`), as an external cross-check -- distinct from `scanin` above,
  which is now required (patch *location*), not optional (this is a cross-check on
  the fitted *model*, still a design hook, not implemented);
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

**Condition and multiplicity (added 2026-10-04, not yet consumed by the algorithm
below).** `Stamp.condition` (`on_paper` | `soaked_off` | unrecorded) and
`Stamp.multiplicity` (`single` | `pair` | `strip` | `block`, with `multiplicity_count`
for the latter three) are now captured on the Stamp tab, since they change which
perforation approach is even correct: an on-paper specimen's measured edge is
contaminated by the backing paper's own cut/torn edge, not just the stamp's own
perforation; a block/pair/strip shares perforation lines between neighbours rather
than having four free edges. Neither is wired into §6.1's algorithm yet -- that's the
next step, not done in this pass. Both default to `None` ("not recorded"), never
assumed, since guessing wrong here would silently apply the wrong algorithm.

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

## 7. Cancellation -- dropped

**Status (2026-10-03): removed.** A classical pipeline (colour-model outlier map +
cancel-colour priors + Hough circle/line shape evidence + hysteresis fusion) was
built and run against the real stamps in `testdata/`. It did not produce reliable
masks: most real stamps came back flagged as the pipeline's own "ill-posed" case
(detected cancel colour too close to the design ink to separate), and the few
confident results weren't trustworthy on inspection. Rather than keep an automatic
step that the user cannot rely on, cancellation detection -- automatic or
hint-assisted -- is out of scope for this project. `core/cancellation.py` and its
tests were deleted; `Stamp.cancellation` no longer exists.

**Replacement approach:** §8.1's inclusion ROI is now the primary (not optional)
way to get a clean colour reading on a cancelled stamp. The user selects a region
that is unambiguously *only* the colour they want measured (a clean patch of paper,
or a clean patch of design ink, away from any cancellation), via
`analyse_colour`'s `user_inclusion_mask` (CLI: `stampan colour --include-rect`,
GUI: Phase 7's inclusion ROI tool). `analyse_colour` warns when no inclusion area
is given, since the full stamp interior may then include cancellation ink
miscounted as a design-ink cluster.

If cancellation segmentation is revisited later, a learned approach (SAM-assisted
annotation or a small trained segmenter) is likely the right starting point rather
than another classical pipeline -- but that is explicitly future work, not planned.

## 8. Colour

### 8.1 Region of analysis

- Interior mask: the stamp mask eroded inward past the perforation zone (hole-bottom
  line plus a margin of ≥ 0.3 mm). Imperforate sides use an equivalent margin.
- Exclusions: any user-drawn exclusion zones (repairs, stains, hinge remnants
  showing through), and specular or glassine artefacts (pixels near saturation,
  strip-edge lines). There is no automatic cancellation exclusion (§7).
- **Inclusion ROI**, the primary tool for a cancelled stamp: the user draws a region
  that is unambiguously only the colour they want measured (clean paper, or clean
  design ink), restricting the analysis to it. Without one, the full stamp interior
  is analysed and a cancellation counts as ink; `analyse_colour` warns when this
  happens.

### 8.2 Colour space

All pixels are converted to CIELAB D50 through the reference model (§4.3).
Presentation uses **LCh(ab)**: L* lightness, C*ab chroma and h_ab hue angle. That is
the cylindrical, HSL-like view René asked for. ΔE00 is the difference metric.

### 8.3 Clustering

- k = 1 (paper) + N inks. N is chosen by the user, default 1.
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

## 9. Colour naming (philatelic reference, not ISCC–NBS)

**Status (2026-10-04): ISCC–NBS dropped.** The original plan used Centore's ISCC–NBS
block-boundary system (`reference_data/iscc_nbs/`) for colour naming. René flagged
this as flawed for stamp work and asked for a dedicated reference built from
stamp-catalogue/printing-ink vocabulary instead (carmine, ultramarine, cobalt blue,
...), combined with an ISCC–NBS-*like* structure (a lightness/chroma modifier
composed with a hue name) rather than Centore's block geometry. `reference_data/
iscc_nbs/` has been deleted; nothing in the codebase references it.

1. Munsell notation is still computed as before (`core/naming.py`): Lab D50 → XYZ D50
   → Bradford chromatic adaptation to **illuminant C** → xyY →
   `colour.notation.munsell.xyY_to_munsell_colour`. This stands on its own as a
   standard scientific colour specification; it no longer feeds a name lookup.
2. Naming goes through `core/philatelic_colours.py` instead, directly from Lab (no
   Munsell step): nearest reference colour by ΔE00 against
   `reference_data/philatelic_colours.csv`, then a modifier ("pale"/"light",
   "dark"/"deep", "grayish", "vivid") from how the input's L*/C* compare to the
   matched entry's own L*/C* — e.g. "deep carmine", "grayish ultramarine".
3. **Provenance caveat, load-bearing for anyone using this for catalogue work:** the
   reference table is a compilation from general colour-science/printing-ink
   knowledge of what these classic names typically look like on paper. It is *not*
   digitised from a specific philatelic catalogue (Yvert, Stanley Gibbons, Michel) or
   measured from real ink swatches. Good for a human-readable, domain-appropriate name
   and for comparing stamps to each other; not yet calibrated catalogue ground truth.
   Replacing or extending the CSV with measured/catalogue-sourced values is the
   intended upgrade path if exact catalogue-colour matching is needed later.
4. Output: the full name (e.g. "deep grayish bluish green"), the matched reference
   entry's own name and family, the ΔE00 to that match (how confident the name is —
   surfaced, not hidden), and the Munsell notation.
5. **Colour overview** (SPEC §10 extension, requested by René): all of a project's
   ink clusters (across every stamp) plotted together in CIELAB a*b* and CIELCh
   (C*-h polar), via `core.colour.render_colour_overview`. Embedded as its own section
   on the report's overview page and also written standalone as
   `<project>/colour_overview.png`, so stamps can be compared to each other directly,
   not just read one at a time.

Note on the report that the Munsell notation assumes illuminant C viewing.

## 10. Report

A4 PDF, comparison first (rewritten 2026-10-12 at René's request -- the earlier
one-page-per-stamp layout with full diagnostics was too detailed):

1. **Summary**: counts; calibration references with resolution, scale, colour model and
   cross-validated ΔE00; ink colour per catalogue number (mean LCh, max ΔE00 from the
   group mean, perforations seen); perforation × catalogue-number counts; a short
   methods note.
2. **Sheets**: each sheet in calibrated colour with the current stamp rectangles and
   stamp numbers.
3. **Comparison charts** (left out when there is nothing to compare): ink colour (a*b*
   and L*-C*) by catalogue number, by perforation, by cancellation year (continuous
   scale; estimated dates and undated stamps marked), by cancellation type; shade (h,
   C*) over time; horizontal vs vertical gauge map. Points carry stamp numbers. Only
   ink colours ticked in the project overview are used.
4. **Index**: one line per stamp.
5. **Stamp cards**, four per page: a large calibrated-colour picture, perforation
   (notation, per-side gauges, "by hand"), cancellation, colours, size, flags, notes.

Per-side perforation diagnostics stay in the project folder and the GUI. CSV and JSON
exports carry the per-stamp data; `colour_overview.png` is the first colour chart.

## 11. Test data, ground truth, validation

- Test data location (inside the project folder, but gitignored -- never committed):
  `/home/limhes/git/stamps/stamp_analyzer/testdata/`
  - `reference/`: the IT8 target scan(s), with the `scanimage -A` dump
  - `samples/`: sample sheet scans
  - `ground_truth.yaml`: per stamp, the hand-gauged perforation per side, imperf
    sides, whether it's cancelled, the expected shade name/catalogue colour, and notes
- Read the test-data path from the env var `STAMPAN_TESTDATA`. Tests that need real
  data are skipped when it's not set.
- Every phase adds a script in `scripts/` that runs its module over all test data and
  writes diagnostic images plus a summary table to `out/<phase>/`, for René's review.

## 12. GUI (PySide6)

- **Main window.** Menu bar (File / Edit / View / Tools / Project / Help, with
  keyboard shortcuts), a main toolbar (New Project, New Reference, Add Sheet, Generate
  Report), and three areas:
  - *Library* dock (left): Projects → Sheets → Stamps, then References (archived
    ones hidden). Each stamp carries a status dot (nothing / partly / fully measured).
    Context menu per item.
  - *Image* (centre): the QGraphicsView viewer with its own tool bar -- pan,
    rectangle, ellipse, polygon, brush; add/erase; brush size; undo/redo/clear; zoom
    out / % / in / fit / 1:1. Area tools are enabled only while a colour area is being
    edited. An empty state is shown when nothing is loaded.
  - *Inspector* dock (right): the page for the selected item -- Project (statistics,
    colour overview, report), Sheet (detected stamps, adjust rectangle, add a missed
    stamp), Stamp (Details / Perforation / Colour tabs, previous/next), Reference
    (accuracy, scale, scanner settings, diagnostics; rename/archive/compare).
- **Project comparison** (centre area while a project is selected): one row per measured
  colour with Lab/LCh, perforation, catalogue no. and cancellation; search, filter (inks/paper,
  perforation "X on all sides" / "X on any side" / exact notation, cancellation type, year
  range), sort on any column, CSV export. Linked a*b* and L*–C* plots show the visible, ticked
  rows, coloured by measured colour or grouped by perforation, cancellation, sheet or catalogue
  no.; hover names a point, clicking selects its row.
- **Perforation override** (§6.2): per side, kind and gauge set by hand with an optional reason;
  stored in `manual` next to the measured result, shown as "by hand", flagged on the report.
- **Cancellation** (Details tab): type from a user-defined list (Preferences), date as YYYY,
  YYYY-MM or YYYY-MM-DD, and whether it is estimated rather than read from a legible postmark.
- **Dialogs:** Add Sheet (reference + scanner or TIFF, validated before OK); New
  Reference wizard (acquire → describe → review with per-model ΔE00 table and the
  patch-location diagnostic → save).
- **Status bar:** a single busy indicator for all background work, and the pixel
  readout (px, mm, L*a*b* through the colour model, else RGB).
- Long computations run in worker threads (one per page, newest request replaces a
  queued one). Errors show the message with the traceback behind "Show Details".
- Window layout and library folder persist between sessions (QSettings).
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
| `../rnd_projects/rgb_to_munsell/sRGBToMunsellAndISCCNBS.csv` | Centore's conversion table | n/a (§9 no longer uses ISCC–NBS) | n/a |

Note (2026-10-04): the rows above that point at ISCC–NBS as "the authority" for colour
naming are superseded by §9's decision to drop it. `../ColorIdentification/color_key`'s
"200 colour names and the idea" is now the more relevant lead if
`reference_data/philatelic_colours.csv` is ever upgraded with catalogue-sourced
values -- but per that row's own warning, `color_key_calibrated.csv`'s Lab values
are themselves biased and would need re-measuring through this project's own
characterisation pipeline first, not copied as-is.
| `../rnd_projects/stamp_feature_detect`, `../__android` | Machin template matching; an Android skeleton with a mocked perforation result | Out of scope | n/a |

Do not modify anything outside `stamp_analyzer/` (which now also contains `testdata/`).

## 14. Future (design hooks only)

Glassine correction, a check-patch drift monitor, a shade library with Stanley
Gibbons key references, learned cancellation segmentation, syncopated perforation,
watermark imaging, and batch re-analysis when a reference is replaced.
