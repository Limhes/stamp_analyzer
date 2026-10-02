# Stamp Analyzer: instructions for Claude Code

Desktop application for measuring postage stamps from calibrated flatbed scans:
perforation gauge per side, cancellation segmentation, and calibrated ink/paper
colour (CIELAB/LCh D50, ΔE00, ISCC–NBS names). The output is a PDF report with one page per stamp.

The full design is in `docs/SPEC.md`. Read it before starting any phase.
The development phases and their acceptance criteria are in `docs/PHASES.md`.

This project lives in `/home/limhes/git/stamps/stamp_analyzer/`. The sibling folders
(`../rnd_projects`, `../ColorIdentification`, `../__android`) hold René's earlier
experiments. SPEC §13 says what is worth borrowing and what is wrong. Never modify
anything outside `stamp_analyzer/`. Test data lives at `stamp_analyzer/testdata/`
(gitignored; see §11 below).

## Who you are working with

René is an electrical engineer (scientific instrumentation, optics) with a PhD in
chemistry, and a specialist philatelist. Explain colour-science and image-processing
choices at an expert level. Do not simplify, and do not hand-wave.

## Working rules

- **Work one phase at a time.** At the end of a phase, stop and give a short
  report: what was built, the validation numbers, the diagnostic images produced,
  and known weaknesses. Then wait for René's review before starting the next phase.
- **Never invent measurement results.** If an algorithm is unsure, it must say
  so (confidence fields, warnings) instead of returning a plausible-looking number.
- **Every analysis step produces a diagnostic image** that shows what the algorithm
  "saw" (edges, detected holes, masks, clusters). These diagnostic images are
  first-class outputs, not debug leftovers.
- **Keep the core library GUI-free.** `stampan.core.*` must not import Qt. The GUI
  and CLI are thin layers over it.
- **Physical units.** Work in pixels internally. Convert to mm only through the
  calibration object (separate x and y scales), and never through a hard-coded dpi.
- **Colour.** All colour maths runs in float64 on linear scanner RGB, which the
  characterisation model maps to CIE XYZ D50 (2°) and then to CIELAB D50. 8-bit sRGB
  is only for display.
- **Test data** lives in `stamp_analyzer/testdata/` (see `docs/SPEC.md` §11) and is
  gitignored -- never committed. Do not commit scans larger than 1 MB directly;
  small synthetic fixtures are fine.
- When a spec decision turns out to be wrong or ambiguous, propose the change. Do
  not silently deviate from the spec.

## Tech stack

- Python 3.12, environment managed with `uv`.
- numpy, scipy, opencv-python-headless, scikit-image, scikit-learn,
  colour-science, tifffile.
- PySide6 and pyqtgraph (including `pyqtgraph.opengl`) for the GUI.
- Jinja2 and WeasyPrint for reports.
- pytest for tests and ruff for linting/formatting.
- ArgyllCMS (system package, optional) as a cross-check for the colour profile.
- SANE through `scanimage` (subprocess); `python-sane` is optional.

## Commands

```bash
uv sync                      # install
uv run pytest                # tests
uv run ruff check . && uv run ruff format .
uv run stampan --help        # CLI
uv run stampan-gui           # GUI (from phase 7)
```
