//! Lifecycle orchestrator: `SessionSpec`, `spawn`, `run`, `classify_exit`,
//! `classify_admission`. The only module permitted to write a terminal status.
//!
//! Contract: `SPEC.md` §8 (state machine in §2, terminal semantics in §0.1).
//!
//! The single invariant this file exists to protect: **every exit path writes
//! exactly one terminal `MeetingStatus`.** Normal end, browser crash, `Stop`
//! command, panic inside the task, or the task being aborted outright — all of
//! them land on `completed` / `stopped` / `failed`. A row left non-terminal is
//! a row the Python client polls forever.
//!
//! The abort/panic case is covered by [`TerminalGuard`], a drop guard that
//! writes `failed` synchronously (rusqlite is blocking, so this works from
//! `Drop`) if the orchestrator never got to its own terminal write.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use std::net::{Ipv4Addr, SocketAddr};

use chrono::Utc;
use tokio::sync::{RwLock, mpsc, oneshot, watch};
use uuid::Uuid;

use crate::audio::{AudioFrame, VadConfig, run_segmenter};
use crate::db::{Db, MeetingStatus};
use crate::meet::{Admission, BrowserOptions, CallExit, MeetSession, SESSION_ALREADY_PRESENT};
use crate::state::{
    CaptureTransport, MeetingKey, SessionCommand, SessionHandle, SessionPhase, SharedState,
};

/// Frame channel capacity, fixed by `SPEC.md` §6.2.
const FRAME_CHANNEL_CAP: usize = 256;
/// Utterance channel capacity, fixed by `SPEC.md` §6.2.
const UTTERANCE_CHANNEL_CAP: usize = 64;
/// Command channel capacity, fixed by `SPEC.md` §6.2.
const COMMAND_CHANNEL_CAP: usize = 8;
/// Retries per utterance inside `whisper::run_transcriber`.
const WHISPER_MAX_RETRIES: u32 = 3;
/// The client truncates `error` to 300 chars; keep ours comfortably under.
const MAX_ERROR_LEN: usize = 280;

/// Hard ceiling on the **whole** Finalizing drain: relay, segmenter and
/// transcriber share this one wall-clock budget.
///
/// Must stay comfortably below `main::SHUTDOWN_GRACE` (90 s) and the unit's
/// `TimeoutStopSec` (120 s). Overrunning either means systemd SIGKILLs the
/// process mid-finalization, and a killed process writes no terminal status —
/// the whole transcript is then only recoverable through the startup sweep,
/// which marks it `failed`. Abandoning a backlog costs the tail of a meeting;
/// being SIGKILLed costs the meeting. Segments are committed per utterance, so
/// whatever already landed survives the deadline.
///
/// **Why it is shared and not per-stage (v1.3).** The budget originally guarded
/// only the transcriber await, which bounded nothing: the three stages are one
/// backpressured chain. `run_segmenter` blocks on `utterances.send()` once the
/// 64-deep utterance channel is full, and against a whisper that accepts TCP
/// without answering the worker spends `max_retries * DEFAULT_TIMEOUT` (about
/// 8 min) per utterance and needs `HEALTH_PROBE_AFTER_FAILURES` of them before
/// it short-circuits. So the segmenter stayed blocked ~16 min, the relay behind
/// it filled `seg_tx` and blocked too, and both were awaited *unbounded* and
/// *before* the deadline was ever armed. Every stage now runs against one
/// `finalize_deadline`, so total finalization is bounded by construction.
const FINALIZE_BUDGET: Duration = Duration::from_secs(45);

/// Ceiling on leaving the call and tearing the browser down (step 1 of
/// Finalizing), charged on top of [`FINALIZE_BUDGET`].
///
/// `leave()` and `close()` are a sequence of CDP round-trips —
/// `evaluate_expression`, `find_element`, `click`, `Page::close`,
/// `Browser::close` — and chromiumoxide puts no timeout on any of them. The
/// exit that most needs teardown is `CallExit::BrowserError`, i.e. precisely a
/// page that is already misbehaving, so this is the likeliest place to pend
/// forever. `SHUTDOWN_GRACE` (90 s) must still hold with this and
/// `FINALIZE_BUDGET` both spent, hence 20 s.
const BROWSER_TEARDOWN_BUDGET: Duration = Duration::from_secs(20);

/// Loss ratio above which a session that still finished `completed` is logged
/// at error level. Not a status change: with whisper healthy, a partial
/// transcript is a real transcript and the client should still get it.
const LOUD_LOSS_RATIO: f64 = 0.25;

#[derive(Debug, Clone)]
pub struct SessionSpec {
    pub meeting_id: Uuid,
    pub key: MeetingKey,
    pub title: Option<String>,
    pub bot_name: String,
    /// ISO-639-1 or None (auto-detect).
    pub language: Option<String>,
    pub passcode: Option<String>,
    pub recording_enabled: bool,
    pub transcribe_enabled: bool,
}

