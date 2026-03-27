import express from "express";
import multer from "multer";
import { remarkable } from "rmapi-js";
import fs from "fs/promises";

const app  = express();
const upload = multer({ storage: multer.memoryStorage() });

const TOKEN_PATH    = process.env.RMAPI_TOKEN_PATH ?? "/secret/rmapi-token";
const PORT          = process.env.PORT ?? 8080;

async function loadToken() {
  const token = await fs.readFile(TOKEN_PATH, "utf8");
  return token.trim();
}

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.post("/upload", upload.single("pdf"), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ status: "error", message: "No pdf field in request" });
  }

  const today    = new Date();
  const dateStr  = today.toISOString().slice(0, 10);
  const filename = req.body.filename ?? `Daily Planner ${dateStr}`;

  try {
    const token = await loadToken();
    const api   = await remarkable(token);

    // uploadPdf uploads to root; visibleName is what appears on the tablet
    const entry = await api.uploadPdf(filename, req.file.buffer.buffer);

    console.log(`✅ Uploaded "${filename}" (docID: ${entry.docID})`);
    res.json({
      status:      "ok",
      uploaded_as: filename,
      doc_id:      entry.docID,
      date:        dateStr,
    });
  } catch (err) {
    console.error("Upload failed:", err);
    res.status(500).json({ status: "error", message: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`rmapi-js upload service listening on :${PORT}`);
});
