// meetbot in-page audio tap.
//
// Injected by src/meet.rs once the bot is admitted to the call. It mixes every
// remote MediaStream the page is playing into one WebAudio graph, downsamples
// to the canonical 16 kHz mono, and ships base64 i16-LE frames back to Rust
// through the CDP binding created with `Runtime.addBinding`.
//
// The double-underscore MEETBOT placeholder tokens below are substituted by
// meet.rs before evaluation, so every DOM selector in here still comes from the
// single selector table at the top of src/meet.rs — never hardcode a selector
// in this file.
//
// Returns an object (never a bare `null`: CDP drops null return values and
// `EvaluationResult::into_value` would then fail):
//   { status: "started" | "already-running" | "no-binding" | "no-audiocontext" }
(() => {
  'use strict';

  var BINDING = __MEETBOT_BINDING__;
  var FRAME_SAMPLES = __MEETBOT_FRAME_SAMPLES__;
  var TARGET_RATE = __MEETBOT_TARGET_RATE__;
  var SPEAKING_SELECTORS = __MEETBOT_SPEAKING_SELECTORS__;
  var SPEAKER_NAME_SELECTORS = __MEETBOT_SPEAKER_NAME_SELECTORS__;
  var SPEAKER_TILE_SELECTORS = __MEETBOT_SPEAKER_TILE_SELECTORS__;
  var RESCAN_MS = 1000;
  // Retry budget for waking a suspended AudioContext. 15 x 1s covers the whole
  // AUDIO_CLOCK_TIMEOUT window Rust gates on.
  var RESUME_RETRY_MS = 1000;
  var RESUME_ATTEMPTS = 15;
  // Amplitude below this counts as silence. Meet's mix sits at exactly 0.0 when
  // nobody is audible, so this only has to clear dither and float noise.
  var SILENCE_FLOOR = 0.0005;
  // Full shadow/iframe walk every Nth rescan. RESCAN_MS is 1s, so this is
  // every 5s: often enough to catch a participant joining, rare enough that
  // querySelectorAll('*') over Meet's DOM does not become the bottleneck.
  var DEEP_SCAN_EVERY = 5;

  if (window.__meetbotCapture) {
    return { status: 'already-running' };
  }

  var send = window[BINDING];
  if (typeof send !== 'function') {
    return { status: 'no-binding' };
  }

  var Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) {
    return { status: 'no-audiocontext' };
  }

  // Asking for a 16 kHz context makes Chrome resample every source for us, so
  // the samples that reach `onaudioprocess` are already at the rate whisper
  // wants. Rust still re-checks `rate` on every frame and resamples if the
  // browser ignored the hint.
  var ctx = new Ctor({ sampleRate: TARGET_RATE });

  var mixer = ctx.createGain();
  mixer.gain.value = 1;

  // ScriptProcessorNode is deprecated but is the only tap that works without
  // shipping a separate AudioWorklet module file, and it is stable in headless
  // Chrome. 4096 frames ≈ 256 ms at 16 kHz.
  var proc = ctx.createScriptProcessor(4096, 1, 1);

  // A muted terminal sink: the graph has to reach `ctx.destination` for the
  // processor to be pulled, but the bot must never play the meeting back out.
  var sink = ctx.createGain();
  sink.gain.value = 0;

  mixer.connect(proc);
  proc.connect(sink);
  sink.connect(ctx.destination);

  var state = {
    seq: 0,
    sentSamples: 0,
    buf: new Float32Array(FRAME_SAMPLES),
    used: 0,
    attached: Object.create(null),
    attachedCount: 0,
    dropped: 0,
    stopped: false,
    timer: null,
    // Signal-level witnesses. `peakWindow` is the loudest sample since the last
    // stats read and is reset by the reader, so each poll reports that interval
    // rather than the whole call. `loudBlocks` / `blocks` gives the share of
    // 256 ms blocks that carried anything above the noise floor.
    peakWindow: 0,
    blocks: 0,
    loudBlocks: 0,
    // Cumulative count of tracks ever wired in. `attachedCount` goes down when
    // a track ends, so it alone cannot show churn. If this climbs while peak
    // stays at 0.0, the elements are real but carry no audio.
    attachedEver: 0,
    // Where media elements were found on the last deep walk. If shadow and
    // frame both stay 0 while peak stays 0.0, Meet is not using media elements
    // for remote audio and the DOM is the wrong place to look entirely.
    elemsTop: 0,
    elemsShadow: 0,
    elemsFrame: 0,
    scanTick: 0,
    // Which source is feeding the mixer. 'tab' once getDisplayMedia has handed
    // us the tab's own output; 'dom' while we are still reconstructing the mix
    // from media elements. See startTabCapture().
    mode: 'dom',
    // Lifecycle of the tab-capture attempt, for the Rust-side log line:
    // 'pending' -> 'ok' | 'unsupported' | 'err:<DOMException name>' | 'no-audio'.
    tabState: 'pending',
    tabTracks: 0
  };
  window.__meetbotCapture = state;

  var B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

  function base64(bytes) {
    var out = '';
    var i = 0;
    var n = bytes.length;
    while (i + 2 < n) {
      var a = bytes[i], b = bytes[i + 1], c = bytes[i + 2];
      out += B64[a >> 2];
      out += B64[((a & 3) << 4) | (b >> 4)];
      out += B64[((b & 15) << 2) | (c >> 6)];
      out += B64[c & 63];
      i += 3;
    }
    var rem = n - i;
    if (rem === 1) {
      var x = bytes[i];
      out += B64[x >> 2];
      out += B64[(x & 3) << 4];
      out += '==';
    } else if (rem === 2) {
      var y = bytes[i], z = bytes[i + 1];
      out += B64[y >> 2];
      out += B64[((y & 3) << 4) | (z >> 4)];
      out += B64[(z & 15) << 2];
      out += '=';
    }
    return out;
  }

  function clean(text) {
    if (!text) {
      return null;
    }
    var t = String(text).replace(/\s+/g, ' ').trim();
    if (!t || t.length > 80) {
      return null;
    }
    return t;
  }

  function nameFor(node) {
    var roots = [node];
    for (var s = 0; s < SPEAKER_TILE_SELECTORS.length; s++) {
      try {
        var tile = node.closest(SPEAKER_TILE_SELECTORS[s]);
        if (tile) {
          roots.push(tile);
        }
      } catch (e) { /* selector unsupported by this Chrome */ }
    }
    if (node.parentElement) {
      roots.push(node.parentElement);
    }
    for (var r = 0; r < roots.length; r++) {
      var root = roots[r];
      for (var i = 0; i < SPEAKER_NAME_SELECTORS.length; i++) {
        var sel = SPEAKER_NAME_SELECTORS[i];
        if (sel === ':self') {
          var own = clean(root.getAttribute && (root.getAttribute('data-participant-name') || root.getAttribute('aria-label')));
          if (own) {
            return own;
          }
          continue;
        }
        try {
          var found = root.querySelector(sel);
          if (found) {
            var got = clean(found.getAttribute('aria-label') || found.textContent);
            if (got) {
              return got;
            }
          }
        } catch (e) { /* selector unsupported */ }
      }
    }
    return null;
  }

  // Best effort only. When Google reshuffles the speaking indicator this
  // returns null and Rust falls back to `speaker: None`, which the transcript
  // renders as "Unknown" — degraded, never fatal.
  function currentSpeaker() {
    for (var i = 0; i < SPEAKING_SELECTORS.length; i++) {
      var nodes;
      try {
        nodes = document.querySelectorAll(SPEAKING_SELECTORS[i]);
      } catch (e) {
        continue;
      }
      for (var j = 0; j < nodes.length; j++) {
        var name = nameFor(nodes[j]);
        if (name) {
          return name;
        }
      }
    }
    return null;
  }

  function flushFrame() {
    var n = state.used;
    if (n <= 0) {
      return;
    }
    var pcm = new Int16Array(n);
    for (var i = 0; i < n; i++) {
      var s = state.buf[i];
      if (s > 1) {
        s = 1;
      } else if (s < -1) {
        s = -1;
      }
      pcm[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
    }
    var offset = state.sentSamples / TARGET_RATE;
    state.sentSamples += n;
    state.used = 0;
    state.seq += 1;
    try {
      send(JSON.stringify({
        seq: state.seq,
        offset: offset,
        rate: TARGET_RATE,
        speaker: currentSpeaker(),
        pcm: base64(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength))
      }));
    } catch (e) {
      // The binding is gone (page torn down mid-call). Stop trying so the
      // audio thread does not spin on exceptions.
      state.dropped += 1;
      if (state.dropped > 10) {
        state.stopped = true;
      }
    }
  }

  proc.onaudioprocess = function (ev) {
    if (state.stopped) {
      return;
    }
    var input = ev.inputBuffer.getChannelData(0);
    // Signal level, not just throughput.
    //
    // Every other counter in this file (seq, sentSamples, ctxTime) advances at
    // exactly the same rate whether the mixer is carrying a room full of people
    // or carrying digital silence, so none of them can tell "recording the
    // meeting" from "recording nothing". Peak amplitude can: silence is 0.0,
    // speech is not. This is the only witness that the bot is actually hearing
    // anyone, so it is worth the one pass over the block.
    var peak = 0;
    for (var j = 0; j < input.length; j++) {
      var a = input[j] < 0 ? -input[j] : input[j];
      if (a > peak) { peak = a; }
    }
    if (peak > state.peakWindow) { state.peakWindow = peak; }
    state.blocks += 1;
    if (peak > SILENCE_FLOOR) { state.loudBlocks += 1; }

    for (var i = 0; i < input.length; i++) {
      state.buf[state.used] = input[i];
      state.used += 1;
      if (state.used >= FRAME_SAMPLES) {
        flushFrame();
      }
    }
  };

  // Google Meet plays each remote participant through its own media element.
  // Elements come and go as people join, leave and get re-tiled, so rescan on
  // an interval and connect any stream we have not already wired in.
  // Where do Meet's media elements actually live?
  //
  // The 5 Aug 2026 outage survived two wrong fixes because nothing ever asked
  // this. A top-level `querySelectorAll('audio, video')` finds 3 elements and
  // they stay silent for the whole call: no track swap, no new track, peak
  // fixed at 0.0 even with 5 people talking. So participant audio is either
  // behind a shadow root, inside an iframe, or not on a media element at all.
  //
  // This walks all three and records WHERE each element was found, so one
  // meeting settles it. The deep walk is throttled because
  // `querySelectorAll('*')` over Meet's DOM is not cheap at 1 Hz.
  function collectMedia() {
    var found = [];
    var roots = [{ node: document, origin: 'top' }];
    var guard = 0;
    state.elemsTop = 0;
    state.elemsShadow = 0;
    state.elemsFrame = 0;

    while (roots.length > 0 && guard < 400) {
      var entry = roots.shift();
      guard += 1;
      var root = entry.node;

      try {
        var media = root.querySelectorAll('audio, video');
        for (var i = 0; i < media.length; i++) {
          found.push(media[i]);
          if (entry.origin === 'top') { state.elemsTop += 1; }
          else if (entry.origin === 'shadow') { state.elemsShadow += 1; }
          else { state.elemsFrame += 1; }
        }
      } catch (e) { /* detached root */ }

      try {
        var all = root.querySelectorAll('*');
        for (var j = 0; j < all.length; j++) {
          if (all[j].shadowRoot) {
            roots.push({ node: all[j].shadowRoot, origin: 'shadow' });
          }
        }
      } catch (e) { /* detached root */ }

      try {
        var frames = root.querySelectorAll('iframe');
        for (var k = 0; k < frames.length; k++) {
          var doc = null;
          try { doc = frames[k].contentDocument; } catch (e) { doc = null; }
          if (doc) { roots.push({ node: doc, origin: 'frame' }); }
        }
      } catch (e) { /* detached root */ }
    }
    return found;
  }

  function attachStreams() {
    // Cheap top-level pass every tick, full walk every DEEP_SCAN_EVERY ticks.
    state.scanTick += 1;
    var els;
    if (state.scanTick % DEEP_SCAN_EVERY === 0 || state.attachedEver === 0) {
      els = collectMedia();
    } else {
      els = document.querySelectorAll('audio, video');
    }
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var ms = el.srcObject;
      if (!ms || typeof ms.getAudioTracks !== 'function') {
        continue;
      }
      var tracks = ms.getAudioTracks();
      if (tracks.length === 0) {
        continue;
      }
      // Key on the TRACK, never on the stream.
      //
      // Meet reuses a small fixed pool of media elements and swaps the audio
      // track inside the SAME MediaStream as people join, leave and get
      // re-tiled. The stream id does not change when that happens.
      //
      // A MediaStreamAudioSourceNode binds the track that was present when it
      // was constructed and does NOT follow the swap. So the old stream-keyed
      // version attached once, at join, to whatever placeholder tracks existed
      // in an empty room, recorded `attached: true` against the stream id, and
      // then skipped that stream forever. Every real participant track that
      // landed on those elements afterwards was never wired to the mixer.
      //
      // That is the 5 Aug 2026 outage exactly: `attached` sat at 3 from the
      // first second and never moved while participants went 1 -> 3 -> 5, and
      // peak amplitude stayed at 0.0 for the whole call. The bot joins several
      // minutes early on purpose, which is what guarantees it attaches to an
      // empty room and never re-checks.
      //
      // One source node per audio track, keyed by track id, so a swap produces
      // a new key and gets wired in on the next rescan.
      for (var t = 0; t < tracks.length; t++) {
        var track = tracks[t];
        var key = track.id || (ms.id + ':' + t);
        if (state.attached[key]) {
          continue;
        }
        try {
          // Wrap the single track: createMediaStreamSource takes only the
          // first audio track of whatever stream it is handed, so a
          // multi-track stream would otherwise lose everything after [0].
          var src = ctx.createMediaStreamSource(new MediaStream([track]));
          src.connect(mixer);
          state.attached[key] = src;
          state.attachedCount += 1;
          state.attachedEver += 1;
          // Drop the node when its track dies, so a long call does not
          // accumulate dead sources hanging off the mixer.
          track.addEventListener('ended', function (node, k) {
            return function () {
              try { node.disconnect(); } catch (e) { /* already gone */ }
              if (state.attached[k]) {
                delete state.attached[k];
                state.attachedCount -= 1;
              }
            };
          }(src, key));
        } catch (e) { /* track already consumed by another context */ }
      }
    }
  }

  // Ask Chrome for the TAB's own audio output.
  //
  // This is the 5 Aug 2026 fix. The DOM tap below reconstructs the mix from
  // whatever media elements the page exposes, which is a bet that Meet routes
  // remote participants through `<audio>`/`<video>` elements the page can see.
  // On 5 Aug that bet stopped paying: a full walk of the top document, every
  // shadow root and every same-origin iframe found exactly 3 elements, all of
  // them silent, through a 5-person call — el_shadow=0, el_frame=0, peak=0.0.
  // No amount of better DOM searching fixes that, because the audio is not on
  // an element any more.
  //
  // getDisplayMedia with `preferCurrentTab` sidesteps the question entirely: it
  // returns whatever the tab is PLAYING, however Meet chose to route it
  // internally. Chrome resolves the picker headlessly because meet.rs launches
  // with `--auto-accept-this-tab-capture`, and meet.rs evaluates this script
  // with `userGesture: true` because getDisplayMedia demands transient
  // activation and would otherwise reject with NotAllowedError.
  //
  // Video is requested only because Chrome rejects an audio-only
  // getDisplayMedia; the video track is stopped the moment it arrives.
  //
  // On success the DOM tap is torn down rather than left running alongside: if
  // Meet ever does put real audio back on those elements, keeping both would
  // mix the same voices into the graph twice.
  function stopDomTap() {
    if (state.timer !== null) {
      clearInterval(state.timer);
      state.timer = null;
    }
    var keys = Object.keys(state.attached);
    for (var i = 0; i < keys.length; i++) {
      try { state.attached[keys[i]].disconnect(); } catch (e) { /* already gone */ }
      delete state.attached[keys[i]];
    }
    state.attachedCount = 0;
  }

  function startTabCapture() {
    var md = navigator.mediaDevices;
    if (!md || typeof md.getDisplayMedia !== 'function') {
      state.tabState = 'unsupported';
      return;
    }
    var p;
    try {
      p = md.getDisplayMedia({
        video: true,
        // Meet's own processing is for humans listening live. Whisper wants the
        // rawest mix available, so turn the cleanup off.
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false
        },
        preferCurrentTab: true,
        selfBrowserSurface: 'include',
        systemAudio: 'exclude'
      });
    } catch (e) {
      state.tabState = 'err:' + (e && e.name ? e.name : 'throw');
      return;
    }
    if (!p || typeof p.then !== 'function') {
      state.tabState = 'err:no-promise';
      return;
    }
    p.then(function (stream) {
      if (state.stopped) {
        try {
          stream.getTracks().forEach(function (t) { t.stop(); });
        } catch (e) { /* already gone */ }
        return;
      }
      // The video track exists only to satisfy Chrome's API shape. Stopping it
      // immediately keeps the encoder off a machine that has no GPU.
      try {
        stream.getVideoTracks().forEach(function (t) { t.stop(); });
      } catch (e) { /* none present */ }

      var tracks = stream.getAudioTracks();

      // REFUSE Chrome's synthetic capture device.
      //
      // `--use-fake-device-for-media-stream` is on the production command line
      // (Meet's green room misbehaves without a camera), and it does not only
      // fake the camera: it substitutes a synthetic 440 Hz beep for
      // getDisplayMedia's audio as well. Measured 6 Aug 2026 with
      // `cargo run --example tabtap`: a page playing NOTHING still reported
      // peak=1.09 and loud_ratio=0.5, identical at every page gain, and the
      // track came back labelled "Fake audio".
      //
      // That is the most dangerous possible failure here. Peak is the one
      // witness that separates "recording the meeting" from "recording
      // nothing", the in-call watchdog gates on it, and a synthetic beep makes
      // it read healthy forever while whisper transcribes a test tone. It would
      // have turned a loud, diagnosable outage into a silent one.
      //
      // So check the label and bail rather than trust the stream. Falling back
      // to the DOM tap is not a fix — that path is deaf to Meet since 5 Aug —
      // but it is honest, and `tabState` says exactly what happened.
      var real = [];
      for (var f = 0; f < tracks.length; f++) {
        if (/fake/i.test(tracks[f].label || '')) {
          continue;
        }
        real.push(tracks[f]);
      }
      if (real.length === 0 && tracks.length > 0) {
        try {
          stream.getTracks().forEach(function (t) { t.stop(); });
        } catch (e) { /* already gone */ }
        state.tabState = 'fake-device';
        return;
      }
      tracks = real;

      if (tracks.length === 0) {
        // The prompt resolved but Chrome handed back no audio. Almost always
        // means the surface was accepted as a screen rather than as this tab,
        // so leave the DOM tap running as the fallback.
        state.tabState = 'no-audio';
        return;
      }
      for (var i = 0; i < tracks.length; i++) {
        try {
          var src = ctx.createMediaStreamSource(new MediaStream([tracks[i]]));
          src.connect(mixer);
          state.attachedEver += 1;
          state.tabTracks += 1;
        } catch (e) { /* track already consumed */ }
      }
      if (state.tabTracks === 0) {
        state.tabState = 'err:wire-failed';
        return;
      }
      stopDomTap();
      state.tabSource = stream;
      state.mode = 'tab';
      state.tabState = 'ok';
    }).catch(function (e) {
      state.tabState = 'err:' + (e && e.name ? e.name : 'reject');
    });
  }

  // Kicked off first, while the evaluation still carries the user gesture
  // getDisplayMedia needs. The DOM tap starts underneath it and keeps running
  // until (and unless) the tab stream actually arrives.
  startTabCapture();

  attachStreams();
  state.timer = setInterval(attachStreams, RESCAN_MS);

  // Kick the context awake, and keep kicking.
  //
  // A single fire-and-forget resume() is not enough. In headless the call can
  // reject outright, or resolve while the context immediately falls back to
  // 'suspended' because the render graph has nothing pulling it yet. Either way
  // the old code never noticed: it did not await the promise and never looked
  // again, so the session died 15s later on the clock gate with
  // state=suspended. That is the 2 Aug and 5 Aug 2026 hard failure.
  //
  // Retry across the whole window Rust waits on (AUDIO_CLOCK_TIMEOUT, 15s) so a
  // context that needs a moment to settle still makes it.
  function pokeContext(attemptsLeft) {
    if (state.stopped || ctx.state === 'running') {
      return;
    }
    if (typeof ctx.resume === 'function') {
      try {
        var p = ctx.resume();
        if (p && typeof p.catch === 'function') {
          p.catch(function () { /* retried below */ });
        }
      } catch (e) { /* retried below */ }
    }
    if (attemptsLeft > 0) {
      setTimeout(function () { pokeContext(attemptsLeft - 1); }, RESUME_RETRY_MS);
    }
  }
  pokeContext(RESUME_ATTEMPTS);

  // Debugging hook. When a call comes back with no audio, evaluate
  // `window.__meetbotCaptureStats()` in the page: `attached` proves the remote
  // streams were found, `seq` proves the processor is actually being pulled,
  // and `ctxState` distinguishes a suspended context from a stalled one.
  window.__meetbotCaptureStats = function () {
    // Reading is destructive for the window counters: each caller gets the
    // interval since the previous read, which is what a periodic poller wants.
    var peak = state.peakWindow;
    var blocks = state.blocks;
    var loud = state.loudBlocks;
    state.peakWindow = 0;
    state.blocks = 0;
    state.loudBlocks = 0;
    return {
      seq: state.seq,
      used: state.used,
      // 'tab' = capturing the tab's own output, 'dom' = still reconstructing
      // from media elements. A call that finishes in 'dom' mode is running on
      // the path that broke on 5 Aug 2026 and should be treated as suspect even
      // if it happened to produce audio.
      mode: state.mode,
      tabState: state.tabState,
      tabTracks: state.tabTracks,
      attached: state.attachedCount,
      attachedEver: state.attachedEver,
      elemsTop: state.elemsTop,
      elemsShadow: state.elemsShadow,
      elemsFrame: state.elemsFrame,
      dropped: state.dropped,
      sentSamples: state.sentSamples,
      // Loudest sample since the previous read. 0.0 means the bot heard
      // literally nothing in that window.
      peak: peak,
      // Share of 256 ms blocks above SILENCE_FLOOR in that window, 0.0 to 1.0.
      loudRatio: blocks > 0 ? loud / blocks : 0,
      ctxState: ctx.state,
      ctxRate: ctx.sampleRate,
      // The audio clock. Rust gates capture start on this ADVANCING between two
      // probes: in headless, a context can report 'running' while the render
      // quantum is never pumped, and currentTime is the only reliable witness
      // that samples are actually flowing. Do not replace with a fixed sleep.
      ctxTime: ctx.currentTime
    };
  };

  window.__meetbotStopCapture = function () {
    state.stopped = true;
    if (state.timer !== null) {
      clearInterval(state.timer);
      state.timer = null;
    }
    if (state.tabSource) {
      try {
        state.tabSource.getTracks().forEach(function (t) { t.stop(); });
      } catch (e) { /* already gone */ }
      state.tabSource = null;
    }
    try { proc.disconnect(); } catch (e) { /* already torn down */ }
    try { mixer.disconnect(); } catch (e) { /* already torn down */ }
    try { ctx.close(); } catch (e) { /* already closed */ }
    return { status: 'stopped' };
  };

  return { status: 'started' };
})()
