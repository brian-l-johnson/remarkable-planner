import express from "express";
import multer from "multer";
import { remarkable } from "rmapi-js";
import fs from "fs/promises";
import AdmZip from "adm-zip";

const app    = express();
const upload = multer({ storage: multer.memoryStorage() });

const TOKEN_PATH     = process.env.RMAPI_TOKEN_PATH  ?? "/secret/rmapi-token";
const ARCHIVE_FOLDER = process.env.ARCHIVE_FOLDER    ?? "Daily Planner Archive";
const KEEP_DAYS      = parseInt(process.env.KEEP_DAYS    ?? "7",  10);
const DELETE_DAYS    = parseInt(process.env.DELETE_DAYS  ?? "30", 10);
const PORT           = process.env.PORT              ?? 8080;

async function getApi() {
  const token = await fs.readFile(TOKEN_PATH, "utf8");
  return remarkable(token.trim());
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
    const api = await getApi();

    await managePlanners(api, today);

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

// GET /download/:date — Download an annotated planner document by date (YYYY-MM-DD).
// Returns JSON with base64-encoded base PDF and .rm stroke files per page.
app.get("/download/:date", async (req, res) => {
  const { date } = req.params;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ status: "error", message: "Date must be YYYY-MM-DD" });
  }

  const targetName = `Daily Planner ${date}`;

  try {
    const api = await getApi();

    // Find the document in metadata (root or archive)
    const allMeta = await api.getEntriesMetadata();
    const docMeta = allMeta.find(
      e => e.type !== "CollectionType" && e.visibleName === targetName
    );

    if (!docMeta) {
      return res.status(404).json({ status: "not_found", message: `No document named "${targetName}"` });
    }

    // Get all items (includes hash values needed for content download)
    const items = await api.listItems();
    const item = items.find(i => i.documentId === docMeta.documentId);

    if (!item?.hash) {
      return res.status(404).json({ status: "not_found", message: "Document found but has no hash (may still be syncing)" });
    }

    // Download the raw document zip bundle
    const docData = await api.getDocument(item.hash);
    const buf = Buffer.from(docData instanceof Uint8Array ? docData : new Uint8Array(docData));

    const zip = new AdmZip(buf);
    const entries = zip.getEntries();

    // Extract base PDF and .rm stroke files
    let basePdf = null;
    let contentMetadata = null;
    const rmFiles = {};

    for (const entry of entries) {
      const name = entry.entryName;

      if (name.endsWith(".pdf")) {
        basePdf = entry.getData().toString("base64");
      } else if (name.endsWith(".content")) {
        try {
          contentMetadata = JSON.parse(entry.getData().toString("utf8"));
        } catch {
          // non-fatal
        }
      } else if (name.endsWith(".rm")) {
        // Entry name is like "{uuid}/{pageIndex}.rm" — use page index as key
        const pageMatch = name.match(/\/(\d+)\.rm$/);
        const pageKey = pageMatch ? pageMatch[1] : name;
        rmFiles[pageKey] = entry.getData().toString("base64");
      }
    }

    if (!basePdf) {
      return res.status(422).json({ status: "error", message: "Document has no PDF layer (epub or unsupported format?)" });
    }

    const hasAnnotations = Object.keys(rmFiles).length > 0;
    console.log(`📥 Downloaded "${targetName}" — ${hasAnnotations ? Object.keys(rmFiles).length + " page(s) with annotations" : "no annotations yet"}`);

    res.json({
      status: "ok",
      documentId:      docMeta.documentId,
      visibleName:     targetName,
      hasAnnotations,
      basePdf,
      rmFiles,
      contentMetadata: contentMetadata ?? {},
    });

  } catch (err) {
    console.error("Download failed:", err);
    res.status(500).json({ status: "error", message: err.message });
  }
});

async function managePlanners(api, today) {
  try {
    const allMeta = await api.getEntriesMetadata();

    // Find the archive folder — must already exist on the device
    const archiveFolder = allMeta.find(
      e => e.type === "CollectionType" && e.visibleName === ARCHIVE_FOLDER
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

    for (const doc of allMeta) {
      if (doc.type === "CollectionType") continue;

      const match = doc.visibleName.match(/^Daily Planner (\d{4}-\d{2}-\d{2})$/);
      if (!match) continue;

      const plannerDate = new Date(match[1]);

      // In archive folder and older than DELETE_DAYS → trash
      if (doc.parent === archiveFolder.documentId && plannerDate < deleteCutoff) {
        console.log(`Trashing "${doc.visibleName}" (>${DELETE_DAYS} days old)...`);
        await api.move(doc.documentId, "trash");
        deleted++;
      }
      // In root and older than KEEP_DAYS → move to archive
      else if ((doc.parent === "" || doc.parent == null) && plannerDate < archiveCutoff) {
        console.log(`Archiving "${doc.visibleName}" (>${KEEP_DAYS} days old)...`);
        await api.move(doc.documentId, archiveFolder.documentId);
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
