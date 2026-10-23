# EpiPilot Research Workspace integration

This development branch ports PR #10's local-first React/FastAPI research
workspace onto the current evidence-gated `main`. It intentionally keeps
imported notes in **staging**, not `research/store.py` canonical evidence.
The independent scientific review rules remain authoritative.

## Start
```bash
python -m pip install -e '.[ui]'
python -m epipilot.workspace.api
# separate terminal
cd frontend && npm install && npm run dev
```
Open `http://127.0.0.1:5173`; the API listens at `127.0.0.1:8787`.

This port contains repository creation/adoption, pinned Git source inventory,
human review of provisional notes, local audit trail and research UI. It is
not a new autonomous executor, and reviewed notes must never automatically
turn into EpiPilot-v2 measurement-validated Evidence. Compare the richer
semantic onboarding in PR #11 only after this portable baseline passes CI.

Provenance: `feat/research-workspace-ui` at `080074afcc1f3494975b52ccb99f1f37a8e6d371`.
