import express from "express";
import multer from "multer";
import { remarkable } from "rmapi-js";
import fs from "fs/promises";
import AdmZip from "adm-zip";

const app    = express();
const upload = multer({ storage: multer.memoryStorage() });

const TOKEN_PATH      = process.env.RMAPI_TOKEN_PATH  ?? "/secret/rmapi-token";
const ARCHIVE_FOLDER  = process.env.ARCHIVE_FOLDER    ?? "Daily Planner Archive";
const PLANNER_PREFIX  = process.env.PLANNER_PREFIX    ?? "planner";
const KEEP_DAYS       = parseInt(process.env.KEEP_DAYS    ?? "7",  10);
const DELETE_DAYS     = parseInt(process.env.DELETE_DAYS  ?? "30", 10);
const PORT            = process.env.PORT              ?? 8080;

async function getApi() {
  const token = await fs.readFile(TOKEN_PATH, "utf8");
  return remarkable(token.trim());
}

// Fetch only .metadata (not .content) for every root-level item, in bounded
// batches of `concurrency`.  Returns objects with { id, hash, visibleName,
// type, parent } — everything managePlanners() and the download endpoint need.
// Avoids the N×3 fan-out of api.listItems() that was triggering ETIMEDOUT.
async function listItemsMeta(api, concurrency = 8) {
  const [rootHash]          = await api.raw.getRootHash();
  const { entries: rootEnts } = await api.raw.getEntries(rootHash);

  const items = [];
  for (let i = 0; i < rootEnts.length; i += concurrency) {
    const batch   = rootEnts.slice(i, i + concurrency);
    const results = await Promise.all(batch.map(async ({ id, hash }) => {
      try {
        const { entries } = await api.raw.getEntries(hash);
        const metaEnt = entries.find(e => e.id.endsWith(".metadata"));
        if (!metaEnt) return null;
        const meta = await api.raw.getMetadata(metaEnt.hash);
        return { id, hash, visibleName: meta.visibleName, type: meta.type, parent: meta.parent, lastModified: meta.lastModified ?? "0" };
      } catch (e) {
        console.warn(`Skipping item ${id}: ${e.message}`);
        return null;
      }
    }));
    items.push(...results.filter(Boolean));
  }
  return items;
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
  const filename = req.body.filename ?? `${PLANNER_PREFIX}-${dateStr}`;

  try {
    const api = await getApi();

    await managePlanners(api, today);

    // Buffer extends Uint8Array — compatible with v9's uploadPdf signature
    const entry = await api.uploadPdf(filename, req.file.buffer);

    console.log(`✅ Uploaded "${filename}" (id: ${entry.hash})`);
    res.json({
      status:      "ok",
      uploaded_as: filename,
      doc_id:      entry.hash,
      date:        dateStr,
    });
  } catch (err) {
    console.error("Upload failed:", err);
    res.status(500).json({ status: "error", message: err.message });
  }
});

// GET /check/:date — Return document metadata without downloading the zip.
// Use this to check lastModified before deciding whether to run OCR.
app.get("/check/:date", async (req, res) => {
  const { date } = req.params;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ status: "error", message: "Date must be YYYY-MM-DD" });
  }

  const targetName = `${PLANNER_PREFIX}-${date}`;

  try {
    const api = await getApi();

    const items   = await listItemsMeta(api);
    const matches = items.filter(i => i.type === "DocumentType" && i.visibleName === targetName);

    if (matches.length === 0) {
      return res.status(404).json({ status: "not_found", message: `No document named "${targetName}"` });
    }

    const doc = matches.sort((a, b) => Number(b.lastModified) - Number(a.lastModified))[0];

    res.json({
      status:       "ok",
      documentId:   doc.id,
      visibleName:  targetName,
      lastModified: doc.lastModified,
    });

  } catch (err) {
    console.error("Check failed:", err);
    res.status(500).json({ status: "error", message: err.message });
  }
});

