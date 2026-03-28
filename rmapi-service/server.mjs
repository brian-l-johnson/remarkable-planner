import express from "express";
import multer from "multer";
import { remarkable } from "rmapi-js";
import fs from "fs/promises";

const app    = express();
const upload = multer({ storage: multer.memoryStorage() });

const TOKEN_PATH      = process.env.RMAPI_TOKEN_PATH ?? "/secret/rmapi-token";
const ARCHIVE_FOLDER  = process.env.ARCHIVE_FOLDER   ?? "Daily Planner Archive";
const PORT            = process.env.PORT              ?? 8080;

async function loadToken() {
  const token = await fs.readFile(TOKEN_PATH, "utf8");
  return token.trim();
}

async function getApi() {
  const token = await loadToken();
  return remarkable(token);
}

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

// POST /upload — upload a PDF to the root of reMarkable
app.post("/upload", upload.single("pdf"), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ status: "error", message: "No pdf field in request" });
  }

  const today    = new Date();
  const dateStr  = today.toISOString().slice(0, 10);
  const filename = req.body.filename ?? `Daily Planner ${dateStr}`;

  try {
    const api   = await getApi();

    // Archive yesterday's planner before uploading today's
    await archiveOldPlanners(api, filename);

    // Upload today's planner
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

// Find and move any existing Daily Planner docs (except today's) to archive folder
async function archiveOldPlanners(api, todayFilename) {
  try {
    const allMeta = await api.getEntriesMetadata();

    // Find or create the archive collection
    let archiveFolder = allMeta.find(
      e => e.type === "CollectionType" && e.visibleName === ARCHIVE_FOLDER
    );

    if (!archiveFolder) {
      console.log(`Creating archive folder "${ARCHIVE_FOLDER}"...`);
      const entry = await api.putCollection(ARCHIVE_FOLDER, { parent: "" });
      // getEntriesMetadata again to get the new folder's documentId
      const refreshed = await api.getEntriesMetadata();
      archiveFolder = refreshed.find(
        e => e.type === "CollectionType" && e.visibleName === ARCHIVE_FOLDER
      );
    }

    if (!archiveFolder) {
      console.warn("Could not find or create archive folder, skipping archive step");
      return;
    }

    // Find old Daily Planner PDFs in root (parent === "") that aren't today's
    const oldPlanners = allMeta.filter(
      e => e.type !== "CollectionType"
        && e.visibleName.startsWith("Daily Planner ")
        && e.visibleName !== todayFilename
        && (e.parent === "" || e.parent == null)
    );

    for (const doc of oldPlanners) {
      console.log(`Archiving "${doc.visibleName}" (${doc.documentId})...`);
      await api.move(doc.documentId, archiveFolder.documentId);
    }

    if (oldPlanners.length > 0) {
      console.log(`✅ Archived ${oldPlanners.length} old planner(s)`);
    }
  } catch (err) {
    // Don't fail the upload if archiving fails
    console.warn("Archive step failed (non-fatal):", err.message);
  }
}

app.listen(PORT, () => {
  console.log(`rmapi-js upload service listening on :${PORT}`);
});
