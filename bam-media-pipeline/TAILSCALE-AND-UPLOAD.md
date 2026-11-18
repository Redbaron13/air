# Tailscale + local preprocess + upload

Home PC does the heavy shrinking. The Azure VM only receives small JPEGs and runs the vision worker. Originals stay on your NAS.

## Best way to get images onto the Azure box

1. On the home computer, pick the 400-500 keepers (already curated).
2. Run `preprocess_for_ingest.py` so every still becomes a 1600px JPEG and every video becomes 3 keyframes.
3. Copy **only** `preview/` and `manifest.jsonl` over Tailscale with rsync.
4. On the Azure VM, ingest `/storage/active/preview`.

Do not upload raw Mini 4 Pro DNG/JPEG or 4K masters for this 3-day tag pass. Those files are 10-40x larger and do not improve captions. Build website derivatives later from the NAS originals.

If you later need 2560px web images, rsync the approved originals in a second pass, or run Sharp at home against the NAS using the approved asset list from Postgres.

## Tailscale (recommended: install on the VM host)

On the **Azure Linux VM**:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=bam-azure --ssh
```

On the **home computer**:

```bash
# macOS: brew install tailscale   or the Tailscale app
tailscale up
tailscale status
```

Approve the VM in the Tailscale admin console if it is pending.

Then from home:

```text
http://bam-azure:3080          review UI
ssh bam-azure                  if --ssh was enabled
rsync ... bam-azure:~/bam-storage/active/
```

Do not use Tailscale Funnel. These photos should stay on the tailnet.

### Optional: Tailscale as a compose sidecar

If you would rather not install Tailscale on the host:

1. Create a reusable auth key (ephemeral off, tagged) in Tailscale admin.
2. Put `TS_AUTHKEY=tskey-auth-...` in `.env`.
3. On the Azure VM (Linux):

```bash
docker compose -f docker-compose.yml -f docker-compose.azure.yml -f docker-compose.tailscale.yml up -d tailscale api-server postgres redis
```

`network_mode: host` only works well on Linux. That is what you want on the Azure VM.

Open port 41641/udp on the Azure NSG if direct connections fail; Tailscale will still work through DERP, just slower for rsync.

## Home PC preprocess

Needs `ffmpeg` only.

```bash
cd bam-media-pipeline
python3 scripts/preprocess_for_ingest.py \
  --source ~/Pictures/site-keepers \
  --out ~/bam-staging \
  --host bam-azure \
  --remote-dir ~/bam-storage/active
```

Then either:

```bash
chmod +x scripts/rsync_to_azure.sh
./scripts/rsync_to_azure.sh ~/bam-staging bam-azure ~/bam-storage/active
```

or run the commands printed in `~/bam-staging/rsync.cmd`.

## Azure VM after the files arrive

Mount `~/bam-storage` as `PIPELINE_STORAGE_PATH` so compose sees `/storage/active/preview`.

```bash
docker compose exec api-server python -u /app/scripts/ingest_worker.py /storage/active/preview
./scripts/run_azure_batch.sh
```

Review from home at `http://bam-azure:3080`.
