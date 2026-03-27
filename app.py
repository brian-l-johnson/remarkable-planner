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

    # Split todos into priority and later
    todos         = data.get("todos", [])
    todo_priority = [t for t in todos if t.get("priority")]
    todo_later    = [t for t in todos if not t.get("priority")]

    # Blank row padding — always at least 1, enough to fill section
    priority_blanks = max(3 - len(todo_priority), 1)
    later_blanks    = max(4 - len(todo_later), 1)

    # Pre-process calendar events into template-ready dicts
    calendar_events = []
    for ev in data.get("events", []):
        top_pct    = round(((ev["start_hour"] - 7) * 60 + ev["start_min"]) / (13 * 60) * 100, 2)
        height_pct = round(((ev["end_hour"] - ev["start_hour"]) * 60 + (ev["end_min"] - ev["start_min"])) / (13 * 60) * 100, 2)
        height_pct = max(height_pct, 3.5)

        sh, sm = ev["start_hour"], ev["start_min"]
        eh, em = ev["end_hour"],   ev["end_min"]
        start_label = f"{sh if sh <= 12 else sh - 12}:{sm:02d}"
        end_label   = f"{eh if eh <= 12 else eh - 12}:{em:02d}"

        calendar_events.append({
            "title":      ev.get("title", ""),
            "color":      ev.get("color", "cal-blue"),
            "top_pct":    top_pct,
            "height_pct": height_pct,
            "time_label": f"{start_label} – {end_label}",
        })

    payload = {
        "date_full":         today.strftime("%-d %B %Y"),
        "day_name":          today.strftime("%A").upper(),
        "week_number":       today.strftime("%-W"),
        "weather_condition": data.get("weather_condition", ""),
        "weather_high":      data.get("weather_high", ""),
        "weather_low":       data.get("weather_low", ""),
        "calendar_events":   calendar_events,
        "todo_priority":     todo_priority,
        "todo_later":        todo_later,
        "priority_blanks":   priority_blanks,
        "later_blanks":      later_blanks,
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
