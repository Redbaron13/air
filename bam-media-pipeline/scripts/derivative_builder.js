const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

async function buildVariants(asset, outDir, db, sharp = require('sharp')) {
  if (asset.workflow_state !== 'APPROVED') throw new Error('Human approval required');
  if (!['still', 'image', 'photo'].includes(asset.media_kind)) throw new Error('Only stills supported');
  if (!/^[A-Za-z0-9_-]+$/.test(asset.asset_id)) throw new Error('Invalid asset ID');
  const source = fs.realpathSync(asset.source_path);
  const root = fs.realpathSync(process.env.ACTIVE_DIR || '/storage/active');
  const relative = path.relative(root, source);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Source outside staging root');
  // Verify bytes before rendering, not merely the path recorded at ingestion.
  const bytes = fs.readFileSync(source);
  if (hash(bytes) !== asset.original_sha256) throw new Error('Original content changed');
  const results = [], widths = new Set();
  for (const width of [400, 800, 1400, 2560]) {
    const {data, info} = await sharp(bytes).rotate().resize({width, withoutEnlargement:true})
      .webp({quality:82}).toBuffer({resolveWithObject:true});
    if (widths.has(info.width)) continue;
    widths.add(info.width);
    const sha = hash(data);
    const key = `bam/web/stills/${asset.asset_id}/${sha}.webp`;
    const target = path.join(outDir, key);
    fs.mkdirSync(path.dirname(target), {recursive:true});
    if (fs.existsSync(target)) {
      if (hash(fs.readFileSync(target)) !== sha) throw new Error('Existing derivative corrupted');
    } else {
      fs.writeFileSync(target, data, {flag:'wx'});
    }
    const copyId = hash(Buffer.from(`${asset.asset_id}:${sha}`));
    const row = {parent_asset_id:asset.asset_id, copy_id:copyId, size_tier:String(width),
      version_code:'webp-v1', format:'webp', width:info.width, height:info.height,
      byte_size:data.length, sha256:sha, r2_storage_key:key};
    if (db) await db.query(`INSERT INTO asset_copies
      (copy_id,parent_asset_id,size_tier,version_code,format,width,height,byte_size,sha256,r2_storage_key)
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT (copy_id) DO NOTHING`,
      [copyId,asset.asset_id,row.size_tier,row.version_code,'webp',info.width,info.height,data.length,sha,key]);
    results.push(row);
  }
  return results;
}

async function main() {
  const {Client} = require('pg');
  if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is required');
  const db = new Client({connectionString:process.env.DATABASE_URL, connectionTimeoutMillis:10000});
  await db.connect();
  let count = 0;
  try {
    // One-shot bounded batch, restartable by an operator or scheduler.
    for (let i=0; i<Number(process.env.DERIVATIVE_BATCH_SIZE || 20); i++) {
      await db.query('BEGIN');
      try {
        const {rows} = await db.query(`SELECT * FROM assets a WHERE workflow_state='APPROVED'
          AND media_kind IN ('still','image','photo') AND NOT EXISTS
          (SELECT 1 FROM asset_copies c WHERE c.parent_asset_id=a.asset_id AND c.version_code='webp-v1')
          ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1`);
        if (!rows.length) {await db.query('COMMIT'); break;}
        await buildVariants(rows[0], process.env.EXPORTS_DIR || '/storage/exports', db);
        await db.query('COMMIT'); count++;
      } catch (error) {await db.query('ROLLBACK'); throw error;}
    }
    console.log(JSON.stringify({status:'complete',assets:count}));
  } finally {await db.end();}
}
module.exports = {buildVariants};
if (require.main === module) main().catch(error => {
  // Connection errors can include secrets. Print only the error type.
  console.error(`Derivative batch failed (${error.name}); check configuration and source integrity`);
  process.exitCode=1;
});