/// Creates the command channel + phase cell, spawns `run` on the tokio runtime,
/// registers the handle in `state.sessions`, and returns immediately. The
/// concurrency permit is acquired by the CALLER (api.rs) and moved in.
pub fn spawn(
    state: SharedState,
    spec: SessionSpec,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> anyhow::Result<SessionHandle> {
    let (cmd_tx, cmd_rx) = mpsc::channel(COMMAND_CHANNEL_CAP);
    let phase = Arc::new(RwLock::new(SessionPhase::Joining));

    let handle = SessionHandle {
        meeting_id: spec.meeting_id,
        key: spec.key.clone(),
        title: spec.title.clone(),
        started_at: Utc::now(),
        phase: Arc::clone(&phase),
        cmd_tx,
    };

    let task_handle = handle.clone();
    let task_state = Arc::clone(&state);
    tokio::spawn(async move {
        // Register from inside the task so the handle is visible before any of
        // the browser work starts; `run` owns the matching `unregister`.
        task_state.register(task_handle).await;
        run(task_state, spec, cmd_rx, phase, permit).await;
    });

    Ok(handle)
}

/// The full lifecycle. Never panics; every exit path writes exactly one
/// terminal `MeetingStatus` and then `state.unregister(&spec.key)`.
///
/// Order in Finalizing (hard requirement):
///   1. leave the call, close the browser
///   2. drop the frames sender -> run_segmenter flushes -> drops the utterance sender
///   3. await run_transcriber to completion (all segments inserted)
///   4. set_end_time, then set_status(<terminal>)
pub async fn run(
    state: SharedState,
    spec: SessionSpec,
    cmd_rx: mpsc::Receiver<SessionCommand>,
    phase: Arc<RwLock<SessionPhase>>,
    permit: tokio::sync::OwnedSemaphorePermit,
) {
    // Held for the whole session, finalization included, so the concurrency
    // slot is only released once the row is terminal.
    let _permit = permit;

    let meeting_id = spec.meeting_id;
    let key = spec.key.clone();
    let db = state.db.clone();

    // Armed until the orchestrator writes its own terminal status. If this
    // task is aborted or unwinds, the guard writes `failed` on the way out.
    let mut guard = TerminalGuard::new(Arc::clone(&state), key.clone(), meeting_id);

    // Command pump: owns cmd_rx, answers Query, raises the stop flag.
    let (stop_tx, stop_rx) = watch::channel(false);
    let pump = tokio::spawn(command_pump(cmd_rx, Arc::clone(&phase), stop_tx));

    let outcome = drive(&state, &spec, &phase, stop_rx).await;

    // --- terminal write (step 4 of the Finalizing order) ---------------------
    let terminal_phase = outcome.phase;
    let status = terminal_phase
        .terminal_status()
        .unwrap_or(MeetingStatus::Failed);

    if let Err(e) = db.set_end_time(meeting_id, Utc::now()) {
        tracing::warn!(%meeting_id, error = %e, "failed to write end_time");
    }
    match db.set_status(meeting_id, status, outcome.error.as_deref()) {
        // Only disarm once the terminal status is actually on disk.
        Ok(()) => guard.disarm(),
        Err(e) => tracing::error!(%meeting_id, error = %e, "failed to write terminal status"),
    }
    set_phase(&phase, terminal_phase).await;

    tracing::info!(
        %meeting_id,
        key = %key,
        status = status.as_str(),
        segments = outcome.segments,
        error = outcome.error.as_deref().unwrap_or(""),
        "session finished"
    );

    pump.abort();
    state.unregister(&key).await;
}

/// What `drive` reports back to `run`.
struct Outcome {
    phase: SessionPhase,
    error: Option<String>,
    segments: usize,
}

impl Outcome {
    fn failed(msg: impl Into<String>) -> Outcome {
        Outcome {
            phase: SessionPhase::Failed,
            error: Some(truncate(msg.into())),
            segments: 0,
        }
    }

    /// Terminal state for "no bot was needed here": the identity is already in
    /// the call. `completed` + zero segments, i.e. the same operational-skip
    /// shape as a bot that was never admitted (SPEC.md §0.1), so the client
    /// files it quietly instead of raising a recorder-failure alarm.
    fn skipped_not_needed() -> Outcome {
        Outcome {
            phase: SessionPhase::Completed,
            error: None,
            segments: 0,
        }
    }

    fn completed() -> Outcome {
        Outcome {
            phase: SessionPhase::Completed,
            error: None,
            segments: 0,
        }
    }
}

/// The state machine proper (`SPEC.md` §2). Returns the terminal phase plus the
/// error text to persist; it does **not** write the terminal status itself, so
/// there is exactly one such write, in `run`.
async fn drive(
    state: &SharedState,
    spec: &SessionSpec,
    phase: &Arc<RwLock<SessionPhase>>,
    mut stop_rx: watch::Receiver<bool>,
) -> Outcome {
    let cfg = &state.cfg;
    let db = state.db.clone();
    let meeting_id = spec.meeting_id;

    // --- Joining -------------------------------------------------------------
    set_phase(phase, SessionPhase::Joining).await;
    log_status(&db, meeting_id, MeetingStatus::Joining, None);

    // Create the capture sink BEFORE the browser starts: Chrome reads
    // PULSE_SINK once at launch, so a sink that appears later is a sink Chrome
    // never plays into.
    if cfg.capture_transport == CaptureTransport::Pulse {
        if let Some(sink) = cfg.pulse_sink.as_deref() {
            match crate::pulse::ensure_null_sink(sink) {
                Ok(true) => tracing::info!(sink, "created the meetbot capture sink"),
                Ok(false) => tracing::debug!(sink, "capture sink already present"),
                Err(e) => {
                    return Outcome::failed(format!(
                        "could not prepare the PulseAudio capture sink: {e:#}"
                    ));
                }
            }
        }
    }

    let opts = BrowserOptions {
        chromium_path: cfg.chromium_path.clone(),
        headless: cfg.headless,
        attach_cdp_port: cfg.cdp_port,
        user_data_dir: None,
        profile_template: cfg.profile_template.clone(),
        window_size: (1280, 720),
        pulse_sink: cfg.pulse_sink.clone(),
    };

    let meet = match MeetSession::launch(&opts).await {
        Ok(m) => m,
        Err(e) => return Outcome::failed(format!("browser launch failed: {e:#}")),
    };

    // SPEC.md §7.2. `launch` already armed the session with
    // `meet::DEFAULT_MAX_CALL_DURATION` (4h) — or the `MEETBOT_MAX_CALL_MIN`
    // env override — so the cap is live either way. This only applies an
    // explicit `max_call_duration_min` from config.toml on top; absent the key
    // we leave the session default untouched so the env override survives.
    if let Some(d) = cfg.max_call_duration() {
        meet.set_max_call_duration(d);
        tracing::info!(
            max_call_secs = meet.max_call_duration().as_secs(),
            "applied configured in-call ceiling"
        );
    }

    if let Err(e) = meet
        .join(&spec.key, &spec.bot_name, spec.passcode.as_deref())
        .await
    {
        let msg = format!("join failed: {e:#}");
        meet.close().await;
        // the operator is already in the room under this identity, so Meet only offered
        // the session-stealing control and the bot correctly refused it. Nothing
        // was lost and nothing is wrong: he is present, and his own recorder
        // covers meetings he attends. Treat it as an operational skip
        // (`completed`, zero segments, SPEC.md §0.1) rather than a failure —
        // a fail heartbeat on every meeting he attends would train him to
        // ignore the alert that matters.
        if msg.contains(SESSION_ALREADY_PRESENT) {
            tracing::info!(%meeting_id, "identity already in the call; skipping without joining");
            return Outcome::skipped_not_needed();
        }
        return Outcome::failed(msg);
    }

    // --- WaitingRoom ---------------------------------------------------------
    set_phase(phase, SessionPhase::WaitingRoom).await;
    log_status(&db, meeting_id, MeetingStatus::AwaitingAdmission, None);

    let admission = tokio::select! {
        biased;
        _ = stop_requested(&mut stop_rx) => {
            // Stop before admission: no audio was ever captured, so this is an
            // operational skip -> `completed`, zero segments (SPEC.md §0.1).
            tracing::info!(%meeting_id, "stop received while awaiting admission");
            meet.close().await;
            set_phase(phase, SessionPhase::Finalizing).await;
            log_status(&db, meeting_id, MeetingStatus::Finalizing, None);
            return Outcome::completed();
        }
        res = meet.wait_for_admission(cfg.admission_timeout()) => res,
    };

    let admission = match admission {
        Ok(a) => a,
        Err(e) => {
            let msg = format!("waiting-room watch failed: {e:#}");
            meet.close().await;
            return Outcome::failed(msg);
        }
    };

    if !matches!(admission, Admission::Admitted) {
        tracing::info!(%meeting_id, ?admission, "not admitted; finishing as completed");
        meet.close().await;
        set_phase(phase, SessionPhase::Finalizing).await;
        log_status(&db, meeting_id, MeetingStatus::Finalizing, None);
        return Outcome {
            phase: classify_admission(&admission),
            error: None,
            segments: 0,
        };
    }

    // --- InCall --------------------------------------------------------------
    set_phase(phase, SessionPhase::InCall).await;
    let started = Utc::now();
    if let Err(e) = db.set_start_time(meeting_id, started) {
        tracing::warn!(%meeting_id, error = %e, "failed to write start_time");
    }
    log_status(&db, meeting_id, MeetingStatus::Active, None);

    // Channel topology (SPEC.md §6.2), with one relay hop in the middle so the
    // orchestrator can tell whether any audio was ever captured — that bit
    // decides `stopped` vs `completed` in `classify_exit`.
    let (frame_tx, frame_rx) = mpsc::channel::<AudioFrame>(FRAME_CHANNEL_CAP);
    let (seg_tx, seg_rx) = mpsc::channel::<AudioFrame>(FRAME_CHANNEL_CAP);
    let (utt_tx, utt_rx) = mpsc::channel(UTTERANCE_CHANNEL_CAP);

    let frames_seen = Arc::new(AtomicU64::new(0));
    let (capture_done_tx, capture_done_rx) = oneshot::channel::<()>();
    let relay = tokio::spawn(run_relay(
        frame_rx,
        seg_tx,
        Arc::clone(&frames_seen),
        capture_done_rx,
    ));

    let wav_path = if spec.recording_enabled {
        let dir = cfg.audio_dir();
        match std::fs::create_dir_all(&dir) {
            Ok(()) => Some(dir.join(format!("{meeting_id}.wav"))),
            Err(e) => {
                tracing::warn!(error = %e, dir = %dir.display(), "cannot create audio dir; not recording");
                None
            }
        }
    } else {
        None
    };

    let segmenter = tokio::spawn(run_segmenter(seg_rx, VadConfig::default(), utt_tx, wav_path));

    let transcriber = if spec.transcribe_enabled {
        TranscriberTask::Live(tokio::spawn(crate::whisper::run_transcriber(
            Arc::clone(&state.whisper),
            utt_rx,
            db.clone(),
            meeting_id,
            spec.language.clone(),
            WHISPER_MAX_RETRIES,
        )))
    } else {
        // Still drain, otherwise the segmenter blocks on a full channel.
        let mut utt_rx = utt_rx;
        TranscriberTask::Drained(tokio::spawn(
            async move { while utt_rx.recv().await.is_some() {} },
        ))
    };

    // Two transports carry PCM out of the page (SPEC.md §7 / §7.1). The CDP
    // binding is the default and routes frames through meet.rs; the WebSocket
    // path hands the frames sender to a local ingest server instead and meet.rs
    // only injects the tap. `_ingest` must outlive the call: dropping it closes
    // the frames channel, which is precisely the end-of-capture signal.
    let mut _ingest: Option<crate::audio::IngestServer> = None;
    // The PulseAudio transport captures OUTSIDE the browser: Chrome plays the
    // meeting into a sink and a recorder thread reads that sink's monitor. It
    // must outlive the call for the same reason `_ingest` must -- dropping it
    // stops the capture -- and it publishes the peak counter `watch_call` uses
    // to decide the bot is deaf, since there is no tap in the page to read.
    let mut _pulse: Option<crate::pulse::PulseRecorder> = None;
    let mut pulse_stats: Option<Arc<crate::pulse::PulseStats>> = None;
    let mut pulse_speaker: Option<Arc<std::sync::Mutex<Option<String>>>> = None;
    let mut pulse_source: Option<String> = None;
    let capture_error = match cfg.capture_transport {
        CaptureTransport::Cdp => match meet.start_capture(frame_tx).await {
            Ok(()) => None,
            Err(e) => Some(format!("audio capture failed: {e:#}")),
        },
        CaptureTransport::WebSocket => {
            // Port 0: the OS picks, so concurrent bots never collide. Loopback
            // only — this socket must never be reachable off-box.
            let bind = SocketAddr::from((Ipv4Addr::LOCALHOST, 0));
            match crate::audio::start_ingest_server(bind, frame_tx).await {
                Ok(server) => {
                    let url = server.ingest_url();
                    _ingest = Some(server);
                    match meet.start_capture_ws(&url).await {
                        Ok(()) => None,
                        Err(e) => Some(format!("audio capture failed: {e:#}")),
                    }
                }
                Err(e) => Some(format!("audio ingest server failed to bind: {e:#}")),
            }
        }
        CaptureTransport::Pulse => match cfg.pulse_source.as_deref() {
            // No default source on purpose: Pulse's default is a microphone, so
            // a missing config would record the room instead of the meeting and
            // look like it was working.
            None => Some(
                "capture_transport is \"pulse\" but pulse_source is not set. It must name \
                 the monitor of the sink Chrome plays into, e.g. \"RDPSink.monitor\"; run \
                 `python3 tools/pulse_probe.py` to list this host's sources."
                    .to_string(),
            ),
            Some(source) => {
                let speaker = Arc::new(std::sync::Mutex::new(None));
                match crate::pulse::PulseRecorder::start(source, frame_tx, Arc::clone(&speaker)) {
                    Ok(rec) => {
                        tracing::info!(source, "recording the PulseAudio monitor");
                        pulse_stats = Some(Arc::clone(&rec.stats));
                        pulse_speaker = Some(speaker);
                        pulse_source = Some(source.to_string());
                        _pulse = Some(rec);
                        None
                    }
                    Err(e) => Some(format!("pulse capture failed: {e:#}")),
                }
            }
        },
    };

    let exit = match capture_error {
        Some(msg) => CallExit::BrowserError(msg),
        None => {
            let (stop_once_tx, stop_once_rx) = oneshot::channel();
            let mut bridge_rx = stop_rx.clone();
            let bridge = tokio::spawn(async move {
                stop_requested(&mut bridge_rx).await;
                let _ = stop_once_tx.send(());
            });
            let exit = meet
                .watch_call(
                    Duration::from_secs(cfg.lonely_grace_sec),
                    Duration::from_secs(cfg.empty_room_grace_sec),
                    stop_once_rx,
                    pulse_stats.clone(),
                    pulse_speaker.clone(),
                    pulse_source.clone(),
                )
                .await;
            bridge.abort();
            exit
        }
    };

    // --- Finalizing ----------------------------------------------------------
    set_phase(phase, SessionPhase::Finalizing).await;
    log_status(&db, meeting_id, MeetingStatus::Finalizing, None);

    // Everything below shares one wall-clock budget. See FINALIZE_BUDGET: the
    // relay, the segmenter and the transcriber are a single backpressured
    // chain, so bounding only the last of them bounds nothing.
    let finalize_deadline = tokio::time::Instant::now() + FINALIZE_BUDGET;
    let mut finalize_timed_out = false;

    // 1. leave the call, close the browser (drops meet's frames sender).
    //    Bounded: these are unbounded CDP round-trips against a page that, on
    //    the BrowserError exit, is already known to be broken. On expiry
    //    `close()`'s future is dropped mid-flight, which drops the MeetSession
    //    and runs its destructor — `closed` is only set once close() has
    //    actually finished, so the cleanup still happens there.
    let teardown = async {
        meet.leave().await;
        meet.close().await;
    };
    if tokio::time::timeout(BROWSER_TEARDOWN_BUDGET, teardown)
        .await
        .is_err()
    {
        finalize_timed_out = true;
        tracing::error!(
            %meeting_id,
            budget_sec = BROWSER_TEARDOWN_BUDGET.as_secs(),
            "browser teardown exceeded its budget; abandoning it to the destructor"
        );
    }

    // 1b. On the WebSocket transport the frames sender does NOT live in `meet`
    // — it lives in the ingest server's accept task. `_ingest` would otherwise
    // only drop when `drive` returns, which is AFTER the `relay.await` below,
    // so the relay would wait forever on a channel whose last sender it is
    // itself blocking the release of. Drop it here, once the page is gone.
    drop(_ingest.take());

    // 1c. Dropping the ingest server is still not enough on its own:
    // `IngestServer::drop` aborts the accept loop, but each accepted connection
    // runs in its own task holding a CLONE of the frames sender. Waiting for
    // those to end is waiting for Chrome to tear down its sockets, i.e. making
    // our finalization depend on remote behaviour. `capture_done` is the
    // authoritative end-of-capture signal instead: the relay stops on it and
    // drops `seg_tx` regardless of who else still holds a frames sender.
    let _ = capture_done_tx.send(());

    // 2. drain the relay -> segmenter flushes -> utterance sender drops.
    //    Both run against the shared deadline: a stalled transcriber holds the
    //    utterance channel full, which blocks the segmenter's `send`, which
    //    fills `seg_tx` and blocks the relay. Aborting the relay drops `seg_tx`
    //    and aborting the segmenter drops `utt_tx`, so giving up here also
    //    unblocks the stage after it.
    if await_stage(relay, finalize_deadline, meeting_id, "audio relay")
        .await
        .is_none()
    {
        finalize_timed_out = true;
    }

    match await_stage(segmenter, finalize_deadline, meeting_id, "segmenter").await {
        Some(Ok(Some(path))) => {
            if let Err(e) = db.set_audio_path(meeting_id, &path.to_string_lossy()) {
                tracing::warn!(%meeting_id, error = %e, "failed to write audio_path");
            }
        }
        Some(Ok(None)) => {}
        Some(Err(e)) => tracing::warn!(%meeting_id, error = %e, "segmenter failed"),
        // Abandoned: the WAV header is left unfinalized, which costs a
        // playable recording but never a transcript.
        None => finalize_timed_out = true,
    }

    // 3. await the transcriber under a deadline: every segment it managed to
    //    insert is on disk before `run` writes the terminal status, but the
    //    wait itself is bounded (see FINALIZE_BUDGET).
    let mut drain = match transcriber {
        TranscriberTask::Live(task) => {
            await_transcriber(task, remaining(finalize_deadline), &db, meeting_id).await
        }
        TranscriberTask::Drained(task) => {
            // Same deadline: this one only drains a channel, but "only drains a
            // channel" is exactly what the relay deadlock used to be too.
            if await_stage(task, finalize_deadline, meeting_id, "utterance drain")
                .await
                .is_none()
            {
                finalize_timed_out = true;
            }
            TranscriptDrain::default()
        }
    };
    // An earlier stage eating the budget is just as much proof that the
    // transcription path is broken as the transcriber eating it, and the
    // zero-segment classification below keys off exactly that.
    drain.timed_out |= finalize_timed_out;
    let segments = drain.segments;

    let captured_audio = frames_seen.load(Ordering::Relaxed) > 0;
    let mut terminal = classify_exit(&exit, captured_audio);
    let mut error = match &exit {
        CallExit::BrowserError(msg) => Some(truncate(msg.clone())),
        _ => None,
    };

    // --- silent-meeting vs. silent-failure ----------------------------------
    // `terminal + 0 segments` is what the Python client reads as an operational
    // skip (`skipped_not_admitted`, heartbeat ok, no MOM). That is correct for a
    // bot that was never admitted or sat in a room where nobody spoke. It is
    // catastrophic for a bot that heard a full meeting and lost every word to a
    // whisper outage that started AFTER the pre-join gate: the meeting vanishes
    // behind a green light. Audio captured + transcription enabled + nothing
    // transcribed is only benign when nothing was LOST, which the drain's own
    // counters settle far more reliably than a health probe can — see
    // `judge_silence`.
    if spec.transcribe_enabled
        && captured_audio
        && segments == 0
        && terminal == SessionPhase::Completed
    {
        let verdict = match judge_silence(&drain) {
            // No evidence either way — only reachable when the segmenter handed
            // whisper nothing at all. Ask whisper directly as a last resort.
            SilenceVerdict::Inconclusive => {
                if state.whisper.health().await {
                    SilenceVerdict::SilentRoom
                } else {
                    SilenceVerdict::TranscriptionLost
                }
            }
            decided => decided,
        };

        if verdict == SilenceVerdict::TranscriptionLost {
            terminal = SessionPhase::Failed;
            error = Some(truncate(format!(
                "transcription lost: audio was captured but whisper at {} produced no segments \
                 ({} of {} utterance(s) unrecoverable)",
                state.whisper.endpoint(),
                drain.lost(),
                drain.seen()
            )));
            tracing::error!(
                %meeting_id,
                endpoint = %state.whisper.endpoint(),
                timed_out = drain.timed_out,
                whisper_down = drain.whisper_down(),
                seen = drain.seen(),
                lost = drain.lost(),
                reported = drain.outcome.is_some(),
                "audio captured but transcription produced nothing; failing the session \
                 instead of reporting an empty meeting"
            );
        } else {
            tracing::info!(
                %meeting_id,
                seen = drain.seen(),
                "admitted meeting produced no speech and nothing was lost; finishing completed"
            );
        }
    }

    if drain.loss_ratio() >= LOUD_LOSS_RATIO {
        // Partial loss with segments on disk: still `completed`, because a
        // partial transcript beats no transcript, but never silently.
        tracing::error!(
            %meeting_id,
            segments,
            lost = drain.lost(),
            loss_ratio = drain.loss_ratio(),
            timed_out = drain.timed_out,
            whisper_down = drain.whisper_down(),
            "transcription lost a large share of this meeting"
        );
    }

    Outcome {
        phase: terminal,
        error,
        segments,
    }
}

/// What the finalization drain produced, whether or not it ran to completion.
#[derive(Debug, Default)]
struct TranscriptDrain {
    /// Segments on disk for this meeting.
    segments: usize,
    /// `None` when the worker never reported (deadline, panic, or hard error).
    outcome: Option<crate::whisper::TranscriberOutcome>,
    /// The drain hit `FINALIZE_BUDGET` and the worker was abandoned.
    timed_out: bool,
}

impl TranscriptDrain {
    fn whisper_down(&self) -> bool {
        self.outcome.map(|o| o.whisper_down).unwrap_or(false)
    }

    fn lost(&self) -> usize {
        self.outcome.map(|o| o.lost()).unwrap_or(0)
    }

    fn loss_ratio(&self) -> f64 {
        self.outcome.map(|o| o.loss_ratio()).unwrap_or(0.0)
    }

    /// Utterances the transcriber actually pulled off the channel. `0` when the
    /// worker never reported, which is why `judge_silence` checks `outcome`
    /// before it trusts this.
    fn seen(&self) -> usize {
        self.outcome.map(|o| o.seen).unwrap_or(0)
    }
}

/// Verdict on a terminal session that captured audio and produced zero segments
/// (the §0.1 v1.2 row). Pure, so every branch of the most dangerous decision in
/// the system is testable without a browser or a whisper.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SilenceVerdict {
    /// Nothing was ever lost: keep `completed`. THE load-bearing path — the
    /// client files this as `skipped_not_admitted` with a green heartbeat.
    SilentRoom,
    /// Work was handed to whisper and did not come back: `failed`.
    TranscriptionLost,
    /// Nothing reached whisper at all, so the counts prove nothing either way.
    Inconclusive,
}

