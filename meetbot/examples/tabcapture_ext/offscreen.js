// Consumes the tabCapture stream and measures it.
//
// Deliberately measures the SAME thing capture.js measures -- peak amplitude --
// and reports the track label, because the label is what exposed Chrome's
// synthetic device on the getDisplayMedia path ("Fake audio"). A peak alone
// cannot tell real tab audio from a test tone.

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== 'capture') {
    return;
  }
  (async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          mandatory: {
            chromeMediaSource: 'tab',
            chromeMediaSourceId: msg.streamId
          }
        }
      });
      const track = stream.getAudioTracks()[0];
      const label = track ? track.label : null;

      const ctx = new AudioContext({ sampleRate: 16000 });
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const sink = ctx.createGain();
      sink.gain.value = 0;

      let peak = 0;
      let blocks = 0;
      let loud = 0;
      proc.onaudioprocess = (ev) => {
        const d = ev.inputBuffer.getChannelData(0);
        let p = 0;
        for (let i = 0; i < d.length; i++) {
          const a = d[i] < 0 ? -d[i] : d[i];
          if (a > p) { p = a; }
        }
        if (p > peak) { peak = p; }
        blocks += 1;
        if (p > 0.0005) { loud += 1; }
      };

      src.connect(proc);
      proc.connect(sink);
      sink.connect(ctx.destination);
      try { await ctx.resume(); } catch (e) { /* autoplay flag should cover it */ }

      // Long enough to be past the connect transient. The getDisplayMedia probe
      // was fooled by reading the first window after attach.
      setTimeout(() => {
        sendResponse({
          ok: true,
          peak,
          loudRatio: blocks > 0 ? loud / blocks : 0,
          blocks,
          label,
          ctxState: ctx.state
        });
      }, 3000);
    } catch (e) {
      sendResponse({
        ok: false,
        error: (e && e.name ? e.name + ': ' : '') + (e && e.message ? e.message : String(e))
      });
    }
  })();
  return true;
});
