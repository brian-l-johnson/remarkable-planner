#!/usr/bin/env python3
"""
reMarkable Daily Planner - PDF Generator Service
POST /generate  → renders HTML template to PDF, returns raw PDF bytes
GET  /health    → liveness probe
"""

import os
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import io

app = Flask(__name__)
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
jinja = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

WEATHER_SYMBOLS = {
    "sunny":            "☀",
    "clear-night":      "☽",
    "partlycloudy":     "⛅",
    "cloudy":           "☁",
    "fog":              "🌫",
    "rainy":            "🌧",
    "pouring":          "🌧",
    "snowy":            "❄",
    "snowy-rainy":      "🌨",
    "lightning":        "⚡",
    "lightning-rainy":  "⛈",
    "windy":            "💨",
    "windy-variant":    "💨",
    "hail":             "🌨",
    "exceptional":      "⚠",
}

WEATHER_LABELS = {
    "sunny":            "Sunny",
    "clear-night":      "Clear",
    "partlycloudy":     "Partly cloudy",
    "cloudy":           "Cloudy",
    "fog":              "Foggy",
    "rainy":            "Rainy",
    "pouring":          "Heavy rain",
    "snowy":            "Snowy",
    "snowy-rainy":      "Wintry mix",
    "lightning":        "Thunderstorm",
    "lightning-rainy":  "Thunderstorm",
    "windy":            "Windy",
    "windy-variant":    "Windy",
    "hail":             "Hail",
    "exceptional":      "Unusual",
}


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)

    today = datetime.now()
    payload = {
        "date_full":         today.strftime("%-d %B %Y"),
        "day_name":          today.strftime("%A").upper(),
        "week_number":       today.strftime("%-W"),
        "weather_condition": data.get("weather_condition", ""),
        "weather_high":      data.get("weather_high", ""),
        "weather_low":       data.get("weather_low", ""),
        "events":            data.get("events", []),
        "todos":             data.get("todos", []),
        "weather_symbols":   WEATHER_SYMBOLS,
        "weather_labels":    WEATHER_LABELS,
    }

    template = jinja.get_template("planner.html")
    html_out = template.render(**payload)

    pdf_bytes = HTML(string=html_out, base_url=TEMPLATE_DIR).write_pdf()

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"Daily Planner {today.strftime('%Y-%m-%d')}.pdf",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
