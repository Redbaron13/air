const sharp = require("sharp");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { Client } = require("pg");

const db = new Client({ connectionString: process.env.DATABASE_URL });

function log(level, event, details = {}) {
  console.log(`${new Date().toISOString()} ${level} bam.derivative ${event} ${JSON.stringify(details)}`);
}

db.connect()
  .then(() => log("INFO", "database_connected"))
  .catch((error) => {
    log("ERROR", "database_connection_failed", { error: error.message });
    process.exitCode = 1;
  });

const VARIANTS = [
  { sizeTier: "3M4", code: "I10", width: 400, format: "webp", quality: 80 },
  { sizeTier: "6M7", code: "I10", width: 800, format: "webp", quality: 82 },
  { sizeTier: "1T2", code: "I12", width: 1400, format: "avif", quality: 78 },
  { sizeTier: "2D4", code: "I10", width: 2560, format: "webp", quality: 85 }
];

async function buildVariants(assetId, sourcePath, uniqueStem, outDir) {
  log("INFO", "variant_build_started", { assetId, sourcePath, outDir, variants: VARIANTS.length });
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  for (const v of VARIANTS) {
    const copyId = `${v.sizeTier}-${uniqueStem}-${v.code}`;
    const filename = `${copyId}.${v.format}`;
    const target = path.join(outDir, filename);

    let p = sharp(sourcePath).resize(v.width, null, { withoutEnlargement: true });
    if (v.format === "webp") p = p.webp({ quality: v.quality });
    else if (v.format === "avif") p = p.avif({ quality: v.quality });
    else p = p.jpeg({ quality: v.quality });

    log("INFO", "variant_render_started", { assetId, copyId, target, width: v.width, format: v.format });
    const info = await p.toFile(target);
    const hash = crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex");
    
    await db.query(`
      INSERT INTO asset_copies (copy_id, parent_asset_id, size_tier, version_code, format, width, height, byte_size, sha256, r2_storage_key)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    `, [copyId, assetId, v.sizeTier, v.code, v.format, info.width, info.height, info.size, hash, `bam/web/stills/${assetId}/${filename}`]);
    log("INFO", "variant_build_complete", { assetId, copyId, bytes: info.size, sha256: hash });
  }
  log("INFO", "variant_build_finished", { assetId });
}

process.on("unhandledRejection", (error) => log("ERROR", "unhandled_rejection", { error: error.message }));
process.on("uncaughtException", (error) => {
  log("ERROR", "uncaught_exception", { error: error.message });
  process.exit(1);
});

log("INFO", "worker_started", { dispatcher: "external message queue required" });