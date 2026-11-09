"""The library as a whole: which files are the record, which are raw data
and which are a rebuildable cache (SPEC §3.1).

- **Record** -- small text and PNG files holding all the work (stamp.json,
  colour areas, references' colour models) plus the published export.
  Tracked in git.
- **Raw** -- the 16-bit scans. Written once and never changed, hundreds of
  MB each: backed up separately (Nextcloud, NAS, restic), not in git.
- **Cache** -- stamp crops and diagnostic images, rebuilt on demand.
"""

from __future__ import annotations

from pathlib import Path

GITIGNORE = """\
# Stamp Analyzer library (written by Stamp Analyzer; edit freely).
#
# Raw scans: hundreds of MB, never changed after scanning. Back them up
# separately (Nextcloud, NAS, restic) -- not in git.
*.tif
*.tiff

# Cache: rebuilt on demand (stamp crops are re-cut from the sheet scan,
# diagnostics are re-rendered by measuring again).
diagnostics/
*.partial
*.partial.*

# Old-style PDF reports and their side files.
/projects/*/report.*
/projects/*/colour_overview.png
"""

README = """\
# Stamp library

Made with Stamp Analyzer. What lives where:

- `published.json`: index of all published projects. *In git.*
- `projects/<id>/published/`: **the stable export for other software** --
  `project.json`, `table.csv` and the stamp pictures (WebP). *In git.*
- `projects/<id>/project.json`, `sample_*.json`, `stamps/*/stamp.json`: the
  working record, measured and hand-set values. *In git.*
- `projects/<id>/stamps/*/colour_rois/*.png`: colour areas drawn by hand. *In git.*
- `references/<id>/reference.json`, `model.npz`: calibration (scale, colour
  model). *In git.*
- `*.tif`: raw 16-bit scans (sheets, IT8 targets). *Not in git: back up
  separately.*
- `stamps/*/crop.tif`, `diagnostics/`: cache, rebuilt when missing. *Not in git.*

Read `published/` from other software; the other files are internal and may
change between versions. The format is described in Stamp Analyzer's
`docs/published-format.md`.
"""


def ensure_layout(library_root: str | Path) -> Path:
    """Create the library folder with its .gitignore and README if they are
    missing. Existing files are left alone: they may have been edited."""
    root = Path(library_root)
    root.mkdir(parents=True, exist_ok=True)
    for name, text in ((".gitignore", GITIGNORE), ("README.md", README)):
        path = root / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    return root
