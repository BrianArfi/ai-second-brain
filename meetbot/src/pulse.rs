//! PulseAudio loopback capture: record the sink Chrome plays into.
//!
//! # Why this exists
//!
//! Every in-browser route to the meeting audio is dead on this host, and each
//! died for a different reason (all measured 6 Aug 2026, all reproducible with
//! `cargo run --example tabtap` / `--example extcapture`):
//!
//! * the DOM tap in `assets/capture.js` finds media elements that carry no
//!   audio, because Meet stopped routing remote participants through them;
//! * `getDisplayMedia` returns Chrome's synthetic 440 Hz test tone, because
//!   `--use-fake-device-for-media-stream` fakes display-capture audio too, and
//!   without that flag it fails with `NotReadableError`;
//! * `chrome.tabCapture` needs an extension "invocation" that headless has no
//!   reliable way to deliver, and fails with `NotFoundError` even when invoked.
//!
//! The common wall is Chrome's audio service having no capture device. This
//! module does not ask it for one. Chrome plays the meeting out of its normal
//! audio OUTPUT, into a PulseAudio sink, and we read that sink's monitor from a
//! separate process entirely. No DOM, no CDP, no getUserMedia, and nothing that
//! a Meet or Chrome release can reshuffle.
//!
//! # Binding without headers
//!
//! There is no `libpulse-dev` on this box and no passwordless sudo to install
//! it, so this `dlopen`s `libpulse-simple.so.0` at runtime rather than linking
//! against it. That is also the better failure mode: a host with no PulseAudio
//! gets a clear error from [`PulseRecorder::start`] instead of a binary that
//! will not link.
//!
//! # The witness
//!
//! [`PulseStats::peak`] is the same signal-level witness `capture.js` exposes,
//! for the same reason: sample throughput advances identically whether the
//! recorder is carrying a room full of people or carrying digital silence, so
//! only amplitude can tell the two apart. `watch_call` reads it, and it is what
//! makes a deaf call fail loudly instead of finishing green.

use std::ffi::{CString, c_char, c_int, c_void};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};

use anyhow::{Result, anyhow, bail};
use tokio::sync::mpsc;

use crate::audio::{AudioFrame, SAMPLE_RATE};

/// `PA_STREAM_RECORD`.
const PA_STREAM_RECORD: c_int = 2;
/// `PA_SAMPLE_S16LE`.
const PA_SAMPLE_S16LE: c_int = 3;

/// Samples per frame handed to the segmenter. 3200 @ 16 kHz = 200 ms, which
/// matches the order of the CDP tap's frames so downstream timing behaves the
/// same on either transport.
const FRAME_SAMPLES: usize = 3200;

/// Peak at or below this counts as silence. Matches `SILENCE_FLOOR` in
/// `meet.rs` and `assets/capture.js`; all three must agree or the watchdog and
/// the logs disagree about what "deaf" means.
const SILENCE_FLOOR: f32 = 0.0005;

#[repr(C)]
struct SampleSpec {
    format: c_int,
    rate: u32,
    channels: u8,
}

