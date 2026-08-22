/* ASB Meeting Recorder -- phone client for meeting-recorder/ingest_server.py
 *
 * Records the room with MediaRecorder and streams a chunk every 15 seconds to
 * whichever host answers first. Chunks that fail to send are kept in IndexedDB
 * and drained later, so a meeting recorded with no connectivity at all still
 * reaches the harness once the phone is back on a network the laptop is on.
 *
 * Two things drive the whole design:
 *
 *  1. Android kills backgrounded browser tabs. Streaming in 15-second pieces
 *     means a tab that dies at minute 47 has already landed 47 minutes; the
 *     server's stale sweep then finishes that session into a real transcript.
 *     Buffering the whole meeting in memory would lose all of it.
 *  2. The session may have to be created LATE. If /session cannot be reached
 *     when recording starts, the recording still runs against a local id, and
 *     the real session is opened at drain time with the original start time
 *     passed along, so the file name and the meeting timestamp stay honest.
 */
'use strict';

const CHUNK_MS = 15000;
const DB_NAME = 'asb-recorder';
const DB_VERSION = 1;

const $ = (id) => document.getElementById(id);

/* ---------- settings ---------- */

const settings = {
  get token() { return localStorage.getItem('token') || ''; },
  set token(v) { localStorage.setItem('token', v.trim()); },
  get hosts() {
    const raw = localStorage.getItem('hosts');
    if (raw) return JSON.parse(raw);
    // Seeded with the origin that served this page: that host is reachable by
    // definition. A second host is added by hand in Settings.
    return [location.origin];
  },
  set hosts(list) { localStorage.setItem('hosts', JSON.stringify(list)); },
  get lastHost() { return localStorage.getItem('lastHost') || ''; },
  set lastHost(v) { localStorage.setItem('lastHost', v); },
};

/* ---------- tiny IndexedDB layer ---------- */

let dbPromise = null;

function db() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const d = req.result;
      if (!d.objectStoreNames.contains('sessions')) {
        d.createObjectStore('sessions', { keyPath: 'localId' });
      }
      if (!d.objectStoreNames.contains('queue')) {
        const s = d.createObjectStore('queue', { keyPath: 'id', autoIncrement: true });
        s.createIndex('localId', 'localId');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

function tx(store, mode, fn) {
  return db().then((d) => new Promise((resolve, reject) => {
    const t = d.transaction(store, mode);
    const result = fn(t.objectStore(store));
    t.oncomplete = () => resolve(result && result.__req ? result.__req.result : result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  }));
}

const wrap = (req) => ({ __req: req });

const putSession = (s) => tx('sessions', 'readwrite', (st) => st.put(s));
const getSession = (id) => tx('sessions', 'readonly', (st) => wrap(st.get(id)));
const allSessions = () => tx('sessions', 'readonly', (st) => wrap(st.getAll()));
const delSession = (id) => tx('sessions', 'readwrite', (st) => st.delete(id));
const pushChunk = (item) => tx('queue', 'readwrite', (st) => st.put(item));
const allChunks = () => tx('queue', 'readonly', (st) => wrap(st.getAll()));
const delChunk = (id) => tx('queue', 'readwrite', (st) => st.delete(id));

/* ---------- UI ---------- */

function log(msg) {
  const el = $('log');
  const t = new Date().toTimeString().slice(0, 8);
  el.textContent = `${t}  ${msg}\n` + el.textContent;
  if (el.textContent.length > 4000) el.textContent = el.textContent.slice(0, 4000);
}

function banner(kind, msg) {
  const el = $('banner');
  if (!msg) { el.className = ''; return; }
  el.className = 'show ' + kind;
  el.textContent = msg;
}

function setHostStatus(state, label) {
  $('hostDot').className = 'dot ' + state;
  $('hostName').textContent = label;
}

async function refreshCounts() {
  const chunks = await allChunks();
  $('pending').textContent = String(chunks.length);
  $('uploaded').textContent = `${uploadedCount} chunks`;
}

let uploadedCount = 0;

/* ---------- host picking ---------- */

let activeHost = '';

async function pickHost() {
  const hosts = settings.hosts;
  // Prefer the host that worked last time, so a phone that has not moved
  // network does not secondary between two laptops between meetings.
  const ordered = [settings.lastHost, ...hosts].filter((h, i, a) => h && a.indexOf(h) === i);

  const probe = (host) => fetch(host.replace(/\/+$/, '') + '/health', {
    method: 'GET', cache: 'no-store', signal: AbortSignal.timeout(4000),
  }).then((r) => (r.ok ? r.json().then((j) => ({ host, info: j })) : Promise.reject()));

  for (const host of ordered) {
    try {
      const { host: h, info } = await probe(host);
      activeHost = h.replace(/\/+$/, '');
      settings.lastHost = activeHost;
      // The host tells us about its siblings, so the second machine's address
      // never has to be typed into the phone by hand.
      if (Array.isArray(info.hosts) && info.hosts.length) {
        const merged = [...settings.hosts];
        let added = 0;
        for (const peer of info.hosts) {
          const clean = String(peer).replace(/\/+$/, '');
          if (clean && !merged.includes(clean)) { merged.push(clean); added += 1; }
        }
        if (added) {
          settings.hosts = merged;
          $('hosts').value = merged.join('\n');
          log(`learned ${added} more host(s) from ${new URL(activeHost).hostname}`);
        }
      }
      setHostStatus('ok', `${info.host || 'ok'} · ${new URL(activeHost).hostname}`);
      return activeHost;
    } catch (_) { /* try the next one */ }
  }
  activeHost = '';
  setHostStatus('bad', 'unreachable');
  return '';
}

/* ---------- upload queue ---------- */

let draining = false;

function authHeaders(extra) {
  return Object.assign({ 'X-ASB-Token': settings.token }, extra || {});
}

async function openRemoteSession(sess) {
  const res = await fetch(activeHost + '/session', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      title: sess.title,
      ad_hoc: sess.ad_hoc,
      attendees: sess.attendees,
      start_epoch: sess.startEpoch / 1000,
    }),
  });
  if (res.status === 401) throw new Error('unauthorised: check the pairing token');
  if (!res.ok) throw new Error(`session failed (${res.status})`);
  const data = await res.json();
  sess.sid = data.sid;
  sess.host = activeHost;
  await putSession(sess);
  log(`session opened on ${new URL(activeHost).hostname}: ${data.sid}`);
  return sess;
}