// GET /download/:date — Download an annotated planner document by date (YYYY-MM-DD).
// Returns JSON with base64-encoded base PDF and .rm stroke files per page.
app.get("/download/:date", async (req, res) => {
  const { date } = req.params;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ status: "error", message: "Date must be YYYY-MM-DD" });
  }

  const targetName = `${PLANNER_PREFIX}-${date}`;

  try {
    const api = await getApi();

    const items   = await listItemsMeta(api);
    const matches = items.filter(i => i.type === "DocumentType" && i.visibleName === targetName);

    if (matches.length === 0) {
      return res.status(404).json({ status: "not_found", message: `No document named "${targetName}"` });
    }

    // When multiple copies exist (e.g. from retried uploads), use the most recently modified.
    const doc = matches.sort((a, b) => Number(b.lastModified) - Number(a.lastModified))[0];
    if (matches.length > 1) {
      console.warn(`Found ${matches.length} documents named "${targetName}" — using most recent (${doc.id})`);
    }

    // getDocument() returns the full document bundle as a zip Uint8Array
    const zipBytes = await api.getDocument(doc.hash);
    const zip      = new AdmZip(Buffer.from(zipBytes));
    const entries  = zip.getEntries();

    // Extract base PDF and .rm stroke files
    let basePdf         = null;
    let contentMetadata = {};
    const rmFiles       = {};

    for (const entry of entries) {
      const name = entry.entryName;

      if (name.endsWith(".pdf")) {
        basePdf = entry.getData().toString("base64");
      } else if (name.endsWith(".content")) {
        try { contentMetadata = JSON.parse(entry.getData().toString("utf8")); } catch { /* non-fatal */ }
      } else if (name.endsWith(".rm")) {
        // Paths are like "{id}/{pageUUID}.rm" (UUID) or "{id}/{n}.rm" (legacy number).
        // Key by sequential index so the renderer always gets 0-based page numbers.
        const pageKey = String(Object.keys(rmFiles).length);
        rmFiles[pageKey] = entry.getData().toString("base64");
      }
    }

    if (!basePdf) {
      return res.status(422).json({ status: "error", message: "Document has no PDF layer" });
    }

    const hasAnnotations = Object.keys(rmFiles).length > 0;
    console.log(`📥 Downloaded "${targetName}" — ${hasAnnotations ? Object.keys(rmFiles).length + " page(s) with annotations" : "no annotations yet"}`);

    res.json({
      status: "ok",
      documentId:  doc.id,
      visibleName: targetName,
      hasAnnotations,
      basePdf,
      rmFiles,
      contentMetadata,
    });

  } catch (err) {
    console.error("Download failed:", err);
    res.status(500).json({ status: "error", message: err.message });
  }
});

async function managePlanners(api, today) {
  try {
    const items = await listItemsMeta(api);

    // Find the archive folder — must already exist on the device
    const archiveFolder = items.find(
      i => i.type === "CollectionType" && i.visibleName === ARCHIVE_FOLDER
    );

    if (!archiveFolder) {
      console.warn(`Archive folder "${ARCHIVE_FOLDER}" not found — skipping management step.`);
      console.warn(`Create a folder named "${ARCHIVE_FOLDER}" on your reMarkable to enable archiving.`);
      return;
    }

    const archiveCutoff = new Date(today);
    archiveCutoff.setDate(archiveCutoff.getDate() - KEEP_DAYS);

    const deleteCutoff = new Date(today);
    deleteCutoff.setDate(deleteCutoff.getDate() - DELETE_DAYS);

    let archived = 0;
    let deleted  = 0;

    for (const doc of items) {
      if (doc.type !== "DocumentType") continue;

      const match = doc.visibleName.match(new RegExp(`^${PLANNER_PREFIX}-(\\d{4}-\\d{2}-\\d{2})$`));
      if (!match) continue;

      const plannerDate = new Date(match[1]);

      // In archive and older than DELETE_DAYS → trash
      // In v9, move() takes hash as first arg and destination id as second
      if (doc.parent === archiveFolder.id && plannerDate < deleteCutoff) {
        console.log(`Trashing "${doc.visibleName}" (>${DELETE_DAYS} days old)...`);
        await api.move(doc.hash, "trash");
        deleted++;
      }
      // In root and older than KEEP_DAYS → move to archive
      else if ((doc.parent === "" || doc.parent == null) && plannerDate < archiveCutoff) {
        console.log(`Archiving "${doc.visibleName}" (>${KEEP_DAYS} days old)...`);
        await api.move(doc.hash, archiveFolder.id);
        archived++;
      }
    }

    if (archived > 0) console.log(`✅ Archived ${archived} planner(s) to "${ARCHIVE_FOLDER}"`);
    if (deleted  > 0) console.log(`✅ Trashed ${deleted} planner(s) older than ${DELETE_DAYS} days`);
    if (archived === 0 && deleted === 0) console.log("No planners to archive or delete");

  } catch (err) {
    console.warn("Planner management failed (non-fatal):", err.message);
  }
}

app.listen(PORT, () => {
  console.log(`rmapi-js upload service listening on :${PORT}`);
  console.log(`  Keep in root: ${KEEP_DAYS} days | Archive for: ${DELETE_DAYS} days | Then trash`);
});
