from stampan.core import models as models_mod
from stampan.core.models import (
    AnalysisMeta,
    ColourCluster,
    ColourModelInfo,
    ColourResult,
    GeometryResult,
    PerforationResult,
    PerforationSide,
    Reference,
    Sample,
    ScaleCalibration,
    Stamp,
)


def _sample_stamp() -> Stamp:
    perforation = PerforationResult(
        auto={
            "top": PerforationSide(gauge=11.92, uncertainty=0.03, notation="12", hole_count=14),
            "bottom": PerforationSide(gauge=11.88, uncertainty=0.04, notation="12", hole_count=14),
            "left": PerforationSide(kind="imperf"),
            "right": PerforationSide(kind="straight_edge"),
        },
        manual={"left": PerforationSide(kind="perforated", gauge=12.0)},
        manual_reason={"left": "auto mis-detected imperf; holes visible under magnification"},
        meta=AnalysisMeta(confidence=0.92, warnings=["corner exclusion widened"]),
    )
    colour = ColourResult(
        n_inks=1,
        tau=0.9,
        clusters=[
            ColourCluster(
                label="paper",
                pixel_count=120_000,
                area_fraction=0.82,
                mean_lab=(88.1, -1.2, 3.4),
                std_lab=(1.1, 0.4, 0.5),
                mean_lch=(88.1, 3.6, 109.4),
                munsell="N 9/",
                philatelic_name="white",
                philatelic_match_delta_e00=4.2,
                srgb_swatch=(230, 228, 220),
            ),
            ColourCluster(
                label="ink1",
                pixel_count=26_000,
                area_fraction=0.18,
                mean_lab=(32.0, 45.0, -20.0),
                munsell="5P 3/8",
                philatelic_name="deep carmine",
                philatelic_match_delta_e00=6.1,
            ),
        ],
        mixed_pixel_fraction=0.06,
        ink_paper_delta_e00={"ink1": 48.3},
        meta=AnalysisMeta(confidence=0.95),
    )
    return Stamp(
        stamp_id="stamp_0007",
        crop_path="stamps/stamp_0007/crop.tif",
        catalogue_numbers=["NVPH 12", "Mi 10"],
        country_issue="Netherlands 1872",
        notes="corner crease",
        condition=models_mod.CONDITION_SOAKED_OFF,
        multiplicity=models_mod.MULTIPLICITY_BLOCK,
        multiplicity_count=4,
        geometry=GeometryResult(width_mm=20.1, height_mm=25.3, rotation_deg=0.4),
        perforation=perforation,
        colour=colour,
    )


def test_stamp_json_roundtrip():
    stamp = _sample_stamp()
    restored = Stamp.from_json(stamp.to_json())
    assert restored == stamp


def test_stamp_condition_and_multiplicity_default_to_unrecorded():
    # Never assumed -- these change which perforation approach is correct,
    # so "unknown" must stay distinguishable from any real value.
    stamp = Stamp(stamp_id="s1")
    assert stamp.condition is None
    assert stamp.multiplicity is None
    assert stamp.multiplicity_count is None
    restored = Stamp.from_json(stamp.to_json())
    assert restored == stamp


def test_perforation_auto_manual_kept_separate():
    stamp = _sample_stamp()
    # Manual override for "left" must not touch the automatic result.
    assert stamp.perforation.auto["left"].kind == "imperf"
    assert stamp.perforation.manual["left"].kind == "perforated"
    assert "left" in stamp.perforation.manual_reason


def test_reference_json_roundtrip():
    ref = Reference(
        ref_id="ref_20261002",
        name="Epson 1200U / R240103",
        created="2026-10-02",
        notes="first reference",
        scanner_options={"resolution": 1200, "depth": 16, "mode": "Color"},
        scale=ScaleCalibration(
            px_per_mm_x=47.24, px_per_mm_y=47.19, uncertainty_x=0.01, uncertainty_y=0.01
        ),
        colour_model=ColourModelInfo(
            kind="root_poly2",
            model_path="model.npz",
            train_delta_e00={"mean": 0.8, "median": 0.6, "p95": 2.1, "max": 3.9},
            cv_delta_e00={"mean": 1.1, "median": 0.9, "p95": 2.8, "max": 4.5},
        ),
        scan_path="reference_scan.tif",
    )
    restored = Reference.from_json(ref.to_json())
    assert restored == ref


def test_sample_json_roundtrip():
    sample = Sample(
        sample_id="sample_0001",
        project_id="project_demo",
        scan_path="sample_0001.tif",
        ref_id="ref_20261002",
        scan_time="2026-10-02T14:03:00",
        stamp_ids=["stamp_0001", "stamp_0002"],
    )
    restored = Sample.from_json(sample.to_json())
    assert restored == sample


def test_reference_without_scale_or_model_roundtrips():
    ref = Reference(ref_id="ref_bare", name="bare", created="2026-10-02")
    restored = Reference.from_json(ref.to_json())
    assert restored == ref
    assert restored.scale is None
    assert restored.colour_model is None