async function drain() {
  if (draining) return;
  draining = true;
  try {
    if (!settings.token) { banner('warn', 'No pairing token yet. Open Settings and paste it.'); return; }
    if (!activeHost && !(await pickHost())) {
      banner('warn', 'No host reachable. Recording is safe on the phone and will upload when you are back on the network.');
      return;
    }

    const sessions = await allSessions();
    const chunks = (await allChunks()).sort((a, b) => a.id - b.id);

    for (const sess of sessions.sort((a, b) => a.startEpoch - b.startEpoch)) {
      let mine = chunks.filter((c) => c.localId === sess.localId).sort((a, b) => a.seq - b.seq);
      if (!sess.sid) {
        if (!mine.length && !sess.stopped) continue;   // nothing to send yet
        await openRemoteSession(sess);
      }

      for (const c of mine) {
        const url = `${sess.host}/chunk?sid=${encodeURIComponent(sess.sid)}&seq=${c.seq}`;
        const res = await fetch(url, {
          method: 'POST',
          headers: authHeaders({ 'Content-Type': 'application/octet-stream' }),
          body: c.blob,
        });
        if (res.status === 404) {
          // The server forgot this session (restart with no on-disk state).
          // Dropping the chunks is the only honest option; say so out loud.
          log(`session ${sess.sid} gone from host, discarding ${mine.length} chunk(s)`);
          banner('err', 'The laptop lost this recording session. Some audio could not be delivered.');
          for (const x of mine) await delChunk(x.id);
          await delSession(sess.localId);
          break;
        }
        if (!res.ok) throw new Error(`chunk ${c.seq} rejected (${res.status})`);
        await delChunk(c.id);
        uploadedCount += 1;
        await refreshCounts();
      }

      const left = (await allChunks()).filter((c) => c.localId === sess.localId);
      if (sess.stopped && sess.sid && left.length === 0) {
        const res = await fetch(`${sess.host}/finish?sid=${encodeURIComponent(sess.sid)}`, {
          method: 'POST', headers: authHeaders(),
        });
        if (res.ok) {
          const data = await res.json();
          await delSession(sess.localId);
          log(`finished: ${data.file} (${data.duration_sec}s)`);
          banner('ok', `Delivered: ${data.file}. The harness is transcribing it now.`);
        } else if (res.status === 409 || res.status === 404) {
          await delSession(sess.localId);   // already finished, or swept
        } else {
          throw new Error(`finish failed (${res.status})`);
        }
      }
    }
  } catch (err) {
    log('upload paused: ' + err.message);
    if (/unauthorised/.test(err.message)) banner('err', err.message);
    activeHost = '';
    setHostStatus('warn', 'retrying…');
  } finally {
    draining = false;
    await refreshCounts();
  }
}

/* ---------- recording ---------- */

