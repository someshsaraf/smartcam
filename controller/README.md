# Controller (Pi 5)

Expected layout:

| Path | Purpose |
|------|---------|
| `backend/` | FastAPI app (`uvicorn app.main:app`), `requirements.txt`, `.env` |
| `frontend/` | React + Vite dashboard (`package.json`, `npm run dev`) |
| `shared/` | Optional Python package path added to `PYTHONPATH` when present |

Some clones or minimal checkouts only contain **`backend/`**. In that case the dashboard **was not removed** — it was never in that tree. Use either:

1. **Full repository** — `git pull` / re-clone so `controller/frontend/` exists, then `./start.sh controller --install` again, or  
2. **API only** — `./start.sh controller --install` (with the updated `start.sh`) skips npm when `frontend/` is missing; use **`http://<pi5>:8000/docs`** for OpenAPI.

From repo root, `./start.sh controller` does the same: backend starts, Vite is skipped if there is no `package.json` under `controller/frontend`.

See [`../docs/SETUP_PI5.md`](../docs/SETUP_PI5.md) for Mosquitto, MediaMTX, and Hailo.
