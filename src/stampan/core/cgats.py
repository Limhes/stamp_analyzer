"""CGATS/IT8.7 parser.

Parses the subset of the CGATS.5 format used by Wolf Faust IT8.7/2 reference
files: scalar header keywords, a BEGIN_DATA_FORMAT/END_DATA_FORMAT field list,
and a BEGIN_DATA/END_DATA block of whitespace-separated rows. Handles
trailing whitespace, blank lines, and ``#`` comments (outside quoted
strings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# D50 2 degree observer reference white (ASTM E308), consistent with the
# CGATS data in reference_data/R240103.txt (Y scaled to 100 = perfect
# reflecting diffuser).
D50_WHITE_XYZ = (96.422, 100.000, 82.521)


def _strip_comment(line: str) -> str:
    """Remove a trailing ``# ...`` comment, respecting double-quoted strings."""
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "#" and not in_quotes:
            return line[:i]
    return line


def _parse_scalar(token: str) -> str | float | int:
    """Parse a single header value token: quoted string, int, float, or bare word."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def _parse_value_cell(token: str) -> str | float:
    """Parse a single data-row cell: quoted string stays a string, else float."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    try:
        return float(token)
    except ValueError:
        return token


@dataclass
class CGATSTable:
    """A parsed CGATS file: scalar header keywords plus a data table."""

    header: dict[str, str | float | int] = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)
    rows: list[dict[str, str | float]] = field(default_factory=list)

    def by_sample_id(self) -> dict[str, dict[str, str | float]]:
        """Index rows by SAMPLE_ID (or SAMPLE_NAME if SAMPLE_ID is absent)."""
        key_field = "SAMPLE_ID" if "SAMPLE_ID" in self.fields else "SAMPLE_NAME"
        return {str(row[key_field]): row for row in self.rows}


def parse_cgats(text: str) -> CGATSTable:
    """Parse CGATS/IT8.7 text into a :class:`CGATSTable`."""
    table = CGATSTable()
    in_format = False
    in_data = False

    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue

        if line == "BEGIN_DATA_FORMAT":
            in_format = True
            continue
        if line == "END_DATA_FORMAT":
            in_format = False
            continue
        if line == "BEGIN_DATA":
            in_data = True
            continue
        if line == "END_DATA":
            in_data = False
            continue

        if in_format:
            table.fields.extend(line.split())
            continue

        if in_data:
            cells = line.split()
            if not table.fields:
                raise ValueError("DATA block encountered before DATA_FORMAT")
            if len(cells) != len(table.fields):
                raise ValueError(
                    f"data row has {len(cells)} cells, expected {len(table.fields)}: {line!r}"
                )
            row = {f: _parse_value_cell(c) for f, c in zip(table.fields, cells, strict=True)}
            table.rows.append(row)
            continue

        # Plain header keyword line, e.g. ORIGINATOR "Wolf Faust" or NUMBER_OF_SETS 288.
        parts = line.split(maxsplit=1)
        key = parts[0]
        value = _parse_scalar(parts[1]) if len(parts) > 1 else ""
        table.header[key] = value

    return table


def parse_cgats_file(path: str | Path) -> CGATSTable:
    return parse_cgats(Path(path).read_text(encoding="utf-8", errors="replace"))


def lab_from_xyz_d50(x: float, y: float, z: float) -> tuple[float, float, float]:
    """CIE XYZ (D50, Y scaled to 100) -> CIELAB D50, standard CIE formula."""
    xn, yn, zn = D50_WHITE_XYZ

    def f(t: float) -> float:
        delta = 6.0 / 29.0
        if t > delta**3:
            return t ** (1.0 / 3.0)
        return t / (3 * delta**2) + 4.0 / 29.0

    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    lightness = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return lightness, a, b