/// Decide from the drain's own counters, in order of how conclusive they are.
///
/// The health probe used to be the *only* discriminator here, and it is far too
/// weak to carry that alone: `WhisperClient::health` counts **any** HTTP
/// response as up (SPEC.md §6.3), so a whisper whose process is alive but whose
/// model fails every transcription — 500 on each request, or a broken model
/// answering 200 with nothing — probes healthy while losing the entire meeting.
/// `run_transcriber` only sets `whisper_down` when its own probe reports a
/// *connection* failure, so that flag misses the same case. The counters do not:
/// `lost() > 0` with `inserted == 0` means whisper was handed work and returned
/// none of it, whatever the socket says.
///
/// Conversely, evidence of success is evidence: `seen > 0` with `lost == 0` and
/// nothing inserted means whisper answered every request and every answer was
/// blank or non-speech. That is a quiet room, and probing there could only
/// convert a probe blip into a false alarm on the path §0.1 protects.
fn judge_silence(drain: &TranscriptDrain) -> SilenceVerdict {
    // The worker was abandoned at the deadline; whatever was queued is gone.
    if drain.timed_out {
        return SilenceVerdict::TranscriptionLost;
    }
    let Some(outcome) = drain.outcome else {
        // The live worker panicked or returned an error without reporting
        // counts, and the DB holds no segments. Audio went in, nothing came
        // out, and there is no evidence it was ever a silent room.
        return SilenceVerdict::TranscriptionLost;
    };
    if outcome.whisper_down || outcome.lost() > 0 {
        return SilenceVerdict::TranscriptionLost;
    }
    if outcome.seen > 0 {
        // Every utterance round-tripped successfully and came back empty.
        SilenceVerdict::SilentRoom
    } else {
        // The segmenter never cut a single utterance, so whisper was never
        // asked anything. Nothing is lost, but nothing is proven either.
        SilenceVerdict::Inconclusive
    }
}

