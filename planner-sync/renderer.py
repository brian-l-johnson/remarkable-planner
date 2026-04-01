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

# Render DPI for the output PNG — 226 is reMarkable native, lower is fine for vision
RENDER_DPI = 150

# Crop boxes (left, top, right, bottom) in pixels at RENDER_DPI=150.
# Page is ~932×1242px. Right column starts at ~376px (3mm padding + 60.7mm schedule col).
# Columns start at ~197px (padding + 18mm header + 2mm gap + ~49px banner + 2mm gap).
# Column height is ~932px; todo and notes each take half (~466px each).
SECTION_CROPS = {
    "todo":  (376, 190, 932, 680),   # right col, top half — includes col header + some margin
    "notes": (376, 660, 932, 1135),  # right col, bottom half
}


def _rm_to_pdf(x: float, y: float, page_w_pt: float, page_h_pt: float) -> tuple[float, float]:
    """Map reMarkable PDF-annotation coordinates to PDF points (origin bottom-left).

    The reMarkable renders PDFs at its native 226 DPI.  The annotation
    coordinate system is centred horizontally (x=0 is the middle of the
    screen) with y=0 at the top of the visible PDF viewport.

    Calibrated against corner marks, full-span lines, and checkbox strokes
    on 2026-03-31 — corner marks land within ~7 pt of the page edges (the
    residual is the user drawing slightly inside the corner).
    """
    RM_DPI = 226.0
    scale = 72.0 / RM_DPI                # PDF points per RM unit
    pdf_x = x * scale + page_w_pt / 2.0  # centred origin → left-edge origin
    pdf_y = page_h_pt - y * scale         # flip Y: RM top→down, PDF bottom→up
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


def _render_node(c: canvas.Canvas, node, page_w_pt: float, page_h_pt: float) -> None:
    """Recursively walk a scene node (SceneTree, Group) and draw all Lines."""
    for child in _iter_children(node):
        if isinstance(child, si.Group):
            _render_node(c, child, page_w_pt, page_h_pt)
        elif isinstance(child, si.Line):
            _draw_line(c, child, page_w_pt, page_h_pt)


def _draw_line(c: canvas.Canvas, line: si.Line, page_w_pt: float, page_h_pt: float) -> None:
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
        px, py = _rm_to_pdf(pt.x, pt.y, page_w_pt, page_h_pt)
        if first:
            path.moveTo(px, py)
            first = False
        else:
            path.lineTo(px, py)

    c.drawPath(path, stroke=1, fill=0)


def _build_stroke_overlay(rm_data: bytes, page_w_pt: float, page_h_pt: float) -> bytes:
    """
    Parse .rm stroke data and render it to a transparent PDF overlay.
    Returns PDF bytes.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w_pt, page_h_pt))
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0, 0, 0)

    stroke_count = 0
    try:
        tree = rmscene.read_tree(io.BytesIO(rm_data))

        def _count(node):
            n = 0
            for child in _iter_children(node):
                if isinstance(child, si.Line):
                    n += 1
                elif isinstance(child, si.Group):
                    n += _count(child)
            return n

        stroke_count = _count(tree)
        logger.info("Parsed %d stroke(s) from .rm data (page %.0fx%.0fpt)", stroke_count, page_w_pt, page_h_pt)
        _render_node(c, tree, page_w_pt, page_h_pt)
    except Exception as e:
        logger.warning("Failed to parse .rm data: %s", e)

    c.save()
    if stroke_count == 0:
        logger.warning("No strokes were rendered — overlay will be empty")
    return buf.getvalue()


def debug_stroke_coords(base_pdf_b64: str, rm_files_b64: dict[str, str]) -> dict:
    """Dump raw RM stroke coordinates for calibration diagnostics."""
    pdf_bytes = base64.b64decode(base_pdf_b64)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page = reader.pages[0]
    w_pt = float(page.mediabox.width)
    h_pt = float(page.mediabox.height)

    rm_b64 = rm_files_b64.get("0") or next(iter(rm_files_b64.values()))
    rm_data = base64.b64decode(rm_b64)
    tree = rmscene.read_tree(io.BytesIO(rm_data))

    lines = []
    def _collect(node):
        for child in _iter_children(node):
            if isinstance(child, si.Group):
                _collect(child)
            elif isinstance(child, si.Line):
                lines.append(child)
    _collect(tree)

    all_x, all_y = [], []
    strokes = []
    for i, line in enumerate(lines):
        pts = line.points if hasattr(line, "points") else []
        if not pts:
            continue
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        all_x.extend(xs)
        all_y.extend(ys)
        strokes.append({
            "index": i,
            "num_points": len(pts),
            "x_min": round(min(xs), 1),
            "x_max": round(max(xs), 1),
            "y_min": round(min(ys), 1),
            "y_max": round(max(ys), 1),
            "cx": round((min(xs) + max(xs)) / 2, 1),
            "cy": round((min(ys) + max(ys)) / 2, 1),
        })

    result = {
        "pdf_w_pt": round(w_pt, 1),
        "pdf_h_pt": round(h_pt, 1),
        "pdf_aspect": round(w_pt / h_pt, 4),
        "rm_aspect": round(1404 / 1872, 4),
        "total_strokes": len(strokes),
        "strokes": strokes,
    }
    if all_x:
        result["global_x_min"] = round(min(all_x), 1)
        result["global_x_max"] = round(max(all_x), 1)
        result["global_x_span"] = round(max(all_x) - min(all_x), 1)
        result["global_y_min"] = round(min(all_y), 1)
        result["global_y_max"] = round(max(all_y), 1)
        result["global_y_span"] = round(max(all_y) - min(all_y), 1)
    return result


def render_annotated_png(base_pdf_b64: str, rm_files_b64: dict[str, str], section: str | None = None) -> bytes: fd6f5d0 (feat: add section cropping to /render endpoint for vision efficiency)
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

    # Read actual page dimensions from the PDF (don't hardcode them)
    base_reader = PdfReader(io.BytesIO(base_pdf_bytes))
    page0 = base_reader.pages[0]
    page_w_pt = float(page0.mediabox.width)
    page_h_pt = float(page0.mediabox.height)
    logger.info("PDF page size: %.1f x %.1f pt", page_w_pt, page_h_pt)

    # If there are no stroke files, just render the base PDF as-is
    if not rm_files_b64:
        images = convert_from_bytes(base_pdf_bytes, dpi=RENDER_DPI, first_page=1, last_page=1)
        image = images[0]
        if section and section in SECTION_CROPS:
            image = image.crop(SECTION_CROPS[section])
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    # Build stroke overlay for page 0 using the actual page dimensions
    rm_b64 = rm_files_b64.get("0") or next(iter(rm_files_b64.values()))
    rm_data = base64.b64decode(rm_b64)
    overlay_bytes = _build_stroke_overlay(rm_data, page_w_pt, page_h_pt)

    # Merge overlay onto the base PDF's first page (base_reader already open above)
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
    image = images[0]
    if section and section in SECTION_CROPS:
        image = image.crop(SECTION_CROPS[section])
    out_buf = io.BytesIO()
    image.save(out_buf, format="PNG")
    return out_buf.getvalue()
