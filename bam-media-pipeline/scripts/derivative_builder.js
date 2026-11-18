const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Client } = require('pg');
const sharp = require('sharp');

const db = new Client({ connectionString: process.env.DATABASE_URL });

function log(level, event, details = {}) {
  console.log(`${new Date().toISOString()} ${level} bam.derivative ${event} ${JSON.stringify(details)}`);
}

const VARIANTS = [
  { sizeTier: "3M4", code: "I10", width: 400, format: "webp", quality: 80 },
  { sizeTier: "6M7", code: "I10", width: 800, format: "webp", quality: 82 },
  { sizeTier: "1T2", code: "I12", width: 1400, format: "avif", quality: 78 },
  { sizeTier: "2D4", code: "I10", width: 2560, format: "webp", quality: 85 }
];

async function buildVariants(asset, outDir, client = db, imageProcessor = sharp) {
  const { asset_id: assetId, source_path: sourcePath, unique_stem: uniqueStem, original_sha256: originalSha256 } = asset;
  log("INFO", "variant_build_started", { assetId, sourcePath, outDir, variants: VARIANTS.length });
  const activeDir = fs.realpathSync(process.env.ACTIVE_DIR || "/storage/active");
  const resolvedSource = fs.realpathSync(sourcePath);
  if (!resolvedSource.startsWith(`${activeDir}${path.sep}`)) {
    throw new Error(`Source is outside the active staging directory: ${sourcePath}`);
  }
  if (!fs.statSync(resolvedSource).isFile()) throw new Error(`Source is not a regular file: ${sourcePath}`);
  const sourceHash = await hashFile(resolvedSource);
  if (sourceHash !== originalSha256) throw new Error(`Source hash changed since ingestion: ${sourcePath}`);

  for (const v of VARIANTS) {
    const copyId = `${v.sizeTier}-${uniqueStem}-${v.code}`;
    const filename = `${copyId}.${v.format}`;
    const key = `bam/web/stills/${assetId}/${filename}`;
    const target = path.join(outDir, key);
    fs.mkdirSync(path.dirname(target), { recursive: true });

    let p = imageProcessor(resolvedSource).rotate().resize(v.width, null, { withoutEnlargement: true });
    if (v.format === "webp") p = p.webp({ quality: v.quality });
    else if (v.format === "avif") p = p.avif({ quality: v.quality });
    else p = p.jpeg({ quality: v.quality });

    log("INFO", "variant_render_started", { assetId, copyId, target, width: v.width, format: v.format });
    const info = await p.toFile(target);
    const derivativeHash = await hashFile(target);
    
    const values = [copyId, assetId, v.sizeTier, v.code, v.format, info.width, info.height, info.size, derivativeHash, key];
    const keyParameter = `$${values.length}`;
    const existing = await client.query("SELECT copy_id FROM asset_copies WHERE copy_id = $1", [copyId]);
    if (existing.rowCount) {
      await client.query(`
        UPDATE asset_copies SET parent_asset_id = $2, size_tier = $3, version_code = $4, format = $5,
          width = $6, height = $7, byte_size = $8, sha256 = $9, r2_storage_key = ${keyParameter}
        WHERE copy_id = $1
      `, values);
    } else {
      await client.query(`
        INSERT INTO asset_copies (copy_id, parent_asset_id, size_tier, version_code, format, width, height, byte_size, sha256, r2_storage_key)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    `, values);
    }
    log("INFO", "variant_build_complete", { assetId, copyId, bytes: info.size, sha256: derivativeHash, key });
  }
  log("INFO", "variant_build_finished", { assetId });
}

async function hashFile(filePath) {
  const digest = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(filePath)) digest.update(chunk);
  return digest.digest("hex");
}

async function dispatchApprovedAssets(client = db) {
  const result = await client.query(`
    SELECT asset_id, source_path, unique_stem, original_sha256
    FROM assets
    WHERE workflow_state = 'APPROVED' AND media_kind = 'still'
    ORDER BY created_at, asset_id
  `);
  let failures = Number(false);
  for (const asset of result.rows) {
    try {
      await buildVariants(asset, process.env.DERIVATIVE_OUTPUT_DIR || "/storage/exports", client);
    } catch (error) {
      failures += Number(true);
      log("ERROR", "variant_build_failed", { assetId: asset.asset_id, error: error.message });
    }
  }
  log("INFO", "derivative_batch_finished", { assets: result.rows.length, failures });
  return Boolean(failures);
}

process.on("unhandledRejection", (error) => log("ERROR", "unhandled_rejection", { error: error.message }));
process.on("uncaughtException", (error) => {
  log("ERROR", "uncaught_exception", { error: error.message });
  process.exit(1);
});

async function main() {
  if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required");
  await db.connect();
  try {
    log("INFO", "worker_started", { dispatcher: "approved asset batch" });
    log("INFO", "database_connected");
    const failed = await dispatchApprovedAssets();
    if (failed) process.exitCode = 1;
  } finally {
    await db.end();
  }
}

if (require.main === module) {
  main().catch((error) => {
    log("ERROR", "worker_failed", { error: error.message });
    process.exitCode = 1;
  });
}

module.exports = { buildVariants, dispatchApprovedAssets, hashFile };
