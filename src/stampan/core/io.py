"""16-bit TIFF read/write with embedded JSON metadata.

Scans and crops are stored as 16-bit TIFFs. Provenance (scan timestamp,
scanner options, reference id used for calibration, free-form notes) is
embedded in the TIFF's ImageDescription tag as JSON, so a single file is
self-describing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import tifffile


@dataclass
class ScanMetadata:
    """Provenance embedded in a scan/crop TIFF."""

    scan_time: str | None = None  # ISO 8601
    scanner_options: dict[str, Any] = field(default_factory=dict)
    ref_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> ScanMetadata:
        data = json.loads(text) if text else {}
        return cls(
            scan_time=data.get("scan_time"),
            scanner_options=data.get("scanner_options", {}),
            ref_id=data.get("ref_id"),
            extra=data.get("extra", {}),
        )


def write_tiff(
    path: str | Path,
    image: np.ndarray,
    metadata: ScanMetadata | None = None,
) -> None:
    """Write a 16-bit image to TIFF with ScanMetadata embedded as JSON.

    ``image`` must be uint16, shape (H, W) or (H, W, C).
    """
    if image.dtype != np.uint16:
        raise ValueError(f"expected uint16 image, got {image.dtype}")
    description = (metadata or ScanMetadata()).to_json()
    photometric = "minisblack" if image.ndim == 2 else "rgb"
    tifffile.imwrite(
        path,
        image,
        photometric=photometric,
        description=description,
        metadata=None,  # avoid tifffile's own shaped-metadata XML; we use ImageDescription
    )


def read_tiff(path: str | Path) -> tuple[np.ndarray, ScanMetadata]:
    """Read a 16-bit TIFF written by :func:`write_tiff`, with its metadata."""
    with tifffile.TiffFile(str(path)) as tif:
        image = tif.asarray()
        description = tif.pages[0].description or ""
    metadata = ScanMetadata.from_json(description)
    return image, metadata