let recorder = null;
let stream = null;
let wakeLock = null;
let current = null;
let seq = 0;
let startedAt = 0;
let timerId = null;

function pickMime() {
  // Chrome on Android gives WebM/Opus, whose timesliced chunks concatenate into
  // one valid stream. MP4 chunks do NOT concatenate, so on a browser that only
  // offers MP4 we fall back to a single blob at stop (no streaming safety net).
  for (const m of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']) {
    if (MediaRecorder.isTypeSupported(m)) return { mime: m, streaming: true };
  }
  if (MediaRecorder.isTypeSupported('audio/mp4')) return { mime: 'audio/mp4', streaming: false };
  return { mime: '', streaming: false };
}

async function keepAwake() {
  try {
    if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen');
  } catch (_) { /* denied or unsupported; recording still runs */ }
}

function releaseAwake() {
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && recorder && !wakeLock) keepAwake();
});

async function startRecording() {
  const { mime, streaming } = pickMime();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: true },
    });
  } catch (err) {
    banner('err', 'Microphone blocked. This page must be opened over HTTPS, and the '
      + 'microphone permission must be granted.');
    log('getUserMedia: ' + err.name);
    return;
  }

  startedAt = Date.now();
  seq = 0;
  current = {
    localId: 'r' + startedAt + '-' + Math.random().toString(36).slice(2, 8),
    sid: null, host: null, stopped: false,
    title: $('title').value.trim() || 'phone meeting',
    ad_hoc: $('adhoc').checked,
    attendees: $('attendees').value.split(',').map((s) => s.trim()).filter(Boolean),
    startEpoch: startedAt,
  };
  await putSession(current);

  recorder = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : {});
  recorder.ondataavailable = async (e) => {
    if (!e.data || !e.data.size) return;
    await pushChunk({ localId: current.localId, seq: seq++, blob: e.data });
    await refreshCounts();
    drain();
  };
  recorder.onerror = (e) => log('recorder error: ' + (e.error && e.error.name));
  recorder.start(streaming ? CHUNK_MS : undefined);

  keepAwake();
  banner(streaming ? '' : 'warn',
    streaming ? '' : 'This browser cannot stream in pieces, so the recording only '
      + 'uploads when you press stop. Keep the app open.');

  $('record').textContent = 'Stop and send';
  $('record').classList.add('recording');
  $('timer').classList.remove('idle');
  timerId = setInterval(tick, 500);
  tick();
  log(`recording started (${mime || 'browser default'})`);
  drain();     // open the remote session straight away when we are online
}

function tick() {
  const s = Math.floor((Date.now() - startedAt) / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  $('timer').textContent = `${mm}:${ss}`;
}

async function stopRecording() {
  const rec = recorder;
  recorder = null;
  clearInterval(timerId);
  releaseAwake();

  await new Promise((resolve) => {
    rec.onstop = resolve;
    try { rec.stop(); } catch (_) { resolve(); }
  });
  // onstop fires after the last ondataavailable, but that handler is async;
  // give its IndexedDB write a moment to land before the queue is inspected.
  await new Promise((r) => setTimeout(r, 250));

  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }

  current.stopped = true;
  await putSession(current);
  current = null;

  $('record').textContent = 'Start recording';
  $('record').classList.remove('recording');
  $('timer').classList.add('idle');
  $('timer').textContent = '00:00';
  log('recording stopped, sending the rest');
  banner('warn', 'Sending the last pieces…');
  await drain();
}

/* ---------- wiring ---------- */

$('record').addEventListener('click', () => {
  if (recorder) stopRecording(); else startRecording();
});

$('save').addEventListener('click', async () => {
  settings.token = $('token').value;
  settings.hosts = $('hosts').value.split('\n').map((s) => s.trim()).filter(Boolean);
  banner('ok', 'Saved.');
  await pickHost();
  drain();
});

$('recheck').addEventListener('click', async () => { await pickHost(); drain(); });
$('drain').addEventListener('click', () => drain());
window.addEventListener('online', () => { pickHost().then(drain); });

async function boot() {
  $('token').value = settings.token;
  $('hosts').value = settings.hosts.join('\n');
  await refreshCounts();

  const host = await pickHost();
  $('record').disabled = false;
  $('record').textContent = 'Start recording';

  if (!settings.token) {
    banner('warn', 'Not paired yet. Open Settings and paste the pairing token from the laptop.');
  } else if (!host) {
    banner('warn', 'No host reachable right now. You can still record; it uploads when a host comes back.');
  }

  const pending = (await allChunks()).length;
  if (pending) { log(`${pending} chunk(s) waiting from a previous recording`); drain(); }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
}

boot();
