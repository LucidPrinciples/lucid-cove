# Bulk storage (second disk)

Self-hosted Coves often fill the OS disk with Nextcloud files, video pipeline media, and local LLM weights. This guide is the product path for pointing heavy data at a large host volume.

## What lives where

| Data | Default | How to put it on a large disk |
|------|---------|--------------------------------|
| Ollama model blobs | Host Ollama data dir | Set `OLLAMA_MODELS` on the **host** Ollama service (systemd/Docker). Cove only needs `OLLAMA_BASE_URL`. |
| Nextcloud (files, video vault) | Compose named volume `nextcloud_data` | Set `NEXTCLOUD_HOST_PATH` in the Cove `.env` to a host directory, then recreate the `nextcloud` service. |
| App runtime (`/app/data`) | Named volume `app_data` | Usually small; leave on OS disk unless you have a reason. |
| Postgres | Named volume | Leave on fast disk; not bulk media. |

Optional label for operators and docs: `COVE_BULK_ROOT` (e.g. `/data/cove-bulk`). It is informational in Settings; compose uses the concrete paths below.

## Fresh install

1. Create the host tree (example):

   ```bash
   sudo mkdir -p /data/cove-bulk/nextcloud
   sudo chown -R 33:33 /data/cove-bulk/nextcloud   # www-data uid in official NC image; adjust if needed
   ```

2. In the Cove `.env`:

   ```bash
   COVE_BULK_ROOT=/data/cove-bulk
   NEXTCLOUD_HOST_PATH=/data/cove-bulk/nextcloud
   ```

3. `docker compose up -d` (or your provisioner). The example compose mounts  
   `${NEXTCLOUD_HOST_PATH:-nextcloud_data}:/var/www/html`.

4. Point host Ollama at the bulk disk (outside Cove):

   ```ini
   # e.g. systemd drop-in
   Environment=OLLAMA_MODELS=/data/ollama
   ```

5. Mission Control → Settings (admin) → **Bulk storage** shows the env the app sees.

## Migrate an existing Nextcloud volume

Plan a short maintenance window. Do not only change the env without copying data.

1. Note the current volume name (`docker volume ls` / `docker inspect` on the nextcloud container).
2. Stop the Nextcloud container (and app if it writes to NC during migrate).
3. Copy data to the new host path (preserve ownership):

   ```bash
   # Example — adjust volume path from docker volume inspect
   sudo rsync -aHAX --info=progress2 \
     /var/lib/docker/volumes/<project>_nextcloud_data/_data/ \
     /data/cove-bulk/nextcloud/
   ```

4. Set `NEXTCLOUD_HOST_PATH` (and optional `COVE_BULK_ROOT`) in `.env`.
5. Recreate the nextcloud service so the bind replaces the named volume mount.
6. Verify WebDAV / Mission Control Files / video pipeline paths.
7. Only after confidence, remove the old volume.

## Model Lab and Hugging Face GGUFs

Model Lab lists tags from the host Ollama inventory. Import weights on the host:

```bash
ollama pull hf.co/org/model-name
# or: ollama create my-tag -f Modelfile   # FROM /path/to/file.gguf
```

HF-style tags contain `/` (e.g. `hf.co/org/name:latest`). Mission Control Model Lab accepts those tags for sessions and A/B runs.

## Root disk pressure (ops)

If the OS volume is full but Ollama already lives on a second disk, reclaim Docker image/build cache carefully, and move **Nextcloud** with the bind above. Do not assume models are still on `/`.

## Product surfaces

- Env: `COVE_BULK_ROOT`, `NEXTCLOUD_HOST_PATH` (`src/env.py`, `.env.example`)
- Compose: `docker/docker-compose.example.yml`
- Settings API: `GET /api/settings/bulk-storage`
- Admin UI: Settings → Bulk storage
