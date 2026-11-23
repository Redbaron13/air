# AIR

Baron Aerial Media review console and media pipeline.

The review console is the Node server in this directory. The pipeline is the Python package in [`pipeline/`](pipeline/README.md). They are separate entry points. The console still uses its in-memory sample store. The pipeline writes `library/Database/tempID.db` and does not replace the console.

## Review console

Requires [Bun](https://bun.sh).

```sh
bun install
bun run dev
```

The server listens on port 3000. Open `http://127.0.0.1:3000`. `bun run lint` typechecks `server.ts` and does not start the server.

Mission Control and the review pages are static files in `public/`. Shot-angle bands used by the console live in `src/logic.ts`. The pipeline EXIF stage uses those same bands.

## Pipeline

Stage commands, identifier jobs, tags, folders, and which steps need Ollama, Azure, Postgres, or R2 are documented in [`pipeline/README.md`](pipeline/README.md).

Create the local folders:

```sh
sh setup.sh
python3 -m pipeline.air prep init
```

`setup.sh` does not write `.env` or a password. Prep, grouping, id assignment, derivatives, and the website manifest run from the local journal. `AIR_VISION_BACKEND=skip` is the path with no vision network. Azure runs only when `AIR_VISION_BACKEND=azure` and `AZURE_OPENAI_API_KEY` is set.

Offline checks:

```sh
python3 -m unittest discover -s pipeline/tests
```
