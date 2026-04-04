"""
Diagnostic script: dump raw RM stroke coordinates from today's planner.

Usage:
    RMAPI_SERVICE_URL=http://localhost:8000 python debug_coords.py [date]

Prints min/max x/y for each stroke so we can calibrate the coordinate transform.
"""

import base64
import io
import json
import os
import sys

import httpx
import rmscene
import rmscene.scene_items as si
from pypdf import PdfReader


def iter_children(node):
    if hasattr(node, "root"):
        node = node.root
    children = getattr(node, "children", None)
    if children is None:
        return []
    if hasattr(children, "values"):
        return list(children.values())
    return list(children)


def collect_lines(node):
    """Recursively collect all Line objects from the scene tree."""
    lines = []
    for child in iter_children(node):
        if isinstance(child, si.Group):
            lines.extend(collect_lines(child))
        elif isinstance(child, si.Line):
            lines.append(child)
    return lines


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-03-31"
    rmapi_url = os.environ.get("RMAPI_SERVICE_URL", "http://localhost:8000")

    print(f"Downloading planner for {date} from {rmapi_url}...")
    resp = httpx.get(f"{rmapi_url}/download/{date}", timeout=30)
    resp.raise_for_status()
    dl = resp.json()

    if not dl.get("hasAnnotations"):
        print("No annotations found!")
        return

    # PDF page dimensions
    pdf_bytes = base64.b64decode(dl["basePdf"])
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page = reader.pages[0]
    w_pt = float(page.mediabox.width)
    h_pt = float(page.mediabox.height)
    print(f"\nPDF page size: {w_pt:.1f} x {h_pt:.1f} pt")
    print(f"PDF aspect ratio (w/h): {w_pt/h_pt:.4f}")
    print(f"RM aspect ratio (w/h):  {1404/1872:.4f}")

    # Parse strokes
    rm_b64 = dl["rmFiles"].get("0") or next(iter(dl["rmFiles"].values()))
    rm_data = base64.b64decode(rm_b64)
    tree = rmscene.read_tree(io.BytesIO(rm_data))
    lines = collect_lines(tree)

    print(f"\nTotal strokes: {len(lines)}")
    print(f"\n{'#':>3}  {'pts':>4}  {'x_min':>8}  {'x_max':>8}  {'y_min':>8}  {'y_max':>8}  {'cx':>8}  {'cy':>8}")
    print("-" * 75)

    all_x, all_y = [], []
    for i, line in enumerate(lines):
        pts = line.points if hasattr(line, "points") else []
        if not pts:
            continue
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        all_x.extend(xs)
        all_y.extend(ys)
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        print(f"{i:3d}  {len(pts):4d}  {min(xs):8.1f}  {max(xs):8.1f}  {min(ys):8.1f}  {max(ys):8.1f}  {cx:8.1f}  {cy:8.1f}")

    if all_x:
        print(f"\nGlobal coordinate ranges:")
        print(f"  X: {min(all_x):.1f} to {max(all_x):.1f}  (span: {max(all_x)-min(all_x):.1f})")
        print(f"  Y: {min(all_y):.1f} to {max(all_y):.1f}  (span: {max(all_y)-min(all_y):.1f})")


if __name__ == "__main__":
    main()
