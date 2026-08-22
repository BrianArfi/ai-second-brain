// Service worker for the tabCapture probe.
//
// The whole point of this file is that NOTHING here is triggered by a human.
// It wakes on install, finds the target tab by URL, and starts capturing. If
// `chrome.tabCapture.getMediaStreamId` needs a user gesture, this is where it
// fails, and it fails with a specific message ("Extension has not been invoked
// for the current page") rather than silently.

const TARGET = '__TARGET__';

let finished = false;
// Set once a keyboard command has fired. A command is one of the four things
// Chrome counts as "invoking" an extension (alongside an action click, a
// context-menu pick and the omnibox), and it is the only one of the four that
// can plausibly be delivered without a human at the keyboard.
let invoked = false;

async function ensureOffscreen() {
  // tabCapture's stream has to be consumed by a document, and a service worker
  // is not one. The offscreen document is the MV3 replacement for the old
  // background page, and it is what makes unattended capture possible at all.
  const has = await chrome.offscreen.hasDocument();
  if (!has) {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['USER_MEDIA'],
      justification: 'measure the meeting tab audio'
    });
  }
}

async function report(tabId, result) {
  // Keep retrying while the only failure so far is the invocation gate: the
  // whole question is whether a keyboard command clears it.
  if (result && result.ok) {
    finished = true;
  } else if (invoked) {
    finished = true;
  }
  if (result) {
    result.invoked = invoked;
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      // MAIN world, so the CDP session driving the page can read it. An
      // isolated-world write would be invisible to `page.evaluate`.
      world: 'MAIN',
      func: (r) => { window.__tabCaptureProbe = r; },
      args: [result]
    });
  } catch (e) {
    // Nothing else to do: the page is the only channel back out.
  }
}

async function tick() {
  if (finished) {
    return;
  }
  let tab;
  try {
    const tabs = await chrome.tabs.query({});
    tab = tabs.find((t) => t.url && t.url.startsWith(TARGET));
    if (!tab) {
      return;
    }
    await ensureOffscreen();
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    const result = await chrome.runtime.sendMessage({ type: 'capture', streamId });
    await report(tab.id, result || { ok: false, error: 'no reply from offscreen' });
  } catch (e) {
    const err = { ok: false, error: (e && e.name ? e.name + ': ' : '') + (e && e.message ? e.message : String(e)) };
    if (tab) {
      await report(tab.id, err);
    }
  }
}

chrome.commands.onCommand.addListener(() => {
  invoked = true;
  finished = false;
  tick();
});

chrome.runtime.onInstalled.addListener(() => {
  setInterval(tick, 500);
  tick();
});
setInterval(tick, 500);
