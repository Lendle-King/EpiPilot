// EpiPilot Browser Bridge — drives bounded commands in your normal Chrome.
//
// Transport: plain HTTP polling against the local EpiPilot backend. A
// WebSocket would die with the MV3 service worker; polling survives
// suspension because every request is its own event loop turn. Commands come
// only from the local backend (127.0.0.1); the extension never contacts
// remote hosts, never reads credentials, and only operates on tabs while the
// local backend is running.

const BASE = "http://127.0.0.1:8787";
const ALARM = "epipilot-bridge-poll";
let busy = false;

async function post(path, body) {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function pollLoop() {
  if (busy) return;
  busy = true;
  try {
    // Drain the command queue within one wake-up so a driver session flows
    // quickly; the alarm covers idle periods and worker suspension.
    for (let hops = 0; hops < 40; hops += 1) {
      const res = await fetch(BASE + "/api/browser-bridge/commands");
      const data = await res.json();
      const command = data.command;
      if (!command) break;
      await handle(command);
    }
  } catch {
    /* backend unavailable; the next alarm retries */
  } finally {
    busy = false;
  }
}

async function handle(message) {
  const { id, action, ...payload } = message || {};
  if (!id) return;
  try {
    const result = await dispatch(action, payload);
    await post("/api/browser-bridge/result", { id, ok: true, result });
  } catch (error) {
    await post("/api/browser-bridge/result", {
      id,
      ok: false,
      error: String((error && error.message) || error),
    });
  }
}

// --- injected page helpers (self-contained; no closure state) ----------------

function injectedQuery(args) {
  for (const selector of args.selectors) {
    const el = document.querySelector(selector);
    if (el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length)) {
      return { found: true, selector, text: (el.innerText || el.value || "").slice(0, 4000) };
    }
  }
  return { found: false };
}

function injectedClick(args) {
  for (const selector of args.selectors) {
    const el = document.querySelector(selector);
    if (el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length)) {
      el.click();
      return { clicked: true, selector };
    }
  }
  return { clicked: false };
}

function injectedType(args) {
  for (const selector of args.selectors) {
    const el = document.querySelector(selector);
    if (!el) continue;
    el.focus();
    let registered = false;
    if (el.isContentEditable) {
      // ProseMirror-style editors ignore direct textContent writes; insertText
      // through the editing pipeline registers the text in their document model.
      try {
        document.execCommand("selectAll", false, undefined);
        registered = document.execCommand("insertText", false, args.text);
      } catch {
        registered = false;
      }
      if (!registered) {
        el.textContent = args.text;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: args.text }));
      }
    } else {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, args.text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      registered = true;
    }
    const now = (el.innerText || el.value || "");
    const sample = args.text.slice(0, Math.min(40, args.text.length));
    return { typed: now.includes(sample), selector, registered: now.length > 0 };
  }
  return { typed: false };
}

