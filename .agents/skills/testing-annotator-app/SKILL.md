---
name: testing-annotator-app
description: How to run and E2E-test the Annotator app (FastAPI + React) on this Windows box — backend launch, torch/VC++ fix, fabricating training-run states, and GUI automation quirks.
---

# Testing the Annotator app (proj-todensekkei-2609-2611)

## Devin Secrets Needed
- None for local testing. `GH_ISSUES_PAT` exists for GitHub API (use as `GH_TOKEN` bearer — `gh` CLI fails because the git remote is a devin proxy, so call `https://api.github.com/repos/elith-co-jp/proj-todensekkei-2609-2611/...` directly).

## Run the app locally
- Python: `.venv\Scripts\python.exe` (a uv trampoline → child process shows as the uv-managed base python in `tasklist`; that's the SAME server, not a second instance).
- Backend + built frontend: `SEQANNO_DESKTOP=1 SEQANNO_NO_BROWSER=1 SEQANNO_DATA_DIR=./data_manual .venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010` (run from repo root). FastAPI serves `frontend/dist` via catch-all.
- Rebuild frontend after source edits: `cd frontend && npm.cmd run build` (bare `npm` isn't on PATH). Verify served bundle: `curl -s http://127.0.0.1:8010/ | grep -o 'index-[^"]*.js'` vs `ls frontend/dist/assets`.
- DB migrations run at app startup (`database.py::_migrate_columns`) — restart uvicorn after pulling code that adds columns.
- Seed data lives in `data_manual/` (SQLite `seqanno.db` + `images/`). Routes are hash-based: `/#/` projects, `/#/ml` ML ops, `/#/projects/:id` editor.

## CRITICAL: torch fails without VC++ 2015-2022 redist
- Symptom: `import torch` → `OSError: [WinError 1114] ... c10.dll` (DLL init routine failed). NOT a missing-file error (126) — it's a version/init failure, so dropping vcruntime140_1.dll next to the exe does NOT fix it.
- Fix once per box: `curl -L -o vc_redist.x64.exe https://aka.ms/vs/17/release/vc_redist.x64.exe` then run it with `/install /quiet /norestart`. **Do not launch it from Git Bash** — MSYS2 mangles `/install` into a path and the installer hangs; use `shell_flavor: powershell` + `Start-Process -Wait`, or `MSYS2_ARG_CONV_EXCL='*'`.
- Check `curl /api/ml/status` → `"ultralytics": true` only proves `find_spec` succeeded; verify real inference with `POST /api/ml/inference/run {"project_ids":[<id>]}` (≈10–20s on CPU).
- After fixing torch you must RESTART uvicorn — a server started before the fix can't import it.

## Fabricating ML state without real training
- Candidate model: `POST /api/ml/models` accepts any bytes if filename ends `.pt` (no content check beyond `PK` magic OR extension). Then `UPDATE ml_models SET source='trained', file_name='run_N_best.pt' WHERE id=?` for realism.
- Pending run for the 採用判定 panel: `INSERT INTO training_runs (status, project_ids_json, image_count, epochs, imgsz, base_model, result_model_id, metrics_json, decision, baseline_metrics_json, baseline_label, started_at, finished_at) VALUES ('success','[]',3,10,640,?, <model_id>, '{"metrics/mAP50-95(B)":"0.8000",...}', 'pending', '{...}', '<baseline name>', datetime('now'), ...)`. The UI shows the panel iff `status='success' AND decision='pending' AND result_model` resolves. Set metrics ABOVE baseline for a green-delta adopt demo, BELOW for red-delta reject demo.
- `use PYTHONUTF8=1` for python scripts that touch the DB — Japanese strings crash cp1252 consoles.

## GUI automation quirks on this box
- Chrome CDP at `http://localhost:29229`; DevTools page at 1920×1080 CSS but `computer` screenshots are 1024×768 — and the mapping is NOT a clean 1920/1080→1024/768 scale (observed ≈0.65× for x). Physical clicks on page elements frequently miss; when they do, use `browser_console` `el.click()` — it still exercises the real React onClick path.
- Chrome's native file-open dialog may not appear from synthetic clicks. To drive a real `<input type=file>` upload: copy the test file into `frontend/dist/` (FastAPI serves it), then in `browser_console`: `const b=await (await fetch('/file.jpg')).blob(); const f=new File([b],'file.jpg',{type:'image/jpeg'}); const dt=new DataTransfer(); dt.items.add(f); input.files=dt.files; input.dispatchEvent(new Event('change',{bubbles:true}))` — hits the identical React onChange → API path.