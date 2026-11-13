# Published format (`stampan.published/1`)

Stamp Analyzer keeps a read-only export of every project for other software, such as a
website. Read these files only; the rest of the library is internal and may change
between versions. The export is rewritten automatically whenever a project changes.

```
<library>/published.json                       index of all published projects
<library>/projects/<project_id>/published/
  project.json                                 everything below
  table.csv                                    the overview table (as in the app's export)
  stamps/<stamp_id>.webp                       full-resolution picture
  stamps/<stamp_id>_thumb.webp                 400 px on the long side
```

All paths inside a `project.json` are relative to its own `published/` folder.

## `published.json`

```json
{
  "schema": "stampan.published/1",
  "projects": [
    {"id": "25_penni_red_20261010T065924Z", "name": "25 penni red",
     "path": "projects/25_penni_red_20261010T065924Z/published/project.json",
     "updated": "2026-11-09T08:48:00+00:00", "stamps": 57}
  ]
}
```

## `project.json`

```jsonc
{
  "schema": "stampan.published/1",
  "generator": "Stamp Analyzer 0.1.0",
  "project": {"id": "…", "name": "25 penni red", "created": "2026-10-10",
              "notes": null, "updated": "2026-11-09T08:48:00+00:00"},
  "references": [{
    "id": "ref_20261009T110516Z", "name": "test12", "created": "2026-10-09",
    "resolution_dpi": 1200, "px_per_mm": [47.244, 47.244],
    "colour_model": {"kind": "lut_delaunay", "cv_delta_e00_mean": 1.25, "cv_delta_e00_p95": 3.84}
  }],
  "sheets": [{"id": "sample_001", "title": "Sheet 1", "scanned": "…",
              "reference": "ref_20261009T110516Z", "stamps": ["sample_001_stamp000", "…"]}],
  "stamps": [{
    "id": "sample_001_stamp000",        // stable: usable in URLs
    "sheet": "sample_001", "number": 1, "label": "Stamp 1",
    "catalogue": ["17L"], "country_issue": null, "notes": null,
    "condition": "on_paper",            // "soaked_off" | "on_paper" | null (not recorded)
    "multiplicity": "single", "multiplicity_count": null,
    "size_mm": [29.27, 28.91],
    "cancellation": {"type": "Circular date stamp", "date": "1883-10-01", "estimated": true,
                     "place": "Helsingfors"},
    "perforation": {                    // null when neither measured nor set by hand
      "notation": "12 1/2 x 12 1/2",    // top/bottom x left/right; "a/b" = opposite sides differ
      "by_hand": true, "reason": null, "confidence": 0.8, "warnings": ["…"],
      "sides": {
        "top": {"kind": "perforated", "gauge": 12.5, "notation": "12 1/2", "by_hand": true,
                "measured": {"kind": "perforated", "gauge": 12.47, "notation": "12 1/2",
                             "uncertainty": 0.05, "holes": 10}},
        "…": {}
      }
    },
    "colours": [{
      "label": "ink", "display_label": null, "is_paper": false, "in_overview": true,
      "lab": [48.27, 45.78, 20.24], "lab_std": [1.3, 1.57, 2.27],
      "lch": [48.27, 50.1, 23.85], "delta_e00_spread": 1.58, "pixel_count": 5930,
      "philatelic_name": "rose red", "philatelic_match_delta_e00": 6.24,
      "munsell": "4.1R 4.7/10.1", "srgb": "#bc4d53", "out_of_gamut": false
    }],
    "image": "stamps/sample_001_stamp000.webp",
    "thumbnail": "stamps/sample_001_stamp000_thumb.webp",
    "image_size_px": [1667, 1650],
    "image_calibrated": true
  }],
  "table": "table.csv"
}
```

## Meaning of the values

- **Colour.** `lab` is the mean CIELAB (D50, 2° observer) of the colour area, measured on
  linear scanner RGB through the reference's characterisation model. `lch` is CIE LCh(ab):
  L*, C*, h in degrees. `lab_std` is the per-channel standard deviation over the area, and
  `delta_e00_spread` is the area's ΔE00 spread. The reference's cross-validated ΔE00 is the
  accuracy to expect. `srgb` is for display only. `in_overview` is the user's choice of
  colours for comparison charts; `display_label`, when set, is the name to show there.
- **Perforation.** Gauges are teeth per 2 cm; `notation` is rounded to the nearest quarter.
  `by_hand` marks values set by the user; the measured value is always kept under
  `measured`. `kind` is `perforated`, `imperf` or `straight_edge`.
- **Cancellation.** The date is ISO and only as precise as known (`1889`, `1889-03` or
  `1889-03-14`). `estimated` means it was inferred, not read from a legible postmark. `place` is the
  postmark's place name exactly as the user typed it (any script or language), or null.
- **Pictures.** Rendered from the 16-bit scan through the colour model to sRGB. When
  `image_calibrated` is false, the reference had no colour model and the picture is only
  a contrast stretch. Stamps lie as they were cropped; the margin around the teeth is
  about 3 mm of the stock card.

## Compatibility

Fields may be added within `stampan.published/1`; readers should ignore fields they do
not know. Renaming or removing a field, or changing its meaning, changes the schema
version.