function injectedReadState(args) {
  try {
    const turns = document.querySelectorAll('[class*="turn"]');
    if (turns.length) turns[turns.length - 1].scrollIntoView({ block: "end" });
  } catch {
    /* ignore */
  }
  const visible = (el) => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
  const firstVisible = (selectors) => {
    for (const selector of selectors || []) {
      const el = document.querySelector(selector);
      if (visible(el)) return { el, selector };
    }
    return null;
  };
  const composerHit = firstVisible(args.composerSelectors);
  const login = (args.loginSelectors || []).some((s) => visible(document.querySelector(s)));
  const stopVisible = (args.stopSelectors || []).some((s) => visible(document.querySelector(s)));

  const assistantSelectors = (args.assistantSelectors || []).concat([
    '[data-message-author-role="assistant"]',
    '[data-testid^="conversation-turn"]',
    '[data-message-id]',
  ]);
  let assistantCount = 0;
  let lastAssistantText = "";
  for (const selector of assistantSelectors) {
    const nodes = document.querySelectorAll(selector);
    if (nodes.length) {
      assistantCount = nodes.length;
      // The newest turn container can be an empty placeholder; take the last
      // turn that actually carries text.
      for (let i = nodes.length - 1; i >= 0; i -= 1) {
        const text = (nodes[i].innerText || "").trim();
        if (text) {
          lastAssistantText = text.slice(0, 20000);
          break;
        }
      }
      break;
    }
  }
  const main = document.querySelector("main");
  const fullText = main ? main.innerText || "" : document.body.innerText || "";
  // Bind recovery to the latest user turn, never an ID left in older history.
  const userMarkers = [...fullText.matchAll(/^(?:你说|You said)[：:]\s*$/gmi)];
  const latestUser = userMarkers[userMarkers.length - 1];
  const assistantMarkers = [...fullText.matchAll(/^(?:ChatGPT 说|ChatGPT said|ChatGPT)[：:]\s*$/gmi)];
  const latestAssistant = assistantMarkers[assistantMarkers.length - 1];
  // An old assistant answer followed by a new user prompt is not a new reply.
  const assistantText = latestAssistant && latestUser && latestAssistant.index > latestUser.index
    ? fullText.slice(latestAssistant.index + latestAssistant[0].length).trimStart()
    : "";
  let userText = latestUser ? fullText.slice(latestUser.index + latestUser[0].length) : "";
  const nextAssistant = userText.search(/^(?:ChatGPT 说|ChatGPT said|ChatGPT)[：:]\s*$/mi);
  if (nextAssistant >= 0) userText = userText.slice(0, nextAssistant);
  const routeStart = userText.indexOf("ROUTE:");
  const routeHeader = userText.match(/^ROUTE_RUN_ID:\s*(\S+)\s*$/m);
  const routeId = routeStart >= 0
    ? userText.slice(routeStart).match(/"run_id"\s*:\s*"([^"]+)"/)
    : null;
  return {
    conversationUrl: location.href,
    userTurnCount: userMarkers.length,
    lastUserRouteRunId: routeHeader ? routeHeader[1] : routeId ? routeId[1] : null,
    assistantText: assistantText.slice(0, 60000),
    composer: composerHit ? composerHit.selector : null,
    login,
    stopVisible,
    composerText: composerHit ? (composerHit.el.innerText || composerHit.el.value || "").slice(0, 4000) : "",
    assistantCount,
    lastAssistantText,
    domSample: (main ? main.innerText || "" : document.body.innerText || "").slice(-2000),
  };
}

// --- command dispatch --------------------------------------------------------

async function dispatch(action, payload) {
  switch (action) {
    case "tabs": {
      const match = payload.match || null;
      const tabs = await chrome.tabs.query({});
      return {
        tabs: tabs
          .filter((t) => !match || (t.url || "").includes(match))
          .map((t) => ({ tab_id: t.id, url: t.url || "", title: t.title || "", active: !!t.active })),
      };
    }
    case "open_tab": {
      const match = payload.match || payload.url || null;
      const tabs = await chrome.tabs.query({});
      const existing = tabs.find((t) => t.url && match && t.url.includes(match));
      if (existing) {
        await chrome.tabs.update(existing.id, { active: true });
        return { tab_id: existing.id, reused: true };
      }
      const tab = await chrome.tabs.create({ url: payload.url, active: true });
      return { tab_id: tab.id, reused: false };
    }
    case "tab_focus": {
      await chrome.tabs.update(payload.tab_id, { active: true });
      return { focused: true };
    }
    case "query": {
      const [out] = await chrome.scripting.executeScript({
        target: { tabId: payload.tab_id },
        func: injectedQuery,
        args: [{ selectors: payload.selectors || [] }],
      });
      return out.result;
    }
    case "click": {
      const [out] = await chrome.scripting.executeScript({
        target: { tabId: payload.tab_id },
        func: injectedClick,
        args: [{ selectors: payload.selectors || [] }],
      });
      return out.result;
    }
    case "type": {
      const [out] = await chrome.scripting.executeScript({
        target: { tabId: payload.tab_id },
        func: injectedType,
        args: [{ selectors: payload.selectors || [], text: payload.text || "" }],
      });
      return out.result;
    }
    case "read_state": {
      const [out] = await chrome.scripting.executeScript({
        target: { tabId: payload.tab_id },
        func: injectedReadState,
        args: [payload],
      });
      return out.result;
    }
    case "screenshot": {
      const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: "png" });
      return { dataUrl };
    }
    default:
      throw new Error("unknown action: " + action);
  }
}

// --- lifecycle: wake, heartbeat, poll -----------------------------------------

chrome.runtime.onInstalled.addListener(pollLoop);
chrome.runtime.onStartup.addListener(pollLoop);
chrome.action?.onClicked?.addListener(pollLoop);
try {
  chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM) pollLoop();
  });
} catch {
  /* alarms unavailable; extension events still trigger polls */
}

pollLoop();
