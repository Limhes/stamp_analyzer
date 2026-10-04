"""The image viewer: zoom/pan, a px/mm/RGB/Lab pixel readout, and the
inclusion/exclusion ROI tools (SPEC §12).

Supports several independently-named ROIs at once (one per colour the user
wants measured, SPEC §8's ROI-first colour workflow) -- one is "active" at a
time (drawing/erasing affects only it; only it is shown as an overlay), and
``set_active_roi``/``roi_names`` let a panel switch between them. Mask
editing (fill/erase a polygon or brush stroke) is built directly with
OpenCV primitives (``cv2.fillPoly``/``cv2.circle``/``cv2.ellipse``).
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from stampan.core import colour as colour_mod

TOOL_PAN = "pan"
TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"
TOOL_POLYGON = "polygon"
TOOL_BRUSH = "brush"
TOOL_PERF_BOX = "perf_box"

MODE_INCLUDE = "include"
MODE_ERASE = "erase"

_MASK_COLOUR_ACTIVE = QColor(60, 200, 90, 100)

# The editable perforation-box overlay (SPEC §6: "you see the four
# autodetected lines, that the user can then change if necessary ... resize
# and rotate using handles"). Corner handles resize (keeping the opposite
# corner fixed and the current orientation); the separate rotate handle
# above the top edge rotates about the box centre, keeping its size fixed.
_PERF_BOX_COLOUR = QColor(255, 210, 0)
_PERF_ROTATE_COLOUR = QColor(0, 220, 220)
_PERF_HANDLE_RADIUS_PX = 10.0
_PERF_HANDLE_HIT_PX = 26.0
_PERF_ROTATE_OFFSET_PX = 28.0
_PERF_MIN_HALF_SIZE_PX = 5.0
_PERF_CORNER_SIGNS = {0: (-1, -1), 1: (1, -1), 2: (1, 1), 3: (-1, 1)}


def _display_image(rgb: np.ndarray) -> np.ndarray:
    """16-bit scanner RGB -> an 8-bit preview. A simple per-channel
    normalise, not the colour-managed Lab round-trip used for the report's
    calibrated photo (SPEC §10) -- good enough for on-screen navigation,
    and much cheaper to redraw interactively."""
    return cv2.normalize(rgb, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)


def _to_qimage(rgb8: np.ndarray) -> QImage:
    h, w = rgb8.shape[:2]
    contiguous = np.ascontiguousarray(rgb8)
    return QImage(contiguous.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class ImageViewer(QGraphicsView):
    pixel_info = Signal(object)  # dict or None
    roi_changed = Signal(str)  # the active ROI's name, whenever its mask changes
    perf_box_changed = Signal()  # the perforation-box overlay geometry changed

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self._pixmap_item = None
        self._mask_item = None
        self._rgb: np.ndarray | None = None

        self._rois: dict[str, np.ndarray] = {}
        self._undo_stacks: dict[str, list[np.ndarray]] = {}
        self._redo_stacks: dict[str, list[np.ndarray]] = {}
        self._active_roi: str | None = None

        self._px_per_mm_x: float | None = None
        self._px_per_mm_y: float | None = None
        self._predict_lab_fn = None

        self._tool = TOOL_PAN
        self._mode = MODE_INCLUDE
        self._brush_radius_px = 15.0

        self._drawing = False
        self._start_pt: QPointF | None = None
        self._polygon_pts: list[QPointF] = []
        self._preview_item = None

        # perforation-box overlay state: (cx, cy, half_w, half_h, theta_rad)
        self._perf_box: tuple[float, float, float, float, float] | None = None
        self._perf_box_items: list = []
        self._perf_drag_handle: int | None = None
        self._perf_drag_start: tuple[float, float, float, float, float] | None = None
        self._perf_drag_start_corners: np.ndarray | None = None

    # -- loading -----------------------------------------------------

    def set_image(
        self,
        rgb: np.ndarray,
        px_per_mm_x: float | None = None,
        px_per_mm_y: float | None = None,
        predict_lab_fn=None,
    ) -> None:
        self._rgb = rgb
        self._px_per_mm_x = px_per_mm_x
        self._px_per_mm_y = px_per_mm_y
        self._predict_lab_fn = predict_lab_fn
        self._rois.clear()
        self._undo_stacks.clear()
        self._redo_stacks.clear()
        self._active_roi = None
        self._perf_box = None
        self._perf_box_items = []

        self._scene.clear()
        pixmap = QPixmap.fromImage(_to_qimage(_display_image(rgb)))
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self._mask_item = self._scene.addPixmap(QPixmap())
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # -- ROI management ----------------------------------------------------

    def roi_names(self) -> list[str]:
        return list(self._rois.keys())

    def set_active_roi(self, name: str | None, initial_mask: np.ndarray | None = None) -> None:
        """Make ``name`` the ROI that drawing/erasing affects and that's
        shown as the overlay. Creates it (empty, or from ``initial_mask`` --
        e.g. loaded from disk) if it doesn't exist yet."""
        self._active_roi = name
        if name is not None and name not in self._rois:
            if self._rgb is None:
                return
            self._rois[name] = (
                initial_mask.copy()
                if initial_mask is not None
                else np.zeros(self._rgb.shape[:2], dtype=np.uint8)
            )
            self._undo_stacks[name] = []
            self._redo_stacks[name] = []
        elif name is not None and initial_mask is not None:
            # Re-syncing an existing ROI's mask from disk (e.g. on reload).
            self._rois[name] = initial_mask.copy()
        self._refresh_mask_overlay()

    def remove_roi(self, name: str) -> None:
        self._rois.pop(name, None)
        self._undo_stacks.pop(name, None)
        self._redo_stacks.pop(name, None)
        if self._active_roi == name:
            self._active_roi = None
        self._refresh_mask_overlay()

    def rename_roi(self, old_name: str, new_name: str) -> None:
        if old_name not in self._rois or old_name == new_name:
            return
        self._rois[new_name] = self._rois.pop(old_name)
        self._undo_stacks[new_name] = self._undo_stacks.pop(old_name, [])
        self._redo_stacks[new_name] = self._redo_stacks.pop(old_name, [])
        if self._active_roi == old_name:
            self._active_roi = new_name

    def clear_mask(self) -> None:
        if self._active_roi is None:
            return
        self._push_undo()
        self._rois[self._active_roi][:] = 0
        self._refresh_mask_overlay()
        self.roi_changed.emit(self._active_roi)

    def get_mask(self, name: str | None = None) -> np.ndarray | None:
        target = name or self._active_roi
        if target is None or target not in self._rois:
            return None
        mask = self._rois[target]
        return mask.copy() if np.any(mask) else None

    # -- perforation-box overlay ------------------------------------------

    def set_perf_box(self, corners) -> None:
        """Show (or replace) the editable rectangle, seeded from 4 (x, y)
        px corners in sequential winding order (e.g. ``detect_stamp_box``'s
        auto-detected box, or a previously-saved override)."""
        if corners is None:
            self.clear_perf_box()
            return
        corners = np.asarray(corners, dtype=np.float64)
        centre = corners.mean(axis=0)
        e01 = corners[1] - corners[0]
        e12 = corners[2] - corners[1]
        width = float(np.linalg.norm(e01))
        height = float(np.linalg.norm(e12))
        theta = float(np.arctan2(e01[1], e01[0]))
        self._perf_box = (float(centre[0]), float(centre[1]), width / 2.0, height / 2.0, theta)
        self._refresh_perf_box()

    def get_perf_box(self) -> np.ndarray | None:
        """The 4 corners (px, sequential winding) of the current rectangle,
        or None if none is shown -- feed this straight back into
        ``analyse_perforation(..., manual_box_px=...)``."""
        if self._perf_box is None:
            return None
        return self._perf_box_corners(self._perf_box)

    def get_perf_box_rect(self) -> tuple[tuple[float, float], tuple[float, float], float] | None:
        """The current rectangle as (centre_px, (width_px, height_px),
        angle_deg) -- the shape ``cv2.minAreaRect``/``DetectedStamp`` use,
        for callers (e.g. manual bounding-box editing, SPEC §5) that want
        centre/size/angle rather than 4 raw corners."""
        if self._perf_box is None:
            return None
        cx, cy, hw, hh, theta = self._perf_box
        return (cx, cy), (2 * hw, 2 * hh), float(np.degrees(theta))

    def clear_perf_box(self) -> None:
        self._perf_box = None
        self._clear_perf_box_items()

    @staticmethod
    def _perf_box_corners(state: tuple[float, float, float, float, float]) -> np.ndarray:
        cx, cy, hw, hh, theta = state
        ex = np.array([np.cos(theta), np.sin(theta)])
        ey = np.array([-np.sin(theta), np.cos(theta)])
        centre = np.array([cx, cy])
        return np.array(
            [
                centre - hw * ex - hh * ey,
                centre + hw * ex - hh * ey,
                centre + hw * ex + hh * ey,
                centre - hw * ex + hh * ey,
            ]
        )

    @staticmethod
    def _perf_rotate_handle_pos(state: tuple[float, float, float, float, float]) -> np.ndarray:
        cx, cy, _hw, hh, theta = state
        ey = np.array([-np.sin(theta), np.cos(theta)])
        return np.array([cx, cy]) - ey * (hh + _PERF_ROTATE_OFFSET_PX)

    def _clear_perf_box_items(self) -> None:
        for item in self._perf_box_items:
            self._scene.removeItem(item)
        self._perf_box_items = []

    def _refresh_perf_box(self) -> None:
        self._clear_perf_box_items()
        if self._perf_box is None:
            return
        corners = self._perf_box_corners(self._perf_box)

        poly_item = QGraphicsPolygonItem(QPolygonF([QPointF(x, y) for x, y in corners]))
        poly_item.setPen(QPen(_PERF_BOX_COLOUR, 2))
        poly_item.setBrush(Qt.BrushStyle.NoBrush)
        poly_item.setZValue(10)
        self._scene.addItem(poly_item)
        self._perf_box_items.append(poly_item)

        for cx, cy in corners:
            handle = QGraphicsEllipseItem(
                cx - _PERF_HANDLE_RADIUS_PX,
                cy - _PERF_HANDLE_RADIUS_PX,
                2 * _PERF_HANDLE_RADIUS_PX,
                2 * _PERF_HANDLE_RADIUS_PX,
            )
            handle.setBrush(_PERF_BOX_COLOUR)
            handle.setPen(QPen(QColor(0, 0, 0), 1))
            handle.setZValue(11)
            self._scene.addItem(handle)
            self._perf_box_items.append(handle)

        rx, ry = self._perf_rotate_handle_pos(self._perf_box)
        rotate_handle = QGraphicsEllipseItem(
            rx - _PERF_HANDLE_RADIUS_PX,
            ry - _PERF_HANDLE_RADIUS_PX,
            2 * _PERF_HANDLE_RADIUS_PX,
            2 * _PERF_HANDLE_RADIUS_PX,
        )
        rotate_handle.setBrush(_PERF_ROTATE_COLOUR)
        rotate_handle.setPen(QPen(QColor(0, 0, 0), 1))
        rotate_handle.setZValue(11)
        self._scene.addItem(rotate_handle)
        self._perf_box_items.append(rotate_handle)

    def _hit_test_perf_handle(self, scene_pt: QPointF) -> int | None:
        if self._perf_box is None:
            return None
        pt = np.array([scene_pt.x(), scene_pt.y()])
        for i, corner in enumerate(self._perf_box_corners(self._perf_box)):
            if np.linalg.norm(pt - corner) <= _PERF_HANDLE_HIT_PX:
                return i
        if np.linalg.norm(pt - self._perf_rotate_handle_pos(self._perf_box)) <= _PERF_HANDLE_HIT_PX:
            return 4
        return None

    def _drag_perf_handle(self, scene_pt: QPointF) -> None:
        mouse = np.array([scene_pt.x(), scene_pt.y()])
        cx0, cy0, hw0, hh0, theta0 = self._perf_drag_start
        if self._perf_drag_handle == 4:  # rotate, about the fixed centre
            v = mouse - np.array([cx0, cy0])
            theta = float(np.arctan2(v[1], v[0])) + np.pi / 2
            self._perf_box = (cx0, cy0, hw0, hh0, theta)
        else:  # corner resize: opposite corner + current orientation fixed
            idx = self._perf_drag_handle
            opposite = self._perf_drag_start_corners[(idx + 2) % 4]
            sign_w, sign_h = _PERF_CORNER_SIGNS[idx]
            ex = np.array([np.cos(theta0), np.sin(theta0)])
            ey = np.array([-np.sin(theta0), np.cos(theta0)])
            v = mouse - opposite
            new_hw = max(sign_w * float(np.dot(v, ex)) / 2.0, _PERF_MIN_HALF_SIZE_PX)
            new_hh = max(sign_h * float(np.dot(v, ey)) / 2.0, _PERF_MIN_HALF_SIZE_PX)
            new_centre = (mouse + opposite) / 2.0
            self._perf_box = (float(new_centre[0]), float(new_centre[1]), new_hw, new_hh, theta0)
        self._refresh_perf_box()
        self.perf_box_changed.emit()

    # -- tool state ----------------------------------------------------

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self._polygon_pts = []
        self._remove_preview()
        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag
            if tool == TOOL_PAN
            else QGraphicsView.DragMode.NoDrag
        )

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def set_brush_radius(self, px: float) -> None:
        self._brush_radius_px = px

    def undo(self) -> None:
        if self._active_roi is None:
            return
        stack = self._undo_stacks.get(self._active_roi, [])
        if not stack:
            return
        self._redo_stacks[self._active_roi].append(self._rois[self._active_roi].copy())
        self._rois[self._active_roi] = stack.pop()
        self._refresh_mask_overlay()
        self.roi_changed.emit(self._active_roi)

    def redo(self) -> None:
        if self._active_roi is None:
            return
        stack = self._redo_stacks.get(self._active_roi, [])
        if not stack:
            return
        self._undo_stacks[self._active_roi].append(self._rois[self._active_roi].copy())
        self._rois[self._active_roi] = stack.pop()
        self._refresh_mask_overlay()
        self.roi_changed.emit(self._active_roi)

    def _push_undo(self) -> None:
        if self._active_roi is not None:
            self._undo_stacks[self._active_roi].append(self._rois[self._active_roi].copy())
            self._redo_stacks[self._active_roi].clear()

    def _active_mask(self) -> np.ndarray | None:
        return self._rois.get(self._active_roi) if self._active_roi is not None else None

    # -- rendering -------------------------------------------------------

    def _refresh_mask_overlay(self) -> None:
        if self._mask_item is None or self._rgb is None:
            return
        mask = self._active_mask()
        h, w = self._rgb.shape[:2]
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        if mask is not None:
            rgba[mask > 0] = (
                _MASK_COLOUR_ACTIVE.red(),
                _MASK_COLOUR_ACTIVE.green(),
                _MASK_COLOUR_ACTIVE.blue(),
                _MASK_COLOUR_ACTIVE.alpha(),
            )
        image = QImage(np.ascontiguousarray(rgba).data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
        self._mask_item.setPixmap(QPixmap.fromImage(image.copy()))

    def _remove_preview(self) -> None:
        if self._preview_item is not None:
            self._scene.removeItem(self._preview_item)
            self._preview_item = None

    # -- zoom --------------------------------------------------------

    def wheelEvent(self, event) -> None:  # noqa: N802 -- Qt override
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    # -- pixel readout + drawing ------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        if self._rgb is None:
            return
        scene_pt = self.mapToScene(event.pos())
        self._emit_pixel_info(scene_pt)
        if self._drawing and self._tool == TOOL_PERF_BOX and self._perf_drag_handle is not None:
            self._drag_perf_handle(scene_pt)
        elif self._drawing and self._tool == TOOL_BRUSH:
            self._stamp_brush(scene_pt)
        elif self._drawing and self._tool in (TOOL_RECT, TOOL_ELLIPSE):
            self._update_shape_preview(scene_pt)

    def _emit_pixel_info(self, scene_pt: QPointF) -> None:
        x, y = int(scene_pt.x()), int(scene_pt.y())
        h, w = self._rgb.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            self.pixel_info.emit(None)
            return
        rgb_val = tuple(int(v) for v in self._rgb[y, x])
        info = {"x": x, "y": y, "rgb": rgb_val, "mm": None, "lab": None}
        if self._px_per_mm_x and self._px_per_mm_y:
            info["mm"] = (x / self._px_per_mm_x, y / self._px_per_mm_y)
        if self._predict_lab_fn is not None:
            lab = colour_mod.rgb_to_lab_d50(self._rgb[y : y + 1, x : x + 1], self._predict_lab_fn)[
                0, 0
            ]
            if np.all(np.isfinite(lab)):
                info["lab"] = tuple(float(v) for v in lab)
        self.pixel_info.emit(info)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            self._tool == TOOL_PERF_BOX
            and self._perf_box is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            scene_pt = self.mapToScene(event.pos())
            handle = self._hit_test_perf_handle(scene_pt)
            if handle is not None:
                self._perf_drag_handle = handle
                self._perf_drag_start = self._perf_box
                self._perf_drag_start_corners = self._perf_box_corners(self._perf_box)
                self._drawing = True
                return
            super().mousePressEvent(event)
            return
        if (
            self._rgb is None
            or self._tool == TOOL_PAN
            or self._active_roi is None
            or event.button() != Qt.MouseButton.LeftButton
        ):
            super().mousePressEvent(event)
            return
        scene_pt = self.mapToScene(event.pos())
        if self._tool == TOOL_POLYGON:
            self._polygon_pts.append(scene_pt)
            self._update_polygon_preview()
            return
        self._drawing = True
        self._start_pt = scene_pt
        self._push_undo()
        if self._tool == TOOL_BRUSH:
            self._stamp_brush(scene_pt)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drawing and self._tool == TOOL_PERF_BOX and self._perf_drag_handle is not None:
            self._perf_drag_handle = None
            self._perf_drag_start = None
            self._perf_drag_start_corners = None
            self._drawing = False
            return
        if self._drawing and self._tool in (TOOL_RECT, TOOL_ELLIPSE):
            self._commit_shape(self.mapToScene(event.pos()))
        self._drawing = False
        if self._tool != TOOL_POLYGON:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._tool == TOOL_POLYGON and len(self._polygon_pts) >= 3:
            self._commit_polygon()
        else:
            super().mouseDoubleClickEvent(event)

    def _update_shape_preview(self, scene_pt: QPointF) -> None:
        self._remove_preview()
        rect = QRectF(self._start_pt, scene_pt).normalized()
        pen = QPen(_MASK_COLOUR_ACTIVE, 2)
        if self._tool == TOOL_RECT:
            self._preview_item = QGraphicsRectItem(rect)
        else:
            self._preview_item = QGraphicsEllipseItem(rect)
        self._preview_item.setPen(pen)
        self._scene.addItem(self._preview_item)

    def _commit_shape(self, end_pt: QPointF) -> None:
        self._remove_preview()
        mask = self._active_mask()
        if self._start_pt is None or mask is None:
            return
        x0, y0 = int(self._start_pt.x()), int(self._start_pt.y())
        x1, y1 = int(end_pt.x()), int(end_pt.y())
        value = 255 if self._mode == MODE_INCLUDE else 0
        if self._tool == TOOL_RECT:
            cv2.rectangle(mask, (x0, y0), (x1, y1), value, thickness=-1)
        else:
            centre = ((x0 + x1) // 2, (y0 + y1) // 2)
            axes = (abs(x1 - x0) // 2, abs(y1 - y0) // 2)
            cv2.ellipse(mask, centre, axes, 0, 0, 360, value, thickness=-1)
        self._refresh_mask_overlay()
        self.roi_changed.emit(self._active_roi)

    def _update_polygon_preview(self) -> None:
        self._remove_preview()
        if len(self._polygon_pts) < 2:
            return
        self._preview_item = QGraphicsPolygonItem(QPolygonF(self._polygon_pts))
        self._preview_item.setPen(QPen(_MASK_COLOUR_ACTIVE, 2))
        self._scene.addItem(self._preview_item)

    def _commit_polygon(self) -> None:
        self._remove_preview()
        mask = self._active_mask()
        if mask is None:
            return
        self._push_undo()
        pts = np.array([[int(p.x()), int(p.y())] for p in self._polygon_pts], dtype=np.int32)
        value = 255 if self._mode == MODE_INCLUDE else 0
        cv2.fillPoly(mask, [pts], value)
        self._polygon_pts = []
        self._refresh_mask_overlay()
        self.roi_changed.emit(self._active_roi)

    def _stamp_brush(self, scene_pt: QPointF) -> None:
        mask = self._active_mask()
        if mask is None:
            return
        value = 255 if self._mode == MODE_INCLUDE else 0
        cv2.circle(
            mask,
            (int(scene_pt.x()), int(scene_pt.y())),
            int(self._brush_radius_px),
            value,
            thickness=-1,
        )
        self._refresh_mask_overlay()
        self.roi_changed.emit(self._active_roi)
