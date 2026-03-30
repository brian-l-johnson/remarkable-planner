"""
renderer.py — Overlay reMarkable .rm stroke data onto a base PDF page and
convert the result to a PNG image for vision-based recognition.

reMarkable coordinate system: origin top-left, 1404 × 1872 device units.
PDF coordinate system (reportlab/PDF spec): origin bottom-left, in points.
"""

import base64
import io
import logging

from pypdf import PdfReader, PdfWriter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
import rmscene
import rmscene.scene_items as si
from pdf2image import convert_from_bytes

logger = logging.getLogger(__name__)

# reMarkable 2 native resolution
RM_WIDTH  = 1404.0
RM_HEIGHT = 1872.0

# PDF page dimensions matching the planner template (157.7947 mm × 210.3929 mm)
PAGE_W_PT = 157.7947 * mm
PAGE_H_PT = 210.3929 * mm

# Render DPI for the output PNG — 226 is reMarkable native, lower is fine for vision
RENDER_DPI = 150


def _rm_to_pdf(x: float, y: float) -> tuple[float, float]:
    """Map reMarkable device coordinates to PDF points (origin bottom-left)."""
    pdf_x = x * (PAGE_W_PT / RM_WIDTH)
    pdf_y = PAGE_H_PT - y * (PAGE_H_PT / RM_HEIGHT)
    return pdf_x, pdf_y


def _iter_children(node) -> list:
    """
    Return the ordered list of children from a SceneTree, Group, or any node
    that carries a CrdtSequence under .children.

    SceneTree  → .root  (a Group)  → .children  (CrdtSequence)
    Group      → .children         (CrdtSequence)
    CrdtSequence supports both .values() and direct iteration.
    """
    # Unwrap SceneTree → its root Group first
    if hasattr(node, "root"):
        node = node.root

    children = getattr(node, "children", None)
    if children is None:
        return []

    # CrdtSequence exposes .values(); fall back to plain iteration
    if hasattr(children, "values"):
        return list(children.values())
    return list(children)


def _render_node(c: canvas.Canvas, node) -> None:
    """Recursively walk a scene node (SceneTree, Group) and draw all Lines."""
    for child in _iter_children(node):
        if isinstance(child, si.Group):
            _render_node(c, child)
        elif isinstance(child, si.Line):
            _draw_line(c, child)


def _draw_line(c: canvas.Canvas, line: si.Line) -> None:
    """Draw a single stroke line onto the canvas."""
    points = line.points if hasattr(line, "points") else []
    if not points:
        return

    # thickness_scale is the correct attribute in rmscene 0.6+
    thickness = getattr(line, "thickness_scale", None) or getattr(line, "brush_size", 1.8)
    c.setLineWidth(max(0.5, float(thickness) * 0.6))
    c.setLineCap(1)   # round cap
    c.setLineJoin(1)  # round join

    path = c.beginPath()
    first = True
    for pt in points:
        px, py = _rm_to_pdf(pt.x, pt.y)
        if first:
            path.moveTo(px, py)
            first = False
        else:
            path.lineTo(px, py)

    c.drawPath(path, stroke=1, fill=0)


def _build_stroke_overlay(rm_data: bytes) -> bytes:
    """
    Parse .rm stroke data and render it to a transparent PDF overlay.
    Returns PDF bytes.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W_PT, PAGE_H_PT))
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0, 0, 0)

    stroke_count = 0
    try:
        tree = rmscene.read_tree(io.BytesIO(rm_data))

        # Count strokes before drawing for diagnostic logging
        def _count(node):
            n = 0
            for child in _iter_children(node):
                if isinstance(child, si.Line):
                    n += 1
                elif isinstance(child, si.Group):
                    n += _count(child)
            return n

        stroke_count = _count(tree)
        logger.info("Parsed %d stroke(s) from .rm data", stroke_count)
        _render_node(c, tree)
    except Exception as e:
        logger.warning("Failed to parse .rm data: %s", e)

    c.save()
    if stroke_count == 0:
        logger.warning("No strokes were rendered — overlay will be empty")
    return buf.getvalue()


def render_annotated_png(base_pdf_b64: str, rm_files_b64: dict[str, str]) -> bytes:
    """
    Merge handwriting strokes onto the base PDF page and return PNG bytes.

    Args:
        base_pdf_b64: Base64-encoded original planner PDF.
        rm_files_b64: Dict of page_index → base64-encoded .rm stroke data.
                      Only page "0" is processed (single-page planner).

    Returns:
        PNG bytes of the first page with strokes rendered on top.
    """
    base_pdf_bytes = base64.b64decode(base_pdf_b64)

    # If there are no stroke files, just render the base PDF as-is
    if not rm_files_b64:
        images = convert_from_bytes(base_pdf_bytes, dpi=RENDER_DPI, first_page=1, last_page=1)
        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        return buf.getvalue()

    # Build stroke overlay for page 0
    rm_b64 = rm_files_b64.get("0") or next(iter(rm_files_b64.values()))
    rm_data = base64.b64decode(rm_b64)
    overlay_bytes = _build_stroke_overlay(rm_data)

    # Merge overlay onto the base PDF's first page
    base_reader    = PdfReader(io.BytesIO(base_pdf_bytes))
    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))

    writer = PdfWriter()
    page = base_reader.pages[0]
    page.merge_page(overlay_reader.pages[0])
    writer.add_page(page)

    merged_buf = io.BytesIO()
    writer.write(merged_buf)
    merged_bytes = merged_buf.getvalue()

    # Convert merged PDF to PNG
    images = convert_from_bytes(merged_bytes, dpi=RENDER_DPI, first_page=1, last_page=1)
    out_buf = io.BytesIO()
    images[0].save(out_buf, format="PNG")
    return out_buf.getvalue()