unsafe extern "C" {
    fn dlopen(filename: *const c_char, flag: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlerror() -> *const c_char;
}

const RTLD_NOW: c_int = 2;

type PaSimpleNew = unsafe extern "C" fn(
    server: *const c_char,
    name: *const c_char,
    dir: c_int,
    dev: *const c_char,
    stream_name: *const c_char,
    ss: *const SampleSpec,
    map: *const c_void,
    attr: *const c_void,
    error: *mut c_int,
) -> *mut c_void;
type PaSimpleRead =
    unsafe extern "C" fn(s: *mut c_void, data: *mut c_void, bytes: usize, error: *mut c_int) -> c_int;
type PaSimpleFree = unsafe extern "C" fn(s: *mut c_void);

struct Lib {
    new: PaSimpleNew,
    read: PaSimpleRead,
    free: PaSimpleFree,
}

// The function pointers are immutable for the process lifetime.
unsafe impl Send for Lib {}
unsafe impl Sync for Lib {}

/// A `pa_simple*`, moved to the capture thread once and touched only there.
///
/// The raw pointer is not `Send`, and it must not become casually sendable:
/// `pa_simple` is not thread-safe, so exactly one thread may ever hold this.
/// The move into the capture thread is that one transfer.
struct StreamHandle(*mut c_void);
unsafe impl Send for StreamHandle {}

impl StreamHandle {
    /// Reached through a method, not by touching `.0` from the closure.
    ///
    /// Rust 2021 captures disjoint FIELDS, so `move || handle.0` would capture
    /// the bare `*mut c_void` -- which is not `Send` -- and the wrapper would
    /// silently buy nothing. Calling a method captures the whole struct.
    fn get(&self) -> *mut c_void {
        self.0
    }
}

fn load_lib() -> Result<Lib> {
    let name = CString::new("libpulse-simple.so.0").unwrap();
    let handle = unsafe { dlopen(name.as_ptr(), RTLD_NOW) };
    if handle.is_null() {
        let err = unsafe { dlerror() };
        let msg = if err.is_null() {
            "unknown dlopen error".to_string()
        } else {
            unsafe { std::ffi::CStr::from_ptr(err) }
                .to_string_lossy()
                .into_owned()
        };
        bail!(
            "could not load libpulse-simple.so.0 ({msg}). PulseAudio capture needs the \
             PulseAudio client library present; install libpulse0 or switch \
             capture_transport off `pulse`."
        );
    }
    let sym = |s: &str| -> Result<*mut c_void> {
        let c = CString::new(s).unwrap();
        let p = unsafe { dlsym(handle, c.as_ptr()) };
        if p.is_null() {
            bail!("libpulse-simple.so.0 has no symbol `{s}`");
        }
        Ok(p)
    };
    Ok(Lib {
        new: unsafe { std::mem::transmute::<*mut c_void, PaSimpleNew>(sym("pa_simple_new")?) },
        read: unsafe { std::mem::transmute::<*mut c_void, PaSimpleRead>(sym("pa_simple_read")?) },
        free: unsafe { std::mem::transmute::<*mut c_void, PaSimpleFree>(sym("pa_simple_free")?) },
    })
}

/// Live counters for the capture thread, read by `watch_call`.
#[derive(Debug, Default)]
pub struct PulseStats {
    /// Loudest sample since the last [`PulseStats::take_peak`], as f32 bits.
    peak_window: AtomicU32,
    /// 200 ms blocks read since the last take.
    blocks: AtomicU64,
    /// ...of which carried anything above [`SILENCE_FLOOR`].
    loud_blocks: AtomicU64,
    /// Frames handed to the segmenter.
    pub frames: AtomicU64,
    /// Set when the capture thread has stopped for any reason.
    pub stopped: AtomicBool,
    /// Set once the stream has been opened successfully.
    pub live: AtomicBool,
}

impl PulseStats {
    fn observe(&self, peak: f32) {
        // Compare-and-swap on the f32 bits: several relaxed writers would
        // otherwise race and the loudest sample is the one that must survive.
        let bits = peak.to_bits();
        let mut cur = self.peak_window.load(Ordering::Relaxed);
        while f32::from_bits(bits) > f32::from_bits(cur) {
            match self.peak_window.compare_exchange_weak(
                cur,
                bits,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(actual) => cur = actual,
            }
        }
        self.blocks.fetch_add(1, Ordering::Relaxed);
        if peak > SILENCE_FLOOR {
            self.loud_blocks.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// Peak and loud-ratio since the previous call, resetting the window.
    ///
    /// Destructive on purpose, exactly like `__meetbotCaptureStats()`: each
    /// caller gets the interval since the last read, which is what a periodic
    /// poller wants. A cumulative peak would latch on the first cough and never
    /// report a bot that went deaf mid-call.
    pub fn take_peak(&self) -> (f32, f64) {
        let peak = f32::from_bits(self.peak_window.swap(0, Ordering::Relaxed));
        let blocks = self.blocks.swap(0, Ordering::Relaxed);
        let loud = self.loud_blocks.swap(0, Ordering::Relaxed);
        let ratio = if blocks > 0 {
            loud as f64 / blocks as f64
        } else {
            0.0
        };
        (peak, ratio)
    }
}

/// A running capture thread. Dropping it stops the capture.
pub struct PulseRecorder {
    pub stats: Arc<PulseStats>,
    stop: Arc<AtomicBool>,
    source: String,
}

impl PulseRecorder {
    pub fn source(&self) -> &str {
        &self.source
    }

    /// Opens `source` and starts pushing [`AudioFrame`]s into `frames`.
    ///
    /// `speaker` is shared with the page poller in `session.rs`: the DOM is
    /// still the only place a participant's NAME exists, so the audio comes
    /// from Pulse and the label comes from Meet's speaking indicator. A stale
    /// or absent label degrades the transcript to "Unknown" and is never fatal.
    pub fn start(
        source: &str,
        frames: mpsc::Sender<AudioFrame>,
        speaker: Arc<std::sync::Mutex<Option<String>>>,
    ) -> Result<PulseRecorder> {
        let lib = load_lib()?;
        let spec = SampleSpec {
            format: PA_SAMPLE_S16LE,
            rate: SAMPLE_RATE,
            channels: 1,
        };
        let app = CString::new("meetbot").unwrap();
        let stream = CString::new("meeting capture").unwrap();
        let dev = CString::new(source).map_err(|_| anyhow!("source name has an interior NUL"))?;
        let mut err: c_int = 0;

        // Opened on THIS thread so a bad source name fails the caller rather
        // than dying silently inside a detached thread.
        let handle = unsafe {
            (lib.new)(
                std::ptr::null(),
                app.as_ptr(),
                PA_STREAM_RECORD,
                dev.as_ptr(),
                stream.as_ptr(),
                &spec,
                std::ptr::null(),
                std::ptr::null(),
                &mut err,
            )
        };
        if handle.is_null() {
            bail!(
                "could not open PulseAudio source `{source}` (error {err}). Check that a \
                 Pulse server is reachable (PULSE_SERVER) and that the source exists; \
                 `python3 tools/pulse_probe.py` lists them."
            );
        }

        let handle = StreamHandle(handle);

        let stats = Arc::new(PulseStats::default());
        stats.live.store(true, Ordering::Relaxed);
        let stop = Arc::new(AtomicBool::new(false));

        let thread_stats = Arc::clone(&stats);
        let thread_stop = Arc::clone(&stop);
        let source_owned = source.to_string();

        // A dedicated OS thread, not a tokio task: pa_simple_read blocks for a
        // whole frame and would otherwise park a runtime worker every 200 ms.
        std::thread::Builder::new()
            .name("pulse-capture".into())
            .spawn(move || {
                let handle = handle.get();
                let mut buf = vec![0i16; FRAME_SAMPLES];
                let mut sent: u64 = 0;
                loop {
                    if thread_stop.load(Ordering::Relaxed) {
                        break;
                    }
                    let mut err: c_int = 0;
                    let rc = unsafe {
                        (lib.read)(
                            handle,
                            buf.as_mut_ptr() as *mut c_void,
                            std::mem::size_of_val(&buf[..]),
                            &mut err,
                        )
                    };
                    if rc < 0 {
                        tracing::error!(
                            source = %source_owned,
                            error = err,
                            "PulseAudio read failed; capture thread is stopping"
                        );
                        break;
                    }

                    let mut peak = 0f32;
                    for s in &buf {
                        let a = (*s as f32 / 32768.0).abs();
                        if a > peak {
                            peak = a;
                        }
                    }
                    thread_stats.observe(peak);

                    let frame = AudioFrame {
                        pcm: buf.clone(),
                        offset_sec: sent as f64 / SAMPLE_RATE as f64,
                        speaker: speaker.lock().ok().and_then(|g| g.clone()),
                    };
                    sent += FRAME_SAMPLES as u64;
                    thread_stats.frames.fetch_add(1, Ordering::Relaxed);

                    // `blocking_send` would deadlock a full channel against a
                    // stopped consumer; dropping is the same choice the CDP
                    // pump makes when the segmenter falls behind.
                    if frames.try_send(frame).is_err() && frames.is_closed() {
                        break;
                    }
                }
                unsafe { (lib.free)(handle) };
                thread_stats.live.store(false, Ordering::Relaxed);
                thread_stats.stopped.store(true, Ordering::Relaxed);
                tracing::info!(frames = sent / FRAME_SAMPLES as u64, "pulse capture finished");
            })
            .map_err(|e| anyhow!("could not spawn the pulse capture thread: {e}"))?;

        Ok(PulseRecorder {
            stats,
            stop,
            source: source.to_string(),
        })
    }
}

impl Drop for PulseRecorder {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn peak_window_keeps_the_loudest_and_resets_on_read() {
        let s = PulseStats::default();
        s.observe(0.1);
        s.observe(0.9);
        s.observe(0.3);
        let (peak, ratio) = s.take_peak();
        assert!((peak - 0.9).abs() < 1e-6, "peak was {peak}");
        assert!((ratio - 1.0).abs() < 1e-6, "ratio was {ratio}");
        // Destructive read: the next window starts empty, so a bot that goes
        // deaf mid-call reports 0.0 rather than latching on an earlier peak.
        let (peak, ratio) = s.take_peak();
        assert_eq!(peak, 0.0);
        assert_eq!(ratio, 0.0);
    }

    #[test]
    fn silence_does_not_count_as_a_loud_block() {
        let s = PulseStats::default();
        s.observe(0.0);
        s.observe(SILENCE_FLOOR);
        let (peak, ratio) = s.take_peak();
        assert_eq!(ratio, 0.0, "a block at exactly the floor is silence");
        assert!(peak <= SILENCE_FLOOR);
    }

    /// The floors must agree across the three places that define one, or the
    /// watchdog and the logs disagree about what "deaf" means.
    #[test]
    fn silence_floor_matches_the_rest_of_the_codebase() {
        assert_eq!(SILENCE_FLOOR, crate::meet::SILENCE_FLOOR as f32);
        let js = include_str!("../assets/capture.js");
        assert!(
            js.contains("var SILENCE_FLOOR = 0.0005"),
            "capture.js changed its silence floor without this one following"
        );
    }
}

// ---------------------------------------------------------------------------
// Null sink
// ---------------------------------------------------------------------------

/// Everything below exists because recording `RDPSink.monitor` took WSLg's
/// audio server down on 6 Aug 2026.
///
/// `RDPSink` is WSLg's bridge to Windows audio. It is not built to absorb a bot
/// playing meetings into it all day: with nothing draining the RDP side its
/// async queue overran (`[rdp-sink] asyncq.c: q overrun, queuing locally`,
/// 2459 events and climbing), and the daemon degraded until it refused new
/// connections outright. Sessions then failed at capture start with
/// `pa_simple_new` error 8, PA_ERR_TIMEOUT.
///
/// A null sink has no such bridge. It drains at a steady clock and discards the
/// samples, and its monitor carries exactly what was played into it. Chrome is
/// pointed at it with `PULSE_SINK`, so the meeting never touches `RDPSink` and
/// the failure cannot recur.
///
/// The module stays loaded until the Pulse daemon restarts, so this is
/// idempotent by design: it checks first and only loads when the sink is
/// missing.

type PaMainloopNew = unsafe extern "C" fn() -> *mut c_void;
type PaMainloopGetApi = unsafe extern "C" fn(*mut c_void) -> *mut c_void;
type PaMainloopIterate = unsafe extern "C" fn(*mut c_void, c_int, *mut c_int) -> c_int;
type PaMainloopFree = unsafe extern "C" fn(*mut c_void);
type PaContextNew = unsafe extern "C" fn(*mut c_void, *const c_char) -> *mut c_void;
type PaContextConnect =
    unsafe extern "C" fn(*mut c_void, *const c_char, c_int, *const c_void) -> c_int;
type PaContextGetState = unsafe extern "C" fn(*mut c_void) -> c_int;
type PaContextDisconnect = unsafe extern "C" fn(*mut c_void);
type PaContextUnref = unsafe extern "C" fn(*mut c_void);
type PaOperationUnref = unsafe extern "C" fn(*mut c_void);
type PaSinkInfoCb = unsafe extern "C" fn(*mut c_void, *const c_void, c_int, *mut c_void);
type PaIndexCb = unsafe extern "C" fn(*mut c_void, u32, *mut c_void);
type PaGetSinkInfoByName =
    unsafe extern "C" fn(*mut c_void, *const c_char, PaSinkInfoCb, *mut c_void) -> *mut c_void;
type PaLoadModule = unsafe extern "C" fn(
    *mut c_void,
    *const c_char,
    *const c_char,
    PaIndexCb,
    *mut c_void,
) -> *mut c_void;

const PA_CONTEXT_READY: c_int = 4;
const PA_CONTEXT_FAILED: c_int = 5;
const PA_CONTEXT_TERMINATED: c_int = 6;
/// `PA_INVALID_INDEX`, what `load_module` reports on failure.
const PA_INVALID_INDEX: u32 = u32::MAX;

/// Shared by both callbacks: `done` is the only way the mainloop knows to stop.
#[derive(Default)]
struct CbState {
    done: bool,
    found: bool,
    index: u32,
}

unsafe extern "C" fn on_sink_info(_c: *mut c_void, info: *const c_void, eol: c_int, user: *mut c_void) {
    let st = unsafe { &mut *(user as *mut CbState) };
    if !info.is_null() {
        st.found = true;
    }
    if eol != 0 {
        st.done = true;
    }
}

unsafe extern "C" fn on_module_loaded(_c: *mut c_void, idx: u32, user: *mut c_void) {
    let st = unsafe { &mut *(user as *mut CbState) };
    st.index = idx;
    st.done = true;
}

/// Loads `module-null-sink` named `name` unless it is already there.
///
/// Returns `true` when it had to create it. Errors are actionable rather than
/// fatal to the caller's judgement: a host whose Pulse server refuses module
/// loading can still run against an existing sink.
pub fn ensure_null_sink(name: &str) -> Result<bool> {
    let lib = CString::new("libpulse.so.0").unwrap();
    let h = unsafe { dlopen(lib.as_ptr(), RTLD_NOW) };
    if h.is_null() {
        bail!("could not load libpulse.so.0");
    }
    macro_rules! sym {
        ($t:ty, $n:literal) => {{
            let c = CString::new($n).unwrap();
            let p = unsafe { dlsym(h, c.as_ptr()) };
            if p.is_null() {
                bail!("libpulse.so.0 has no symbol `{}`", $n);
            }
            unsafe { std::mem::transmute::<*mut c_void, $t>(p) }
        }};
    }
    let mainloop_new = sym!(PaMainloopNew, "pa_mainloop_new");
    let get_api = sym!(PaMainloopGetApi, "pa_mainloop_get_api");
    let iterate = sym!(PaMainloopIterate, "pa_mainloop_iterate");
    let mainloop_free = sym!(PaMainloopFree, "pa_mainloop_free");
    let context_new = sym!(PaContextNew, "pa_context_new");
    let connect = sym!(PaContextConnect, "pa_context_connect");
    let get_state = sym!(PaContextGetState, "pa_context_get_state");
    let disconnect = sym!(PaContextDisconnect, "pa_context_disconnect");
    let context_unref = sym!(PaContextUnref, "pa_context_unref");
    let op_unref = sym!(PaOperationUnref, "pa_operation_unref");
    let sink_by_name = sym!(PaGetSinkInfoByName, "pa_context_get_sink_info_by_name");
    let load_module = sym!(PaLoadModule, "pa_context_load_module");

    let app = CString::new("meetbot").unwrap();
    let loop_ptr = unsafe { mainloop_new() };
    let api = unsafe { get_api(loop_ptr) };
    let ctx = unsafe { context_new(api, app.as_ptr()) };

    // Every early return from here on has to tear these down.
    let cleanup = |ctx: *mut c_void, loop_ptr: *mut c_void| unsafe {
        disconnect(ctx);
        context_unref(ctx);
        mainloop_free(loop_ptr);
    };

    if unsafe { connect(ctx, std::ptr::null(), 0, std::ptr::null()) } < 0 {
        cleanup(ctx, loop_ptr);
        bail!("pa_context_connect failed; is PULSE_SERVER set and the server up?");
    }
    let mut ret: c_int = 0;
    let mut ready = false;
    for _ in 0..2000 {
        let st = unsafe { get_state(ctx) };
        if st == PA_CONTEXT_READY {
            ready = true;
            break;
        }
        if st == PA_CONTEXT_FAILED || st == PA_CONTEXT_TERMINATED {
            break;
        }
        unsafe { iterate(loop_ptr, 1, &mut ret) };
    }
    if !ready {
        cleanup(ctx, loop_ptr);
        bail!("no reachable PulseAudio server (context never became ready)");
    }

    let sink_name = CString::new(name).map_err(|_| anyhow!("sink name has an interior NUL"))?;
    let mut probe = CbState::default();
    let op = unsafe {
        sink_by_name(
            ctx,
            sink_name.as_ptr(),
            on_sink_info,
            &mut probe as *mut _ as *mut c_void,
        )
    };
    for _ in 0..2000 {
        if probe.done {
            break;
        }
        unsafe { iterate(loop_ptr, 1, &mut ret) };
    }
    if !op.is_null() {
        unsafe { op_unref(op) };
    }
    if probe.found {
        cleanup(ctx, loop_ptr);
        return Ok(false);
    }

    // `channels=1` because that is what the recorder reads and what whisper
    // wants; letting Pulse pick stereo just makes it downmix twice.
    let module = CString::new("module-null-sink").unwrap();
    let args = CString::new(format!(
        "sink_name={name} rate=16000 channels=1 \
         sink_properties=device.description=meetbot-capture"
    ))
    .unwrap();
    let mut load = CbState::default();
    let op = unsafe {
        load_module(
            ctx,
            module.as_ptr(),
            args.as_ptr(),
            on_module_loaded,
            &mut load as *mut _ as *mut c_void,
        )
    };
    for _ in 0..2000 {
        if load.done {
            break;
        }
        unsafe { iterate(loop_ptr, 1, &mut ret) };
    }
    if !op.is_null() {
        unsafe { op_unref(op) };
    }
    cleanup(ctx, loop_ptr);

    if !load.done || load.index == PA_INVALID_INDEX {
        bail!(
            "the Pulse server refused to load module-null-sink `{name}`. Without it Chrome \
             would play into the shared RDPSink, which is what took WSLg's audio server \
             down on 6 Aug 2026."
        );
    }
    Ok(true)
}