/// Time left until `deadline`; zero once it has passed, never negative.
fn remaining(deadline: tokio::time::Instant) -> Duration {
    deadline.saturating_duration_since(tokio::time::Instant::now())
}

/// Awaits one finalization stage against the shared deadline, aborting it on
/// expiry. `None` means the stage was abandoned (timed out, panicked, or was
/// already cancelled).
///
/// Aborting is what makes the chain unwind: each stage owns the sender that
/// feeds the next one, so dropping its task drops that sender and releases
/// whatever was blocked behind it.
async fn await_stage<T: Send + 'static>(
    task: tokio::task::JoinHandle<T>,
    deadline: tokio::time::Instant,
    meeting_id: Uuid,
    stage: &'static str,
) -> Option<T> {
    let abort = task.abort_handle();
    match tokio::time::timeout_at(deadline, task).await {
        Ok(Ok(value)) => Some(value),
        Ok(Err(e)) => {
            tracing::warn!(%meeting_id, stage, error = %e, "finalization stage ended abnormally");
            None
        }
        Err(_elapsed) => {
            abort.abort();
            tracing::error!(
                %meeting_id,
                stage,
                "finalization stage exceeded the shared budget; abandoning it"
            );
            None
        }
    }
}

/// Awaits the transcriber under `budget`, falling back to the DB's own segment
/// count on every abnormal path.
///
/// The deadline is the difference between losing a backlog and losing a
/// transcript: `run_transcriber` drains serially, so a whisper that accepts the
/// connection and then never answers costs `max_retries * request_timeout` per
/// queued utterance. Waiting that out overruns `SHUTDOWN_GRACE` and the unit's
/// `TimeoutStopSec`, and a SIGKILLed process never writes its terminal status.
/// Segments are committed as they are produced, so aborting the worker keeps
/// everything transcribed so far.
async fn await_transcriber(
    task: tokio::task::JoinHandle<anyhow::Result<crate::whisper::TranscriberOutcome>>,
    budget: Duration,
    db: &Db,
    meeting_id: Uuid,
) -> TranscriptDrain {
    let abort = task.abort_handle();
    match tokio::time::timeout(budget, task).await {
        Ok(Ok(Ok(outcome))) => TranscriptDrain {
            segments: outcome.inserted,
            outcome: Some(outcome),
            timed_out: false,
        },
        Ok(Ok(Err(e))) => {
            tracing::warn!(%meeting_id, error = %e, "transcriber returned an error");
            TranscriptDrain {
                segments: db.count_segments(meeting_id).unwrap_or(0),
                outcome: None,
                timed_out: false,
            }
        }
        Ok(Err(e)) => {
            tracing::warn!(%meeting_id, error = %e, "transcriber task ended abnormally");
            TranscriptDrain {
                segments: db.count_segments(meeting_id).unwrap_or(0),
                outcome: None,
                timed_out: false,
            }
        }
        Err(_elapsed) => {
            abort.abort();
            let segments = db.count_segments(meeting_id).unwrap_or(0);
            tracing::error!(
                %meeting_id,
                segments,
                budget_sec = budget.as_secs(),
                "transcription drain exceeded the finalization budget; abandoning the backlog \
                 and committing what is already transcribed"
            );
            TranscriptDrain {
                segments,
                outcome: None,
                timed_out: true,
            }
        }
    }
}

