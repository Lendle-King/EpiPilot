# EpiPilot Browser Bridge (Chrome extension)

Lets the local EpiPilot backend drive pages in your **normal, logged-in Chrome**:
tab management, DOM reads, clicks, typing and screenshots. This solves the
automation-browser login wall (Google/ChatGPT reject fresh automation profiles)
by reusing the session you already have.

## Install (once)

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** → select this `extension/` directory.
3. Keep the EpiPilot backend running (`http://127.0.0.1:8787`); the badge will
   connect automatically and reconnect if the backend restarts.

## How EpiPilot uses it

When the extension is connected, ChatGPT driving automatically prefers it
(mode `auto`; force with `EPIPILOT_CHATGPT_MODE=extension`, disable with
`EPIPILOT_CHATGPT_MODE=cdp`). The backend opens/reuses a `chatgpt.com` tab,
types the iteration pack, clicks send, and reads the assistant reply.

## Commands (all local, all bounded)

| Command | Purpose |
|---|---|
| `tabs` / `open_tab` / `tab_focus` | tab management (reuse existing chatgpt.com tab) |
| `query` / `read_state` | DOM reads (composer/login/stop-button presence, reply text) |
| `click` / `type` | click a selector, type into composer (contenteditable-aware) |
| `screenshot` | capture the visible tab as PNG (debugging aid) |

## Security properties

- Talks only to the local HTTP polling endpoints under
  `http://127.0.0.1:8787/api/browser-bridge/*`; never contacts remote hosts.
- Accepts commands only from that local connection; no remote control surface.
- Never reads credentials, cookies or page secrets; only the DOM signals listed above.
- Operates only while the local backend is connected; no background scraping.
