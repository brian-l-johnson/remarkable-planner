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


def _build_stroke_overlay(rm_data: bytes) -> bytes:
    """
    Parse .rm stroke data and render it to a transparent PDF overlay.
    Returns PDF bytes.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W_PT, PAGE_H_PT))
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0, 0, 0)

    try:
        tree = rmscene.read_tree(io.BytesIO(rm_data))
        _render_tree(c, tree)
    except Exception as e:
        logger.warning("Failed to parse .rm data: %s", e)
        # Return an empty overlay rather than crashing
        c.save()
        return buf.getvalue()

    c.save()
    return buf.getvalue()


def _render_tree(c: canvas.Canvas, tree) -> None:
    """Walk the scene tree and draw strokes onto the reportlab canvas."""
    for block in _iter_blocks(tree):
        if isinstance(block, si.Group):
            _render_tree(c, block)
        elif isinstance(block, si.Line):
            _draw_line(c, block)


def _iter_blocks(node) -> list:
    """Yield drawable items from a scene tree node."""
    items = []
    if hasattr(node, "children"):
        for child in node.children.values() if isinstance(node.children, dict) else node.children:
            items.append(child)
    elif hasattr(node, "value") and hasattr(node.value, "children"):
        for child in node.value.children.values() if isinstance(node.value.children, dict) else node.value.children:
            items.append(child)
    return items


def _draw_line(c: canvas.Canvas, line: si.Line) -> None:
    """Draw a single stroke line onto the canvas."""
    points = line.points if hasattr(line, "points") else []
    if not points:
        return

    # Base pen width (in PDF points). reMarkable width values are typically 1–3.
    base_width = getattr(line, "brush_size", 1.8)
    c.setLineWidth(max(0.5, base_width * 0.6))
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
    base_reader   = PdfReader(io.BytesIO(base_pdf_bytes))
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