/// Relays capture frames into the segmenter, counting them on the way.
///
/// Termination must never depend on the page closing its sockets. On the
/// WebSocket transport the frames sender is cloned into one task per accepted
/// connection, and `IngestServer::drop` only aborts the accept loop — a
/// connection task Chrome never tears down would hold the channel open forever
/// and hang finalization. `capture_done` is therefore authoritative: once it
/// fires the relay drains what is already buffered and returns, dropping
/// `seg_tx`, which is what makes `run_segmenter` flush and close the utterance
/// channel behind it.
async fn run_relay(
    mut frame_rx: mpsc::Receiver<AudioFrame>,
    seg_tx: mpsc::Sender<AudioFrame>,
    counter: Arc<AtomicU64>,
    capture_done: oneshot::Receiver<()>,
) {
    let mut capture_done = capture_done;
    loop {
        tokio::select! {
            // Biased on the stop signal: with the page gone, a stuck sender must
            // not be able to starve the shutdown arm.
            biased;
            _ = &mut capture_done => {
                while let Ok(frame) = frame_rx.try_recv() {
                    counter.fetch_add(1, Ordering::Relaxed);
                    if seg_tx.send(frame).await.is_err() {
                        break;
                    }
                }
                break;
            }
            frame = frame_rx.recv() => {
                match frame {
                    Some(frame) => {
                        counter.fetch_add(1, Ordering::Relaxed);
                        if seg_tx.send(frame).await.is_err() {
                            break;
                        }
                    }
                    None => break,
                }
            }
        }
    }
}

/// Either the real whisper worker or a no-op drain when `transcribe_enabled`
/// is false. Both are awaited identically during Finalizing.
enum TranscriberTask {
    Live(tokio::task::JoinHandle<anyhow::Result<crate::whisper::TranscriberOutcome>>),
    Drained(tokio::task::JoinHandle<()>),
}

/// Maps a `meet::CallExit` + "did we capture any audio" to the terminal phase,
/// per the §0.1 decision table. Pure; unit-testable.
///
/// The `!captured_audio` branches are load-bearing: the Python client reads
/// `terminal + zero segments` as an operational skip only when the status is
/// `completed`. A bot that was stopped or bounced before a single audio frame
/// arrived produced nothing, so it is a skip, not a failure.
pub fn classify_exit(exit: &crate::meet::CallExit, captured_audio: bool) -> SessionPhase {
    match exit {
        CallExit::MeetingEnded => SessionPhase::Completed,
        CallExit::RemovedByHost | CallExit::Stopped => {
            if captured_audio {
                SessionPhase::Stopped
            } else {
                SessionPhase::Completed
            }
        }
        CallExit::BrowserError(_) => SessionPhase::Failed,
    }
}

/// Waiting-room outcomes always classify to `SessionPhase::Completed`.
pub fn classify_admission(admission: &crate::meet::Admission) -> SessionPhase {
    match admission {
        Admission::Admitted | Admission::TimedOut | Admission::Denied => SessionPhase::Completed,
    }
}

// ---------------------------------------------------------------------------
// internals
// ---------------------------------------------------------------------------

/// Last line of defence: if the session task is aborted (SIGTERM racing a live
/// call, `JoinHandle::abort`, a panic unwinding through `run`), `Drop` still
/// runs. `Db` methods are synchronous, so the terminal write happens right
/// here; the async `unregister` is best-effort via the current runtime.
struct TerminalGuard {
    state: SharedState,
    key: MeetingKey,
    meeting_id: Uuid,
    armed: bool,
}

impl TerminalGuard {
    fn new(state: SharedState, key: MeetingKey, meeting_id: Uuid) -> TerminalGuard {
        TerminalGuard {
            state,
            key,
            meeting_id,
            armed: true,
        }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        tracing::error!(
            meeting_id = %self.meeting_id,
            key = %self.key,
            "session task ended without a terminal status; forcing failed"
        );
        if let Err(e) = self.state.db.set_status(
            self.meeting_id,
            MeetingStatus::Failed,
            Some("session task cancelled before finalizing"),
        ) {
            tracing::error!(meeting_id = %self.meeting_id, error = %e, "forced terminal write failed");
        }
        if let Ok(rt) = tokio::runtime::Handle::try_current() {
            let state = Arc::clone(&self.state);
            let key = self.key.clone();
            rt.spawn(async move { state.unregister(&key).await });
        }
    }
}

/// Owns `cmd_rx` for the lifetime of the session: answers `Query` from the
/// shared phase cell and raises the stop flag on `Stop`.
async fn command_pump(
    mut cmd_rx: mpsc::Receiver<SessionCommand>,
    phase: Arc<RwLock<SessionPhase>>,
    stop_tx: watch::Sender<bool>,
) {
    while let Some(cmd) = cmd_rx.recv().await {
        match cmd {
            SessionCommand::Stop => {
                let _ = stop_tx.send(true);
            }
            SessionCommand::Query(reply) => {
                let current = *phase.read().await;
                let _ = reply.send(current);
            }
        }
    }
}

/// Resolves the first time the stop flag is true. Pends forever if the sender
/// is gone without the flag ever having been raised, so it is safe to leave
/// inside a `select!` arm.
async fn stop_requested(rx: &mut watch::Receiver<bool>) {
    loop {
        if *rx.borrow_and_update() {
            return;
        }
        if rx.changed().await.is_err() {
            std::future::pending::<()>().await;
        }
    }
}

async fn set_phase(phase: &Arc<RwLock<SessionPhase>>, next: SessionPhase) {
    *phase.write().await = next;
}

fn log_status(db: &Db, meeting_id: Uuid, status: MeetingStatus, error: Option<&str>) {
    if let Err(e) = db.set_status(meeting_id, status, error) {
        tracing::warn!(%meeting_id, status = status.as_str(), error = %e, "status write failed");
    }
}

