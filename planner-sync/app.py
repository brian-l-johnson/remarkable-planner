"""
planner-sync — Minimal rendering microservice.

Downloads today's annotated reMarkable planner via rmapi-service, overlays
the handwriting strokes onto the base PDF, and returns a base64 PNG for the
n8n workflow to pass to Claude Vision.

Endpoints:
  GET /health   — liveness probe
  POST /render  — download + render → base64 PNG
"""

import base64
import logging
import os
from datetime import date

import httpx
from flask import Flask, jsonify, request

from renderer import render_annotated_png

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

RMAPI_SERVICE_URL = os.environ.get("RMAPI_SERVICE_URL", "http://rmapi-uploader.remarkable.svc.cluster.local")
PORT              = int(os.environ.get("PORT", "8080"))


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/render")
def render():
    """
    Download the annotated planner for a given date and return a base64 PNG.

    Request body (JSON):
        date  (optional) — YYYY-MM-DD, defaults to today

    Response (all HTTP 200):
        {"status": "ok",             "date": "...", "png": "<base64 PNG>"}
        {"status": "not_found",      "date": "..."}
        {"status": "no_annotations", "date": "..."}

    HTTP 5xx on unexpected errors.
    """
    body        = request.get_json(silent=True) or {}
    render_date = body.get("date") or date.today().isoformat()

    # ── 1. Download from reMarkable Cloud via rmapi-service ──────────────────
    logger.info("Downloading planner for %s ...", render_date)
    try:
        dl_resp = httpx.get(
            f"{RMAPI_SERVICE_URL}/download/{render_date}",
            timeout=60.0,
        )
    except httpx.RequestError as e:
        return jsonify({"status": "error", "message": f"Could not reach rmapi-service: {e}"}), 502

    if dl_resp.status_code == 404:
        return jsonify({"status": "not_found", "date": render_date})

    if dl_resp.status_code != 200:
        return jsonify({"status": "error", "message": f"Download failed: HTTP {dl_resp.status_code}"}), 502

    dl = dl_resp.json()

    if not dl.get("hasAnnotations"):
        logger.info("No annotations yet for %s", render_date)
        return jsonify({"status": "no_annotations", "date": render_date})

    # ── 2. Render .rm strokes onto base PDF → PNG ─────────────────────────────
    logger.info("Rendering annotated PNG ...")
    try:
        png_bytes = render_annotated_png(dl["basePdf"], dl["rmFiles"])
    except Exception as e:
        logger.exception("Render failed")
        return jsonify({"status": "render_error", "message": str(e)}), 500

    logger.info("Render complete — PNG size: %d bytes", len(png_bytes))
    return jsonify({
        "status": "ok",
        "date":   render_date,
        "png":    base64.b64encode(png_bytes).decode(),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
