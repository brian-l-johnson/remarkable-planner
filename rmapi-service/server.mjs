import express from "express";
import multer from "multer";
import { register, remarkable } from "rmapi-js";
import fs from "fs/promises";
import path from "path";

const app = express();
const upload = multer({ storage: multer.memoryStorage() });

const TOKEN_PATH = process.env.RMAPI_TOKEN_PATH ?? "/secret/rmapi-token";
const FOLDER_NAME = process.env.REMARKABLE_FOLDER ?? "Daily Planner";
const PORT = process.env.PORT ?? 8080;

// Load persisted token at startup
async function loadToken() {
  try {
    const token = await fs.readFile(TOKEN_PATH, "utf8");
    return token.trim();
  } catch {
    throw new Error(`Could not read rMAPI token from ${TOKEN_PATH}`);
  }
}

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

// POST /upload
// Accepts multipart/form-data with a single field "pdf" (the PDF file bytes)
// and an optional field "filename" (defaults to YYYY-MM-DD.pdf)
app.post("/upload", upload.single("pdf"), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ status: "error", message: "No pdf field in request" });
  }

  const today = new Date();
  const dateStr = today.toISOString().slice(0, 10); // YYYY-MM-DD
  const filename = req.body.filename ?? `Daily Planner ${dateStr}`;

  try {
    const token = await loadToken();
    const api = await remarkable(token);

    // Find or create the target folder
    const items = await api.listItems();
    let folder = items.find(
      (i) => i.type === "CollectionType" && i.visibleName === FOLDER_NAME
    );

    if (!folder) {
      console.log(`Folder "${FOLDER_NAME}" not found, creating...`);
      folder = await api.createDirectory(FOLDER_NAME);
    }

    // Upload the PDF into the folder
    await api.putPdf(filename, req.file.buffer, { parent: folder.id });

    console.log(`✅ Uploaded "${filename}" to "${FOLDER_NAME}"`);
    res.json({
      status: "ok",
      uploaded_to: `${FOLDER_NAME}/${filename}`,
      date: dateStr,
    });
  } catch (err) {
    console.error("Upload failed:", err);
    res.status(500).json({ status: "error", message: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`rmapi-js upload service listening on :${PORT}`);
});