fn truncate(mut s: String) -> String {
    if s.len() > MAX_ERROR_LEN {
        let mut cut = MAX_ERROR_LEN;
        while cut > 0 && !s.is_char_boundary(cut) {
            cut -= 1;
        }
        s.truncate(cut);
        s.push('…');
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    // SPEC.md §7.2 end-to-end: a `max_call_duration_min` in config.toml must
    // actually land on the live `MeetSession`. The unit tests in state.rs only
    // prove the key parses; this launches a real headless browser and applies
    // the value through the exact expression `run_session` uses, so a wiring
    // regression (the field existing but never being read) fails here.
    //
    // Skips rather than fails when chromium is missing, so the suite still runs
    // on a box without the playwright cache.
    #[tokio::test]
    async fn configured_max_call_duration_reaches_the_session() {
        let cfg = match crate::state::Config::load(std::path::Path::new("config.toml")) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("skipping: config.toml unreadable: {e}");
                return;
            }
        };
        if !cfg.chromium_path.exists() {
            eprintln!(
                "skipping: no chromium at {}",
                cfg.chromium_path.display()
            );
            return;
        }

        let opts = BrowserOptions {
            chromium_path: cfg.chromium_path.clone(),
            headless: true,
            // Never attach to 9222 (tln-browser.service) from a test.
            attach_cdp_port: None,
            user_data_dir: None,
            // No template: this test only proves the config value reaches the
            // session, and copying the real signed-in profile would be slow and
            // would drag the operator's live Google session into a test.
            profile_template: None,
            window_size: (1280, 720),
            pulse_sink: None,
        };

        let meet = tokio::time::timeout(Duration::from_secs(60), MeetSession::launch(&opts))
            .await
            .expect("browser launch timed out")
            .expect("browser launched");

        // Fresh session starts on the 4h backstop.
        assert_eq!(
            meet.max_call_duration(),
            crate::meet::DEFAULT_MAX_CALL_DURATION,
            "a fresh session must already be capped"
        );

        // The exact wiring from `run_session`.
        let configured = std::time::Duration::from_secs(90 * 60);
        meet.set_max_call_duration(configured);
        assert_eq!(
            meet.max_call_duration(),
            configured,
            "configured ceiling did not reach the session"
        );

        // Zero is clamped back to the default, never read as "uncapped".
        meet.set_max_call_duration(std::time::Duration::ZERO);
        assert_eq!(
            meet.max_call_duration(),
            crate::meet::DEFAULT_MAX_CALL_DURATION,
            "zero must clamp to the default, not disable the cap"
        );

        meet.close().await;
    }

    #[test]
    fn meeting_end_is_always_completed() {
        assert_eq!(
            classify_exit(&CallExit::MeetingEnded, true),
            SessionPhase::Completed
        );
        assert_eq!(
            classify_exit(&CallExit::MeetingEnded, false),
            SessionPhase::Completed
        );
    }

    #[test]
    fn stop_with_audio_is_stopped_without_is_completed() {
        assert_eq!(
            classify_exit(&CallExit::Stopped, true),
            SessionPhase::Stopped
        );
        assert_eq!(
            classify_exit(&CallExit::Stopped, false),
            SessionPhase::Completed
        );
        assert_eq!(
            classify_exit(&CallExit::RemovedByHost, true),
            SessionPhase::Stopped
        );
        assert_eq!(
            classify_exit(&CallExit::RemovedByHost, false),
            SessionPhase::Completed
        );
    }

    #[test]
    fn browser_error_is_failed() {
        assert_eq!(
            classify_exit(&CallExit::BrowserError("page detached".into()), true),
            SessionPhase::Failed
        );
        assert_eq!(
            classify_exit(&CallExit::BrowserError("page detached".into()), false),
            SessionPhase::Failed
        );
    }

    #[test]
    fn every_admission_outcome_is_completed() {
        for a in [Admission::Admitted, Admission::TimedOut, Admission::Denied] {
            assert_eq!(classify_admission(&a), SessionPhase::Completed);
        }
    }

    #[test]
    fn all_classifications_are_terminal() {
        let exits = [
            CallExit::MeetingEnded,
            CallExit::RemovedByHost,
            CallExit::Stopped,
            CallExit::BrowserError("x".into()),
        ];
        for exit in &exits {
            for captured in [true, false] {
                let phase = classify_exit(exit, captured);
                assert!(phase.is_terminal(), "{phase:?} must be terminal");
                assert!(phase.terminal_status().is_some());
            }
        }
    }

    #[test]
    fn truncate_keeps_errors_under_the_client_limit() {
        let out = truncate("x".repeat(1000));
        assert!(out.chars().count() <= MAX_ERROR_LEN + 1);
        assert!(out.ends_with('…'));
        assert_eq!(truncate("short".to_string()), "short");
    }

    // -----------------------------------------------------------------------
    // Finalization bounds
    // -----------------------------------------------------------------------

    /// `main::SHUTDOWN_GRACE` is 90 s and `meetbot.service` sets
    /// `TimeoutStopSec=120`. The drain budget has to leave room for the browser
    /// teardown, the segmenter flush and the terminal DB write on top.
    #[test]
    fn finalize_budget_stays_under_the_shutdown_grace() {
        assert!(
            FINALIZE_BUDGET < Duration::from_secs(90),
            "FINALIZE_BUDGET must stay below main::SHUTDOWN_GRACE"
        );
        assert!(FINALIZE_BUDGET <= Duration::from_secs(60));
    }

    fn test_db() -> (Db, Uuid) {
        let db = Db::open_in_memory().expect("db");
        let rec = db
            .create_meeting(&crate::db::NewMeeting {
                key: MeetingKey::new(crate::state::Platform::GoogleMeet, "abc-defg-hij"),
                title: None,
                bot_name: "Notetaker".to_string(),
                language: None,
                passcode: None,
                recording_enabled: false,
                transcribe_enabled: true,
            })
            .expect("create meeting");
        let id = rec.id;
        (db, id)
    }

    /// Regression: a transcriber that never returns must not pin finalization
    /// past the shutdown grace, and abandoning it must not cost the segments
    /// that were already committed.
    #[tokio::test]
    async fn hung_transcriber_hits_the_budget_and_keeps_committed_segments() {
        let (db, meeting_id) = test_db();
        db.insert_segments(
            meeting_id,
            &[crate::db::NewSegment {
                start_time: 0.0,
                end_time: 1.0,
                speaker: Some("YourManager".into()),
                text: "this was already transcribed".into(),
                language: Some("en".into()),
            }],
        )
        .expect("seed segment");

        let task = tokio::spawn(async move {
            std::future::pending::<()>().await;
            Ok(crate::whisper::TranscriberOutcome::default())
        });

        let started = std::time::Instant::now();
        let drain =
            await_transcriber(task, Duration::from_millis(200), &db, meeting_id).await;

        assert!(drain.timed_out);
        assert!(drain.outcome.is_none());
        assert_eq!(
            drain.segments, 1,
            "already-committed work must survive the deadline"
        );
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "the deadline must actually fire"
        );
    }

    #[tokio::test]
    async fn completed_transcriber_reports_its_own_counts() {
        let (db, meeting_id) = test_db();
        let reported = crate::whisper::TranscriberOutcome {
            inserted: 7,
            seen: 10,
            dropped: 3,
            abandoned: 0,
            whisper_down: false,
        };
        let task = tokio::spawn(async move { Ok(reported) });

        let drain = await_transcriber(task, Duration::from_secs(5), &db, meeting_id).await;
        assert!(!drain.timed_out);
        assert_eq!(drain.segments, 7);
        assert_eq!(drain.lost(), 3);
        assert!(drain.loss_ratio() >= LOUD_LOSS_RATIO);
        assert!(!drain.whisper_down());
    }

    #[test]
    fn a_clean_drain_never_looks_like_loss() {
        // The silent-meeting path: nothing seen, nothing lost, nothing loud.
        let drain = TranscriptDrain {
            segments: 0,
            outcome: Some(crate::whisper::TranscriberOutcome::default()),
            timed_out: false,
        };
        assert_eq!(drain.lost(), 0);
        assert_eq!(drain.loss_ratio(), 0.0);
        assert!(drain.loss_ratio() < LOUD_LOSS_RATIO);
        assert!(!drain.whisper_down());
    }

    // -----------------------------------------------------------------------
    // The §0.1 knife edge: silent room vs. silent failure
    // -----------------------------------------------------------------------

    fn drain_of(outcome: crate::whisper::TranscriberOutcome) -> TranscriptDrain {
        TranscriptDrain {
            segments: outcome.inserted,
            outcome: Some(outcome),
            timed_out: false,
        }
    }

    /// THE regression that must never break: an admitted bot in a room where
    /// nobody spoke finishes `completed` with zero segments, and gets there
    /// without asking whisper anything, so a probe blip cannot false-alarm it.
    #[test]
    fn a_quiet_room_whose_utterances_all_round_tripped_is_never_failed() {
        // 12 utterances went to whisper, all answered, all noise/blank.
        let drain = drain_of(crate::whisper::TranscriberOutcome {
            inserted: 0,
            seen: 12,
            dropped: 0,
            abandoned: 0,
            whisper_down: false,
        });
        assert_eq!(judge_silence(&drain), SilenceVerdict::SilentRoom);
    }

    /// A room so quiet the segmenter never cut an utterance: nothing was lost,
    /// but nothing was proven either, so this is the one case that still probes.
    #[test]
    fn a_room_that_produced_no_utterances_at_all_is_inconclusive() {
        let drain = drain_of(crate::whisper::TranscriberOutcome::default());
        assert_eq!(judge_silence(&drain), SilenceVerdict::Inconclusive);
    }

    /// The hole the health probe left wide open. Whisper's process is alive, so
    /// `health()` returns true and `run_transcriber` never sets `whisper_down`,
    /// but every transcription failed. Before this, the whole meeting shipped as
    /// `completed` + `segments: []` and the client filed it as
    /// `skipped_not_admitted` with a GREEN heartbeat.
    #[test]
    fn a_reachable_whisper_that_transcribes_nothing_is_still_data_loss() {
        let drain = drain_of(crate::whisper::TranscriberOutcome {
            inserted: 0,
            seen: 40,
            dropped: 40,
            abandoned: 0,
            whisper_down: false, // its own probe saw a live socket
        });
        assert_eq!(judge_silence(&drain), SilenceVerdict::TranscriptionLost);
        assert!(
            !drain.whisper_down(),
            "the flag alone must not be what catches this, or the test is vacuous"
        );
        assert_eq!(drain.loss_ratio(), 1.0);
    }

    /// Even a single unrecoverable utterance, with nothing inserted, is loss.
    #[test]
    fn partial_drops_with_zero_segments_are_loss_not_silence() {
        let drain = drain_of(crate::whisper::TranscriberOutcome {
            inserted: 0,
            seen: 3,
            dropped: 1,
            abandoned: 0,
            whisper_down: false,
        });
        assert_eq!(judge_silence(&drain), SilenceVerdict::TranscriptionLost);
    }

    #[test]
    fn a_confirmed_outage_is_loss_without_probing() {
        let drain = drain_of(crate::whisper::TranscriberOutcome {
            inserted: 0,
            seen: 30,
            dropped: 2,
            abandoned: 28,
            whisper_down: true,
        });
        assert_eq!(judge_silence(&drain), SilenceVerdict::TranscriptionLost);
    }

    #[test]
    fn an_expired_drain_is_loss_even_with_clean_counters() {
        let drain = TranscriptDrain {
            segments: 0,
            outcome: Some(crate::whisper::TranscriberOutcome::default()),
            timed_out: true,
        };
        assert_eq!(judge_silence(&drain), SilenceVerdict::TranscriptionLost);
    }

    /// A worker that panicked or errored reported no counters at all. Zero
    /// segments after capturing audio is not something to call a quiet room.
    #[test]
    fn a_worker_that_never_reported_is_loss_not_silence() {
        let drain = TranscriptDrain {
            segments: 0,
            outcome: None,
            timed_out: false,
        };
        assert_eq!(judge_silence(&drain), SilenceVerdict::TranscriptionLost);
        assert_eq!(
            drain.seen(),
            0,
            "seen() defaults to 0 here, which is exactly why judge_silence \
             must check `outcome` before trusting it"
        );
    }

    // -----------------------------------------------------------------------
    // Relay termination
    // -----------------------------------------------------------------------

    fn frame(offset: f64) -> AudioFrame {
        AudioFrame {
            pcm: vec![900i16; 160],
            offset_sec: offset,
            speaker: None,
        }
    }

    /// Regression for the capture-transport deadlock residual: `IngestServer`'s
    /// drop only aborts the accept loop, so a per-connection task still holding
    /// a cloned frames sender used to keep the relay alive until Chrome closed
    /// the socket. The relay must now terminate on our own signal, with the
    /// zombie sender still very much alive.
    #[tokio::test]
    async fn relay_terminates_while_a_connection_task_still_holds_a_sender() {
        let (frame_tx, frame_rx) = mpsc::channel::<AudioFrame>(8);
        let (seg_tx, mut seg_rx) = mpsc::channel::<AudioFrame>(8);
        let (done_tx, done_rx) = oneshot::channel();
        let counter = Arc::new(AtomicU64::new(0));

        let relay = tokio::spawn(run_relay(
            frame_rx,
            seg_tx,
            Arc::clone(&counter),
            done_rx,
        ));

        frame_tx.send(frame(0.0)).await.expect("buffer a frame");

        // Stands in for `serve_ingest_conn`: a task that outlives the accept
        // loop and never closes its clone of the sender.
        let zombie = frame_tx.clone();
        let conn = tokio::spawn(async move {
            let _held = zombie;
            std::future::pending::<()>().await;
        });
        drop(frame_tx); // the accept task's sender goes; the connection's stays

        done_tx.send(()).expect("relay is listening");

        tokio::time::timeout(Duration::from_secs(5), relay)
            .await
            .expect("relay must terminate without the connection closing")
            .expect("relay task must not panic");

        assert_eq!(
            counter.load(Ordering::Relaxed),
            1,
            "the buffered frame must still be drained, not dropped"
        );
        assert!(seg_rx.recv().await.is_some());
        // seg_tx was dropped with the relay, which is the segmenter's flush cue.
        assert!(seg_rx.recv().await.is_none());

        conn.abort();
    }

    // -----------------------------------------------------------------------
    // Shared finalization deadline
    // -----------------------------------------------------------------------

    fn loud_frame(offset: f64) -> AudioFrame {
        // 100 ms at 16 kHz, well above VadConfig::silence_rms.
        AudioFrame {
            pcm: vec![9000i16; 1600],
            offset_sec: offset,
            speaker: None,
        }
    }

    /// Regression for the review's central finding: `FINALIZE_BUDGET` used to
    /// guard only the transcriber await, which bounded nothing, because the
    /// three stages are one backpressured chain.
    ///
    /// A whisper that accepts TCP and never answers leaves the utterance
    /// channel full, so `run_segmenter` blocks in `utterances.send()`, `seg_tx`
    /// fills and the relay blocks behind it — and both of those were awaited
    /// unbounded and *before* the deadline was armed. Measured against the real
    /// `run_relay` + `run_segmenter`, the old sequence still had not returned
    /// after 3 s with the worker stalled; the real-world figure is ~16 min
    /// (`HEALTH_PROBE_AFTER_FAILURES` x `max_retries` x `DEFAULT_TIMEOUT`),
    /// far past `TimeoutStopSec`, so systemd SIGKILLed the process and the
    /// terminal status was never written.
    #[tokio::test]
    async fn a_stalled_transcriber_cannot_pin_finalization_through_backpressure() {
        let (db, meeting_id) = test_db();
        db.insert_segments(
            meeting_id,
            &[crate::db::NewSegment {
                start_time: 0.0,
                end_time: 1.0,
                speaker: None,
                text: "committed before whisper stalled".into(),
                language: Some("en".into()),
            }],
        )
        .expect("seed segment");

        let (frame_tx, frame_rx) = mpsc::channel::<AudioFrame>(FRAME_CHANNEL_CAP);
        let (seg_tx, seg_rx) = mpsc::channel::<AudioFrame>(FRAME_CHANNEL_CAP);
        let (utt_tx, utt_rx) = mpsc::channel(UTTERANCE_CHANNEL_CAP);
        let (done_tx, done_rx) = oneshot::channel();
        let counter = Arc::new(AtomicU64::new(0));

        let relay = tokio::spawn(run_relay(frame_rx, seg_tx, Arc::clone(&counter), done_rx));
        // Tight VAD so a short test produces far more than UTTERANCE_CHANNEL_CAP
        // utterances; the shape under test is the backpressure, not the cutter.
        let cfg = VadConfig {
            min_utterance_ms: 20,
            max_utterance_ms: 100,
            trailing_silence_ms: 40,
            ..VadConfig::default()
        };
        let segmenter = tokio::spawn(run_segmenter(seg_rx, cfg, utt_tx, None));

        // The stalled worker: holds the receiver, never makes progress. This is
        // a whisper that completed the TCP handshake and then went quiet.
        let transcriber = tokio::spawn(async move {
            let _held = utt_rx;
            std::future::pending::<()>().await;
            Ok(crate::whisper::TranscriberOutcome::default())
        });

        // A meeting's worth of speech, far more than the 64-deep channel holds.
        for i in 0..200u32 {
            frame_tx
                .send(loud_frame(f64::from(i) * 0.1))
                .await
                .expect("frame channel has room");
        }
        drop(frame_tx);
        let _ = done_tx.send(());

        let budget = Duration::from_millis(300);
        let deadline = tokio::time::Instant::now() + budget;
        let started = std::time::Instant::now();

        let relay_out = await_stage(relay, deadline, meeting_id, "audio relay").await;
        let seg_out = await_stage(segmenter, deadline, meeting_id, "segmenter").await;
        let drain = await_transcriber(transcriber, remaining(deadline), &db, meeting_id).await;

        assert!(
            started.elapsed() < Duration::from_secs(5),
            "finalization must be bounded end to end, not just at the last stage"
        );
        assert!(
            relay_out.is_none() || seg_out.is_none() || drain.timed_out,
            "something must have reported hitting the deadline"
        );
        assert_eq!(
            drain.segments, 1,
            "segments committed before the stall must survive every give-up path"
        );
    }

    /// The healthy path must not be charged the deadline: with a transcriber
    /// that drains normally, every stage returns its real value.
    #[tokio::test]
    async fn a_healthy_pipeline_finalizes_without_touching_the_deadline() {
        // `_db` rather than `_`: the binding must live to the end of the test so
        // the temp database is not dropped out from under the pipeline.
        let (_db, meeting_id) = test_db();

        let (frame_tx, frame_rx) = mpsc::channel::<AudioFrame>(FRAME_CHANNEL_CAP);
        let (seg_tx, seg_rx) = mpsc::channel::<AudioFrame>(FRAME_CHANNEL_CAP);
        let (utt_tx, mut utt_rx) = mpsc::channel(UTTERANCE_CHANNEL_CAP);
        let (done_tx, done_rx) = oneshot::channel();
        let counter = Arc::new(AtomicU64::new(0));

        let relay = tokio::spawn(run_relay(frame_rx, seg_tx, Arc::clone(&counter), done_rx));
        let cfg = VadConfig {
            min_utterance_ms: 20,
            max_utterance_ms: 100,
            trailing_silence_ms: 40,
            ..VadConfig::default()
        };
        let segmenter = tokio::spawn(run_segmenter(seg_rx, cfg, utt_tx, None));
        let consumer = tokio::spawn(async move {
            let mut n = 0usize;
            while utt_rx.recv().await.is_some() {
                n += 1;
            }
            n
        });

        for i in 0..200u32 {
            frame_tx
                .send(loud_frame(f64::from(i) * 0.1))
                .await
                .expect("frame");
        }
        drop(frame_tx);
        let _ = done_tx.send(());

        let deadline = tokio::time::Instant::now() + FINALIZE_BUDGET;
        assert!(
            await_stage(relay, deadline, meeting_id, "audio relay")
                .await
                .is_some(),
            "a healthy relay must complete, not be abandoned"
        );
        assert!(
            matches!(
                await_stage(segmenter, deadline, meeting_id, "segmenter").await,
                Some(Ok(_))
            ),
            "a healthy segmenter must complete"
        );
        assert_eq!(counter.load(Ordering::Relaxed), 200);
        assert!(
            consumer.await.expect("consumer") > UTTERANCE_CHANNEL_CAP,
            "the test must actually overfill the utterance channel"
        );
        assert!(
            remaining(deadline) > Duration::from_secs(40),
            "the healthy path must barely spend the budget"
        );
    }

    /// The stage helper must abort on expiry rather than leave the task
    /// running: aborting is what drops the stage's sender and unblocks the
    /// stage behind it.
    #[tokio::test]
    async fn an_abandoned_stage_is_actually_aborted() {
        let flag = Arc::new(AtomicU64::new(0));
        let inner = Arc::clone(&flag);
        let task = tokio::spawn(async move {
            let _guard = DropCounter(inner);
            std::future::pending::<()>().await;
        });

        let deadline = tokio::time::Instant::now() + Duration::from_millis(50);
        assert!(
            await_stage(task, deadline, Uuid::nil(), "stalled")
                .await
                .is_none()
        );

        // The abort has to have unwound the task, dropping what it held.
        for _ in 0..100 {
            if flag.load(Ordering::Relaxed) == 1 {
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("abandoned stage was never aborted; its senders would leak");
    }

    struct DropCounter(Arc<AtomicU64>);
    impl Drop for DropCounter {
        fn drop(&mut self) {
            self.0.fetch_add(1, Ordering::Relaxed);
        }
    }

    #[test]
    fn remaining_is_clamped_at_zero() {
        let past = tokio::time::Instant::now() - Duration::from_secs(10);
        assert_eq!(remaining(past), Duration::ZERO);
    }

    /// Browser teardown is charged on top of the drain, so both together must
    /// still leave room under `SHUTDOWN_GRACE` for the terminal DB write.
    #[test]
    fn the_finalization_budgets_together_stay_under_the_shutdown_grace() {
        assert!(
            BROWSER_TEARDOWN_BUDGET + FINALIZE_BUDGET < Duration::from_secs(90),
            "teardown + drain must stay below main::SHUTDOWN_GRACE"
        );
        assert!(BROWSER_TEARDOWN_BUDGET + FINALIZE_BUDGET < Duration::from_secs(120));
    }

    /// The ordinary CDP path: the sender simply drops and the relay ends, with
    /// no signal ever sent.
    #[tokio::test]
    async fn relay_still_ends_when_the_last_sender_drops() {
        let (frame_tx, frame_rx) = mpsc::channel::<AudioFrame>(8);
        let (seg_tx, mut seg_rx) = mpsc::channel::<AudioFrame>(8);
        let (_done_tx, done_rx) = oneshot::channel();
        let counter = Arc::new(AtomicU64::new(0));

        let relay = tokio::spawn(run_relay(
            frame_rx,
            seg_tx,
            Arc::clone(&counter),
            done_rx,
        ));

        frame_tx.send(frame(0.0)).await.expect("send");
        frame_tx.send(frame(0.01)).await.expect("send");
        drop(frame_tx);

        tokio::time::timeout(Duration::from_secs(5), relay)
            .await
            .expect("relay must terminate on channel close")
            .expect("no panic");

        assert_eq!(counter.load(Ordering::Relaxed), 2);
        assert!(seg_rx.recv().await.is_some());
        assert!(seg_rx.recv().await.is_some());
        assert!(seg_rx.recv().await.is_none());
    }
}

