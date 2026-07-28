# Ariel Memory Stack — Setup Notes

Written after a ~30-minute recovery saga on 2026-07-04. Read this before
troubleshooting from scratch next time.

## The working setup

**Storage root:** `/Users/paulvasholz/storage`
This is the actual Qdrant storage root — it contains `collections/`,
`aliases`, and `raft_state.json` directly. Collections live at
`storage/collections/<name>/`.

**Container:** a single Docker container named `qdrant`, bind-mounted to
that storage root. This is the command that works:

```bash
docker run -d --name qdrant -p 6333:6333 \
  -v /Users/paulvasholz/storage:/qdrant/storage \
  qdrant/qdrant
```

Because this is a bind mount to a plain folder (not a Docker-internal
volume), the data survives `docker rm`, Docker Desktop reinstalls, and
even a full Docker Desktop uninstall — it only depends on that folder
existing on disk. **Do not point the mount at any other path** — earlier
guesses (`/Users/paulvasholz/collections/storage`) looked plausible but
were wrong; always verify with the `ls` check below before trusting a path.

## Daily start/stop

```bash
# Start Docker Desktop first (menu bar whale icon must be steady)
docker ps                              # is 'qdrant' already running?
docker start qdrant                    # if it exists but is stopped
# or, if the container doesn't exist yet, use the docker run command above
```

Wait a second or two after `docker run`/`docker start` before hitting the
API — Qdrant's HTTP server takes ~1s to bind after the container reports
its ID. An immediate `curl` can fail with "Recv failure: Connection reset
by peer" even when everything is fine; just retry.

## Verify it's actually working

```bash
curl http://localhost:6333/collections
```

Expected healthy output includes both:
- `ariel_memories_v2` — the main Ariel memory store
- `mem0migrations` — Mem0's internal migrations collection

If the list comes back **empty**, don't assume data loss — check:
1. `docker inspect qdrant --format '{{json .Mounts}}'` — is `Source`
   exactly `/Users/paulvasholz/storage`? A wrong-but-plausible path is
   the most common cause (this happened twice before landing correctly).
2. `docker logs qdrant --tail 80` — Qdrant is verbose on startup and will
   log `Recovered collection <name>: 1/1 (100%)` for each collection it
   finds, or a `WARN ... Collection config is not found` for anything it
   skips. This log is more trustworthy than guessing from folder listings.

## Known harmless clutter

Inside `storage/collections/` there are a few stray, non-collection
entries left over from an earlier data-copy mishap:
- `.DS_Store`
- `raft_state.json`
- `aliases`
- `collections` (a nested folder, not a collection)

Qdrant logs a `WARN ... Collection config is not found, skipping` for each
on every startup. **This is expected and harmless** — do not delete these
without checking first; they're just being skipped, not causing errors.

## If Docker itself goes missing again

`docker: command not found` means the Docker Desktop CLI is gone, not that
your data is gone. The bind-mounted folder at `/Users/paulvasholz/storage`
is independent of Docker Desktop's installation state. Reinstall Docker
Desktop from docker.com, launch it, then re-run the `docker run` command
above — a container with the same bind mount will find all the same data.

Optional backup (cheap insurance before any risky operation, e.g. the
memory-consolidation script's `--apply` mode):

```bash
cp -r /Users/paulvasholz/storage ~/ariel_storage_backup_$(date +%Y%m%d)
```

## The rest of the stack (for completeness)

- **Ollama** must be running (menu-bar app, or `ollama serve`) — serves the
  chat model, the `llama3.1:8b` Mem0 extractor, and `nomic-embed-text`
  embeddings, all through the same local API. No separate terminal window
  is needed to "run" a model; Ollama loads models on demand.
- **The pipeline script** (`ollama_ariel_chatMemRev2.py`) connects to both
  Qdrant (6333) and Ollama (11434) directly — start Qdrant and Ollama
  first, then run the script.
