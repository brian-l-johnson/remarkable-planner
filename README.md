# remarkable-planner

Generate, upload, and sync daily planners with a reMarkable tablet. The system creates PDF planners populated with weather, calendar events, and tasks, uploads them to the tablet, then downloads annotated versions (with handwritten notes) and renders them as images for AI-powered analysis.

## Architecture

The application is composed of three microservices orchestrated by [n8n](https://n8n.io/) workflows:

### PDF Generator (Python/Flask)

Root directory. Uses WeasyPrint to render an HTML/Jinja2 template into a PDF optimized for the reMarkable 2 display (1404×1872px @ 226 DPI). The daily planner includes an hourly calendar grid, weather forecast, task lists, and a dotted background.

- `POST /generate` — accepts JSON (events, todos, weather) and returns a rendered PDF
- `GET /health`

### rMAPI Upload Service (Node.js/Express)

`rmapi-service/`. Communicates with the reMarkable Cloud API via [rmapi-js](https://github.com/Jayy001/rmapi-js). Uploads new planners, downloads annotated documents, and manages the document lifecycle (auto-archive after 7 days, delete after 30 days).

- `POST /upload` — upload a PDF to the tablet
- `GET /download/:date` — download an annotated planner by date (YYYY-MM-DD)
- `GET /health`

### Planner Sync Service (Python/Flask)

`planner-sync/`. Downloads annotated planners and overlays handwritten strokes onto the original PDF, then renders the result as a PNG image. Handles the coordinate transform between the reMarkable scene format and PDF coordinate space.

- `POST /render` — download, overlay annotations, and return a PNG
- `GET /health`

### Orchestration

n8n workflow definitions live in `n8n/`. Import these into an n8n instance to wire the services together on a daily schedule.

## Directory Structure

```
├── app.py                  # PDF Generator entry point
├── templates/              # Jinja2 HTML planner template
├── rmapi-service/          # reMarkable Cloud API service
├── planner-sync/           # Annotation rendering service
├── k8s/remarkable/         # Kubernetes manifests (Kustomize + Flux CD)
├── n8n/                    # n8n workflow exports
└── .github/workflows/      # CI/CD — Docker image builds
```

## Installation

### Prerequisites

- Python 3.x
- Node.js
- A reMarkable Cloud API token (see below)

### Local Development

```bash
# PDF Generator
pip install -r requirements.txt
python app.py

# rMAPI Service
cd rmapi-service
npm install
node server.mjs

# Planner Sync Service
cd planner-sync
pip install -r requirements.txt
python app.py
```

All three services listen on port 8080 by default.

### Obtaining a reMarkable API Token

Register a one-time code at [my.remarkable.com](https://my.remarkable.com/) and exchange it for a device token:

```bash
node rmapi-service/get-token.mjs <ONE-TIME-CODE>
```

Store the resulting token as the `RMAPI_TOKEN` environment variable (or as a Kubernetes secret for production).

### Kubernetes Deployment

Manifests in `k8s/remarkable/` use Kustomize and support Flux CD for GitOps. Docker images are built automatically by GitHub Actions and pushed to GitHub Container Registry.

```bash
kubectl apply -k k8s/remarkable/
```
