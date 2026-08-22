//! CDP browser automation: `BrowserOptions`, `Admission`, `CallExit`,
//! `MeetSession` — join, wait out the waiting room, tap the audio, leave.
//!
//! Contract: `SPEC.md` §7. Owner: the `meet` builder agent.
//!
//! # Shape of this module
//!
//! 1. [`selectors`] — **the** DOM selector table for Google Meet. Everything
//!    this module knows about Meet's markup lives there and nowhere else. The
//!    Microsoft Teams equivalent is [`crate::teams::selectors`], a separate
//!    table under the same one-table rule; [`probe_spec`] is the only place the
//!    two are dispatched between.
//! 2. `js` — JavaScript templates with `__TOKEN__` placeholders. The tokens are
//!    filled from the selector table at call time, so the templates stay free of
//!    hardcoded markup knowledge.
//! 3. [`MeetSession`] — owns the browser, the page, the chromiumoxide handler
//!    task and the audio-binding pump.
//!
//! # Failure philosophy
//!
//! Per `SPEC.md` §0.1 a bot that never gets admitted, or that sits in a silent
//! room, is an *operational skip*, not a failure. This module therefore returns
//! [`Admission::TimedOut`] / [`Admission::Denied`] as ordinary `Ok` values —
//! only a genuinely broken browser produces an `Err` (which `session.rs` maps to
//! `MeetingStatus::Failed`).

use std::path::PathBuf;
use std::time::{Duration, Instant};

use anyhow::{Context as _, Result, anyhow, bail};
use chromiumoxide::browser::{Browser, BrowserConfig};
use chromiumoxide::cdp::js_protocol::runtime::{AddBindingParams, EventBindingCalled};
use chromiumoxide::page::Page;
use futures::StreamExt;
use serde::Deserialize;
use serde::de::DeserializeOwned;
use tokio::sync::{Mutex, mpsc, oneshot};
use tokio::task::JoinHandle;

use crate::audio::AudioFrame;
use crate::state::{MeetingKey, Platform};
use crate::teams;
use crate::teams::JoinStage;

// ---------------------------------------------------------------------------
// ============================ SELECTOR TABLE ===============================
// ---------------------------------------------------------------------------
//
//  *** THIS IS THE ONLY PLACE IN THE CODEBASE THAT KNOWS GOOGLE MEET'S DOM. ***
//
//  Teams' DOM lives in `src/teams.rs`, in its own table with the same rules.
//  Nothing Teams-shaped belongs below.
//
//  Google reshuffles this markup a few times a year. When the bot stops
//  joining, stops noticing that it was admitted, or stops noticing that the
//  call ended, the fix belongs HERE and nowhere else.
//
//  Rules for editing:
//    * Anchor on `aria-label`, `role`, `data-*` and visible ENGLISH text. The
//      class names Meet ships are minified and rotate per release — they are
//      worthless as anchors. This is also why `join()` always navigates with
//      `?hl=en`: it pins the UI language so the text anchors below hold no
//      matter what locale the host machine is in.
//    * Every entry is a CANDIDATE LIST tried in order, first match wins. Add
//      the new variant at the FRONT and keep the old one — Meet rolls changes
//      out gradually and both variants are live for weeks.
//    * Text entries are matched case-insensitively, whitespace-collapsed, and
//      with curly apostrophes folded to `'`, against both `aria-label` and
//      `textContent`. Write them the way the UI shows them.
//    * Text entries match on prefix, so the shortest unambiguous phrase is the
//      most durable choice.
//
// ---------------------------------------------------------------------------
/// Sentinel in the join error when the bot found only `Switch here`, i.e. this
/// identity is already in the call from another device. `session.rs` matches on
/// it to finish as an operational skip rather than a recorder failure.
pub const SESSION_ALREADY_PRESENT: &str = "identity already present in the call";

pub mod selectors {
    /// **Pre-join / green room.** The guest display-name text field, i.e. the
    /// "Your name" box shown to a not-signed-in visitor before they may knock.
    /// If this list goes stale the bot joins under the browser default name.
    pub const NAME_INPUT: &[&str] = &[
        "input[aria-label='Your name']",
        "input[placeholder='Your name']",
        "input[aria-label*='name' i][type='text']",
        "input[jsname][type='text']",
    ];

    /// **Pre-join.** Passcode / meeting-password field. Google Meet does not use
    /// one (it is a Teams concept), but the platform-agnostic join path fills it
    /// when the caller supplied a passcode and a field happens to be present.
    pub const PASSCODE_INPUT: &[&str] = &[
        "input[aria-label*='passcode' i]",
        "input[aria-label*='password' i]",
        "input[type='password']",
    ];

    /// **Pre-join.** The primary affirmative button. `Ask to join` is the guest
    /// path (waiting room), `Join now` appears when the bot is pre-admitted,
    /// `Join anyway` when Meet complains that no camera/mic was found, and
    /// `Switch here` when Meet thinks this identity is already in the call from
    /// another tab. Ordered most-specific first because matching is by prefix.
    /// ORDER IS LOAD-BEARING. Matching walks this list in order and takes the
    /// first hit, so `Join here too` MUST precede the bare `Join` prefix — and
    /// `Switch here` must not be in this list at all (see
    /// SESSION_STEAL_BUTTON_TEXTS). When the same identity is already in the
    /// call, Meet renders BOTH `Join here too` and `Switch here`; on 20 Jul the
    /// bot took `Switch here` purely because it sat higher in this list, and
    /// ejected the operator from a live meeting. `Join here too` is the correct control
    /// there: it adds a second presence and leaves the first one alone.
    pub const JOIN_BUTTON_TEXTS: &[&str] = &[
        "Join here too",
        "Ask to join",
        "Join now",
        "Join anyway",
        // NO bare "Join". Prefix matching made it catch "Join and use a phone
        // for audio" — a control that lives in the expanded "Other ways to join"
        // menu — which starts a phone-audio join and bounces the bot to
        // /landing?pli=1. Every legitimate control is named in full above.
    ];

    /// **Pre-join. NEVER CLICK.** `Switch here` is not a join control: it means
    /// this identity is already in the call from another device, and clicking it
    /// MOVES the meeting to this one and drops the other session.
    ///
    /// Since the bot signs in as the operator, clicking it ejects them from their own
    /// meeting. It did exactly that on 20 Jul during a live Marketplace scrum.
    /// Seeing this button means the operator is already in the room, which is precisely
    /// the case this recorder exists NOT to cover, so the session bails.
    pub const SESSION_STEAL_BUTTON_TEXTS: &[&str] = &["Switch here"];

    /// **Pre-join.** The collapsed disclosure that HIDES the join controls we
    /// actually want.
    ///
    /// When this identity is already in the call, Meet renders `Switch here` as
    /// the only visible control and files `Join here too` (plus Companion mode,
    /// Present, phone audio) inside a collapsed `Other ways to join` section.
    /// Collapsed children have a zero-size bounding box, so the matcher skips
    /// them and the bot falls through to `Switch here` -- which ejects the live
    /// session. Expand this BEFORE looking for a join control.
    pub const EXPANDER_BUTTON_TEXTS: &[&str] = &["Other ways to join"];

    /// **Pre-join.** Toggles that silence the bot before it enters.
    ///
    /// Chrome runs with `--use-fake-device-for-media-stream`, so its "camera" is
    /// a synthetic green test pattern — which is what everyone in the room sees
    /// on the bot's tile unless the camera is turned off first. The labels read
    /// `Turn off ...` only while the device is ON, so matching them is
    /// self-limiting: once off, they read `Turn on ...` and stop matching.
    pub const SELF_MUTE_BUTTON_TEXTS: &[&str] = &[
        "Turn off microphone",
        "Turn off camera",
    ];

    /// **Pre-join.** Present ONLY while the camera is still on (it reads
    /// `Turn on camera` once off). The join loop treats its presence as "camera
    /// still live" and refuses to click Join until it is gone — otherwise the
    /// synthetic green test pattern is broadcast to the room for the instant
    /// between joining and the mute landing. This is the deterministic gate that
    /// replaces the old click-and-hope, which latched after a single toggle and
    /// let a late-appearing camera control through.
    pub const CAMERA_LIVE_TEXTS: &[&str] = &["Turn off camera"];

    /// **Pre-join.** Buttons that clear the interstitial device / permission
    /// dialogs Meet throws before the green room is usable. Clicking any of
    /// these is always safe; none of them joins the call on its own.
    pub const DISMISS_BUTTON_TEXTS: &[&str] = &[
        "Continue without microphone and camera",
        "Continue without microphone",
        "Continue without camera",
        "Dismiss",
        "Got it",
        "Allow",
        "Close",
        "OK",
    ];

    /// **In call.** Presence of any of these means the bot is admitted and the
    /// call toolbar is rendered. This is the single most load-bearing entry in
    /// the table: it drives `WaitingRoom -> InCall`. If it goes stale the bot
    /// sits in the waiting room until `admission_timeout` and every meeting is
    /// recorded as `skipped_not_admitted`.
    pub const IN_CALL_MARKERS: &[&str] = &[
        "button[aria-label='Leave call']",
        "button[aria-label*='Leave call' i]",
        "button[aria-label*='Hang up' i]",
        "[data-call-ended='false']",
        "[data-meeting-code][data-participant-id]",
        "div[data-self-name]",
    ];

    /// **In call.** The hang-up control as a CSS anchor, clicked by
    /// [`super::MeetSession::leave`] when the text match below misses.
    pub const LEAVE_BUTTON: &[&str] = &[
        "button[aria-label='Leave call']",
        "button[aria-label*='Leave call' i]",
        "button[aria-label*='Hang up' i]",
    ];

    /// **In call.** Accessible text of the hang-up control. Tried before
    /// [`LEAVE_BUTTON`] because Meet localises the tooltip but keeps the
    /// accessible name stable under `?hl=en`.
    pub const LEAVE_BUTTON_TEXTS: &[&str] = &["Leave call", "Hang up", "End call"];

    /// **In call.** The participant-count readout. Read from `aria-label` first
    /// (e.g. `"People, 3 participants"`), then `textContent`; the first integer
    /// found wins. A stale entry falls back to counting participant tiles, and
    /// failing that disables the lonely-bot exit (the call then ends on the
    /// call-ended DOM or on the host removing the bot).
    pub const PARTICIPANT_COUNT: &[&str] = &[
        "button[aria-label*='participant' i]",
        "button[aria-label*='People' i]",
        "div[aria-label*='participant' i]",
        "[data-participant-count]",
    ];

    /// **In call.** Participant tiles, counted as the fallback for
    /// [`PARTICIPANT_COUNT`]. Must be one element per human, bot included.
    pub const PARTICIPANT_TILES: &[&str] =
        &["[data-participant-id]", "[data-requested-participant-id]"];

    /// **In call.** Elements Meet marks as "this person is talking right now".
    /// Consumed by `assets/capture.js` to stamp a speaker onto each audio frame.
    /// Purely best-effort: when it goes stale every segment is attributed to
    /// `Unknown`, which the Python client renders fine.
    pub const SPEAKING_INDICATORS: &[&str] = &[
        "[data-is-speaking='true']",
        "[aria-label*='is speaking' i]",
        "[data-participant-id][data-is-speaking]",
    ];

    /// **In call.** The tile container a [`SPEAKING_INDICATORS`] hit sits inside;
    /// used to walk up from the indicator to something that carries a name.
    pub const SPEAKER_TILE: &[&str] = &["[data-participant-id]", "[data-self-name]"];

    /// **In call.** Where a participant's display name lives inside their tile.
    /// The pseudo-selector `:self` means "read `data-participant-name` /
    /// `aria-label` off the tile element itself".
    pub const SPEAKER_NAME: &[&str] = &[
        "[data-self-name]",
        "[data-participant-name]",
        ":self",
        ".notranslate",
    ];

    /// **Waiting room.** Visible text proving the bot has knocked and is waiting.
    /// Informational only — admission is decided by [`IN_CALL_MARKERS`] and
    /// [`DENIED_TEXTS`] — but it tells `join()` the click already landed.
    pub const WAITING_TEXTS: &[&str] = &[
        "Asking to be let in",
        "Asking to join",
        "You'll join the call when someone lets you in",
        "Waiting for someone to let you in",
    ];

    /// **Waiting room, terminal.** The host said no, or Meet refused outright.
    /// Drives `WaitingRoom -> Completed` with zero segments (`SPEC.md` §0.1) —
    /// deliberately NOT a failure.
    pub const DENIED_TEXTS: &[&str] = &[
        "You can't join this call",
        "Your request to join was denied",
        "No one responded to your request to join",
        "Someone in the call denied your request to join",
        "You can't join this video call",
        "Check your meeting code",
    ];

    /// **In call, terminal.** The call is over / the bot is out of it, without
    /// anyone having ejected it. Drives `InCall -> Completed`.
    pub const CALL_ENDED_TEXTS: &[&str] = &[
        "You left the meeting",
        "You've left the meeting",
        "Return to home screen",
        "This call ended",
        "The call ended",
        "Your call has ended",
    ];

    /// **In call, terminal.** The host kicked the bot. Drives `InCall ->
    /// Stopped`, i.e. whatever audio was captured is kept and the client gets a
    /// truncation banner. Checked BEFORE [`CALL_ENDED_TEXTS`] because Meet
    /// renders both kinds of copy on the same screen.
    pub const REMOVED_TEXTS: &[&str] = &[
        "You've been removed from the meeting",
        "You have been removed from the meeting",
        "You were removed from the meeting",
        "Someone removed you from the meeting",
    ];
}

// ---------------------------------------------------------------------------
// Tunables
// ---------------------------------------------------------------------------

/// Name of the `Runtime.addBinding` channel `assets/capture.js` pushes through.
const AUDIO_BINDING: &str = "meetbotAudio";

/// Samples per pushed frame. 8000 @ 16 kHz = 500 ms: small enough that the VAD
/// in `audio.rs` still cuts tight utterance boundaries, large enough that the
/// CDP channel is not the bottleneck.
const FRAME_SAMPLES: usize = 8_000;

/// How often the admission / in-call watchers re-read the DOM.
const POLL_INTERVAL: Duration = Duration::from_secs(2);

/// Budget for the whole pre-join dance (navigate, dismiss, name, click).
const JOIN_TIMEOUT: Duration = Duration::from_secs(90);

/// How long to let Teams' redirect chain run before probing it.
///
/// `/meet/<id>` performs FIVE document navigations (two of them HTTP 302s, one
/// a silent MSAL `prompt=none` probe that always fails for an anonymous guest)
/// before settling on `/light-meetings/launch`. Measured at ~10-15 s. Probing
/// through that window mostly evaluates against pages that are mid-navigation,
/// so we sleep through the bulk of it and let the join loop poll the rest —
/// [`JOIN_TIMEOUT`] still bounds the whole thing.
const TEAMS_REDIRECT_SETTLE: Duration = Duration::from_secs(8);

/// Sent as `Network.setExtraHTTPHeaders{Accept-Language}`.
///
/// Teams has no `?hl=en` equivalent: its UI language follows this header, so
/// without it the English text anchors in `teams::selectors` stop matching on a
/// non-English host and the failure is invisible.
const ACCEPT_LANGUAGE: &str = "en-US,en";

/// Frames-channel back-pressure ceiling: dropping a frame beats stalling the
/// browser's audio thread.
const FRAME_SEND_TIMEOUT: Duration = Duration::from_millis(500);

/// How long to wait for the page's AudioContext clock to advance before calling
/// the tap dead. Generous: a just-admitted Meet tab is still settling.
const AUDIO_CLOCK_TIMEOUT: Duration = Duration::from_secs(15);
/// Gap between the two `currentTime` probes.
const AUDIO_CLOCK_POLL: Duration = Duration::from_millis(250);

/// How long `report_tab_capture` waits for the `getDisplayMedia` promise to
/// settle before reporting whatever state it is in. Purely diagnostic — nothing
/// blocks on the outcome.
const TAB_CAPTURE_SETTLE: Duration = Duration::from_secs(10);

/// How often `watch_call` logs a line of tap stats. In `POLL_INTERVAL` ticks,
/// so 30 x 2s = one line a minute. Cheap, and it is the only in-call witness
/// that the bot is hearing anything at all.
const STATS_LOG_EVERY_TICKS: u32 = 30;

/// How often the deafness probe evaluates in-page stats, in `POLL_INTERVAL`
/// ticks. 5 x 2s = every 10s, which still gives the 180s grace 18 samples while
/// costing a fifth of the CDP traffic the per-tick version did.
const DEAF_PROBE_EVERY_TICKS: u32 = 5;

/// Peak amplitude at or below this counts as silence. Must match
/// `SILENCE_FLOOR` in `assets/capture.js`.
pub const SILENCE_FLOOR: f64 = 0.0005;

/// How long the bot tolerates being in a room WITH other people while every
/// captured sample is silent.
///
/// This is the 5 Aug 2026 failure: admitted, tap running, clock advancing, and
/// `attachStreams()` matching nothing, so the bot recorded 37 minutes of pure
/// silence and reported `completed`. Sitting there is strictly worse than
/// leaving, because a single Google profile means the parked bot also blocks
/// every meeting dispatched behind it. Bail out loudly instead.
///
/// Generous on purpose: only counted once `seen_others` is set, so the early
/// join into an empty room never trips it.
///
/// Re-tuned from 180s once tab capture landed. 180s was sized for a bug that
/// made EVERY call deaf, where firing fast was pure upside. It is the wrong
/// number for a working tap: a room where people are present but nobody speaks
/// for three minutes is an ordinary meeting — someone screen-sharing in
/// silence, a room waiting on a late chair — and killing it would turn a lost
/// three minutes into a lost meeting. Ten minutes of continuous silence with
/// others in the room is still unambiguously broken, and the deaf case is now
/// diagnosable in seconds from `tab_state` in the stats line rather than
/// needing the watchdog to surface it.
/// How long the pulse reader may produce no new frame before the call is
/// failed.
///
/// Distinct from [`DEAF_IN_CALL_GRACE`], which is about SILENCE: a stall is a
/// frame counter that has stopped advancing at all, which means the audio
/// server stopped answering rather than the room going quiet. It needs no grace
/// for an empty room, so it is much shorter: `pa_simple_read` returns every
/// 200 ms in health, so 30 s of nothing is already far past any jitter.
const PULSE_STALL_GRACE: Duration = Duration::from_secs(30);

const DEAF_IN_CALL_GRACE: Duration = Duration::from_secs(600);

/// Reads whichever tap is installed. `capture.js` and `capture_ws.js` expose
/// different hooks and only one is ever injected, so probe for both rather than
/// threading the transport choice down into `watch_call`.
const TAP_STATS_EXPR: &str = "(function(){\
    if (window.__meetbotCaptureStats) return window.__meetbotCaptureStats();\
    if (window.__meetbotWsCapture) return window.__meetbotWsCapture.stats();\
    return {};\
})()";

/// Budget for `Browser::connect` in attach mode.
///
/// `Browser::connect` builds its own reqwest client with **no** timeout to fetch
/// `/json/version` off the CDP endpoint. A port that accepts TCP but never
/// completes the HTTP/WebSocket handshake (a wedged `tln-browser.service`, a
/// port taken over by something else) therefore pends forever, and the session
/// row sits in `joining` until the process is restarted. Bounding it turns that
/// into an ordinary `Joining -> Failed`.
const ATTACH_CONNECT_TIMEOUT: Duration = Duration::from_secs(20);

/// Hard wall-clock ceiling on the in-call phase, four hours by default.
///
/// `admission_timeout` bounds the waiting room; before this constant existed
/// nothing at all bounded the call itself. The lonely-bot exit is not a
/// substitute: it depends on [`selectors::PARTICIPANT_COUNT`] *or*
/// [`selectors::PARTICIPANT_TILES`] resolving to a number, and those two drift
/// together (both are keyed off `data-participant-id`). If they go stale while
/// [`selectors::IN_CALL_MARKERS`] still matches, `probe.participants` is `None`,
/// `alone_since` is reset on every poll, and the lonely-bot exit silently
/// disables itself — one Google UI release strands every bot in an endless call,
/// each holding a live Chrome, a concurrency permit and a non-terminal DB row.
///
/// Override per-process with `MEETBOT_MAX_CALL_MIN` (minutes), or per-session
/// with [`MeetSession::set_max_call_duration`]. Hitting the cap yields
/// [`CallExit::MeetingEnded`], i.e. a normal `completed` session with whatever
/// audio was captured — not a failure.
pub const DEFAULT_MAX_CALL_DURATION: Duration = Duration::from_secs(4 * 60 * 60);

/// Env override for [`DEFAULT_MAX_CALL_DURATION`], in whole minutes.
const MAX_CALL_ENV: &str = "MEETBOT_MAX_CALL_MIN";

/// Filename prefix of the throwaway Chrome profiles this module creates under
/// `std::env::temp_dir()`. Also what [`sweep_stale_profiles`] matches on.
const PROFILE_PREFIX: &str = "meetbot-chrome-";

/// A profile directory older than this is assumed to belong to a dead process
/// and is swept. Comfortably longer than any single launch handshake, and
/// deliberately shorter than [`DEFAULT_MAX_CALL_DURATION`] is long — a live
/// session keeps its profile mtime fresh because Chrome writes to it.
const PROFILE_STALE_AFTER: Duration = Duration::from_secs(6 * 60 * 60);

/// Last-resort User-Agent, used only when the real browser version cannot be
/// read. Prefer [`user_agent_for_binary`] / [`strip_headless_token`], which
/// derive the UA from the browser actually running.
///
/// LANDMINE — the UA major version MUST match the running binary's. Chromium's
/// own headless UA contains the token `HeadlessChrome/...`, which Meet uses to
/// serve a degraded (audio-less) page, so an override IS required. But the
/// override may only rewrite the *product token*: it can never rewrite
/// `navigator.userAgentData` / the `Sec-CH-UA` Client-Hints headers, which
/// always report the browser's real platform and real major version. Meet's
/// anti-abuse cross-checks the two.
///
/// Measured 2026-07-19 on a live meeting: the `HeadlessChrome` token alone is
/// fatal (refused with no green room), while a merely *stale* version string
/// (`Chrome/128` on a Chrome 149 binary) still reached the green room. So the
/// override is mandatory but the version skew is, today, survivable — Meet is
/// evidently lenient about it. Do not rely on that: vexa hit the strict version
/// of this check and documented it (`vexa-bot/dist/constans.js`, stale
/// `Chrome/129` on a Chrome 141 bundle blocked every join), and a cross-platform
/// override — claiming Windows while WebGL and fonts still read Linux — is
/// reliably fatal. Deriving the version costs one `--version` exec and removes
/// the whole class of failure, so derive it rather than hardcoding.
const FALLBACK_USER_AGENT: &str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36";

/// Rewrites `HeadlessChrome/<v>` to `Chrome/<v>` in a User-Agent string,
/// leaving the version and every other token untouched.
///
/// This is the only safe way to de-headless a UA: it keeps the major version in
/// lockstep with the Client-Hints the browser emits, so there is nothing for
/// Meet's anti-abuse to catch. See the LANDMINE on [`FALLBACK_USER_AGENT`].
fn strip_headless_token(ua: &str) -> String {
    ua.replace("HeadlessChrome/", "Chrome/")
}

/// Builds a self-consistent desktop UA from the version a Chromium binary
/// reports on `--version`.
///
/// Used to seed the `--user-agent` launch flag, which has to be decided before
/// the browser exists (so CDP is not yet available to ask). Returns `None` if
/// the binary cannot be executed or prints something unparseable, in which case
/// the caller falls back to [`FALLBACK_USER_AGENT`] and the post-launch CDP
/// correction is what actually saves the session.
fn user_agent_for_binary(path: &std::path::Path) -> Option<String> {
    let out = std::process::Command::new(path).arg("--version").output().ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    // "Google Chrome for Testing 149.0.7827.55" / "Chromium 141.0.7390.37".
    let version = text
        .split_whitespace()
        .find(|tok| tok.split('.').count() == 4 && tok.starts_with(|c: char| c.is_ascii_digit()))?;
    Some(format!(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) \
         Chrome/{version} Safari/537.36"
    ))
}

// ---------------------------------------------------------------------------
// Public types (SPEC.md §7)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct BrowserOptions {
    pub chromium_path: PathBuf,
    pub headless: bool,
    /// Attach to ws://127.0.0.1:{port} instead of launching.
    pub attach_cdp_port: Option<u16>,
    pub user_data_dir: Option<PathBuf>,
    /// Signed-in Chrome profile to seed each session's throwaway profile from.
    ///
    /// Anonymous guests lose: Meet runs a bot check on the knock itself ("System
    /// info will be sent to confirm you're not a bot") and auto-declines ours
    /// ~1.5s later. Rather than racing Google's fingerprinting forever, the bot
    /// joins as an *invited participant* — Meet's own green room offers exactly
    /// this ("sign in with the Google account your host invited").
    ///
    /// The template is COPIED, never used in place: Chrome locks a
    /// `user-data-dir`, so concurrent bots sharing one profile would fight over
    /// it, and a session must not be able to mutate or invalidate the stored
    /// login. Treat this path as read-only.
    pub profile_template: Option<PathBuf>,
    pub window_size: (u32, u32), // (1280, 720)
    /// PulseAudio sink Chrome must play into, exported as `PULSE_SINK`.
    ///
    /// `None` leaves Chrome on the server's default sink. With the Pulse
    /// transport that default is the WRONG answer on WSLg: it is `RDPSink`, the
    /// bridge to Windows audio, and feeding a bot's meetings through it overran
    /// its queue and took the whole audio server down on 6 Aug 2026. Set this
    /// to a dedicated null sink (see [`crate::pulse::ensure_null_sink`]).
    pub pulse_sink: Option<String>,
}

impl Default for BrowserOptions {
    fn default() -> Self {
        Self {
            chromium_path: PathBuf::from(
                // Playwright's Chromium in the current user's cache. Overridden by
                // `chromium_path` in config.toml; this is only the last resort.
                &format!(
                    "{}/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
                    std::env::var("HOME").unwrap_or_else(|_| "/root".into())
                ),
            ),
            headless: true,
            attach_cdp_port: None,
            user_data_dir: None,
            profile_template: None,
            window_size: (1280, 720),
            pulse_sink: None,
        }
    }
}

/// Result of waiting at the waiting-room gate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Admission {
    /// In-call DOM detected.
    Admitted,
    /// `admission_timeout` elapsed — session must finish `completed`, 0 segments.
    TimedOut,
    /// Host denied / "You can't join this call" — also finishes `completed`.
    Denied,
}

/// Why the in-call watch loop returned.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallExit {
    /// Meeting ended or the bot was left alone past the grace period.
    MeetingEnded,
    /// Host removed the bot mid-call.
    RemovedByHost,
    /// `Stop` command observed via the cancel token.
    Stopped,
    /// Page/CDP broke.
    BrowserError(String),
}

/// Same rules as `Platform::meeting_url`, re-exported for callers that only
/// have loose parts.
pub fn meeting_url(platform: Platform, native_id: &str) -> String {
    platform.meeting_url(native_id)
}

// ---------------------------------------------------------------------------
// ======================== PLATFORM DISPATCH (SPEC §1.3.1) ==================
// ---------------------------------------------------------------------------
//
// Both supported platforms share ONE state machine, one audio tap, one
// admission timeout, one wall-clock cap and one set of `CallExit` variants.
// The only thing that varies is which selector table the shared JS templates
// are filled from — and that variation is confined to the two functions below.
//
// Teams was a hard 400 at `POST /bots` until the `coords` redirect was
// understood (see `crate::teams::url_contains_code` for the root cause). It is
// now driven for real; `SPEC.md` §1.3.1 documents the supported behaviour.
// ---------------------------------------------------------------------------

/// Which selector lists the shared [`js::PROBE`] template is filled from.
///
/// Every field is a candidate list. Empty is meaningful and expected: Meet has
/// no CSS-anchored denial surface, Teams has no verified participant counter.
struct ProbeSpec {
    /// Presence proves the bot is in the call.
    in_call: &'static [&'static str],
    /// Presence VETOES an `in_call` claim, whatever `in_call` matched. This is
    /// what stops a selector that exists on both the pre-join and in-call
    /// surfaces from reporting a call the bot never joined.
    not_in_call: &'static [&'static str],
    tiles: &'static [&'static str],
    counters: &'static [&'static str],
    waiting_texts: &'static [&'static str],
    waiting_sel: &'static [&'static str],
    denied_texts: &'static [&'static str],
    denied_sel: &'static [&'static str],
    ended_texts: &'static [&'static str],
    removed_texts: &'static [&'static str],
    /// Named CSS groups reported back in `PageProbe::diag`, so a failed join
    /// logs which anchors matched instead of just "it did not work".
    diag: Vec<(&'static str, &'static [&'static str])>,
}

/// The selector lists for one platform. **The only dispatch point between the
/// two tables** — add a platform here, not in the state machine.
fn probe_spec(platform: Platform) -> ProbeSpec {
    match platform {
        Platform::GoogleMeet => ProbeSpec {
            in_call: selectors::IN_CALL_MARKERS,
            not_in_call: &[],
            tiles: selectors::PARTICIPANT_TILES,
            counters: selectors::PARTICIPANT_COUNT,
            waiting_texts: selectors::WAITING_TEXTS,
            waiting_sel: &[],
            denied_texts: selectors::DENIED_TEXTS,
            denied_sel: &[],
            ended_texts: selectors::CALL_ENDED_TEXTS,
            removed_texts: selectors::REMOVED_TEXTS,
            diag: MEET_DIAG_GROUPS.to_vec(),
        },
        Platform::Teams => ProbeSpec {
            in_call: teams::selectors::IN_CALL_ROOT,
            not_in_call: teams::selectors::NOT_IN_CALL,
            tiles: teams::selectors::PARTICIPANT_TILES,
            counters: teams::selectors::PARTICIPANT_COUNT,
            waiting_texts: teams::selectors::LOBBY_TEXTS,
            waiting_sel: teams::selectors::LOBBY_ROOT,
            denied_texts: teams::selectors::DENIED_TEXTS,
            // The verified rejection surface is a DOM subtree, not a phrase.
            // Teams renders the SAME copy for a bad id, a wrong passcode and an
            // ended meeting, so the text is logged and never branched on.
            denied_sel: teams::selectors::RETRY_SCREEN,
            ended_texts: teams::selectors::CALL_ENDED_TEXTS,
            removed_texts: teams::selectors::REMOVED_TEXTS,
            diag: teams::diag_groups(),
        },
    }
}

/// Diagnostic CSS groups for Meet, the counterpart of [`crate::teams::DIAG_GROUPS`].
const MEET_DIAG_GROUPS: &[(&str, &[&str])] = &[
    ("name_input", selectors::NAME_INPUT),
    ("in_call_markers", selectors::IN_CALL_MARKERS),
    ("participant_tiles", selectors::PARTICIPANT_TILES),
    ("leave_button", selectors::LEAVE_BUTTON),
];

/// Speaker-attribution selectors for the injected audio tap.
fn speaker_selectors(
    platform: Platform,
) -> (&'static [&'static str], &'static [&'static str], &'static [&'static str]) {
    match platform {
        Platform::GoogleMeet => (
            selectors::SPEAKING_INDICATORS,
            selectors::SPEAKER_TILE,
            selectors::SPEAKER_NAME,
        ),
        Platform::Teams => (
            teams::selectors::SPEAKING_INDICATORS,
            teams::selectors::SPEAKER_TILE,
            teams::selectors::SPEAKER_NAME,
        ),
    }
}

/// Hang-up anchors: (accessible texts, CSS selectors).
fn leave_selectors(platform: Platform) -> (&'static [&'static str], &'static [&'static str]) {
    match platform {
        Platform::GoogleMeet => (selectors::LEAVE_BUTTON_TEXTS, selectors::LEAVE_BUTTON),
        Platform::Teams => (
            teams::selectors::LEAVE_BUTTON_TEXTS,
            teams::selectors::LEAVE_BUTTON,
        ),
    }
}

// ---------------------------------------------------------------------------
// JS templates
//
// Every template returns an OBJECT, never a bare `null`: CDP reports a null
// return value as "no value at all" and `EvaluationResult::into_value` would
// then fail instead of yielding `None`.
// ---------------------------------------------------------------------------

mod js {
    /// The audio tap. `__MEETBOT_*__` tokens are substituted from the selector
    /// table before evaluation.
    pub const CAPTURE: &str = include_str!("../assets/capture.js");

    /// The WebSocket-transport tap (`state::CaptureTransport::WebSocket`). Same
    /// selector tokens as [`CAPTURE`], plus `__MEETBOT_INGEST_URL__`.
    pub const CAPTURE_WS: &str = crate::audio::CAPTURE_WS_JS;

    /// What the DOM offers for speaker attribution, for `doctor`.
    pub const SPEAKER_MARKUP: &str = r#"(() => {
  'use strict';
  var SPEAKING = __MEETBOT_SPEAKING_SELECTORS__;
  var TILES = __MEETBOT_SPEAKER_TILE_SELECTORS__;
  var NAMES = __MEETBOT_SPEAKER_NAME_SELECTORS__;

  function count(sels) {
    var out = {};
    for (var i = 0; i < sels.length; i++) {
      try { out[sels[i]] = document.querySelectorAll(sels[i]).length; }
      catch (e) { out[sels[i]] = -1; }
    }
    return out;
  }

  // Every data-* / jsname attribute on the page, most common first. A drifted
  // attribute name shows up here as "the one that looks like a participant key
  // but is not the one we are asking for".
  var attrs = {};
  var all = document.querySelectorAll('*');
  for (var i = 0; i < all.length; i++) {
    var a = all[i].attributes;
    for (var j = 0; j < a.length; j++) {
      var n = a[j].name;
      if (n.indexOf('data-') === 0 || n === 'jsname') {
        attrs[n] = (attrs[n] || 0) + 1;
      }
    }
  }
  var top = Object.keys(attrs).map(function (k) { return [k, attrs[k]]; });
  top.sort(function (x, y) { return y[1] - x[1]; });

  // aria-labels that mention speech state at all, which is where Meet has put
  // the speaking signal in the past.
  var aria = [];
  var labelled = document.querySelectorAll('[aria-label]');
  for (var k = 0; k < labelled.length && aria.length < 25; k++) {
    var l = labelled[k].getAttribute('aria-label') || '';
    if (/speak|talk|mute|presenting|voice/i.test(l)) { aria.push(l.slice(0, 60)); }
  }

  // Where does a NAME actually live now?
  //
  // Counting selectors says what stopped matching; it cannot say what to use
  // instead. So walk up from anything whose text looks like a participant name
  // and record the ancestor chain's tags, classes and attributes. That is the
  // evidence a replacement selector gets written from.
  function chain(el) {
    var out = [];
    var node = el;
    for (var d = 0; d < 4 && node && node.tagName; d++) {
      var attrs = [];
      for (var i = 0; i < node.attributes.length; i++) {
        var a = node.attributes[i];
        var v = a.value.length > 30 ? a.value.slice(0, 30) + '...' : a.value;
        attrs.push(a.name + '=' + v);
      }
      out.push(node.tagName.toLowerCase() + ' {' + attrs.join(' ') + '}');
      node = node.parentElement;
    }
    return out;
  }

  var nameSamples = [];
  var cands = document.querySelectorAll('.notranslate, [data-participant-name], [data-self-name]');
  for (var c = 0; c < cands.length && nameSamples.length < 6; c++) {
    var txt = (cands[c].textContent || '').trim();
    if (txt && txt.length < 60) {
      nameSamples.push({ text: txt, chain: chain(cands[c]) });
    }
  }

  // Does the replacement name source actually resolve? This is the check that
  // the aria-label hook is real rather than plausible.
  var moreOptions = [];
  var mo = document.querySelectorAll('[aria-label*="More options for" i]');
  for (var m = 0; m < mo.length && moreOptions.length < 10; m++) {
    var lbl = mo[m].getAttribute('aria-label') || '';
    moreOptions.push(lbl.replace(/^more options for\s+/i, ''));
  }

  return {
    moreOptionsNames: moreOptions,
    speaking: count(SPEAKING),
    tiles: count(TILES),
    names: count(NAMES.filter(function (n) { return n !== ':self'; })),
    topAttrs: top.slice(0, 12),
    ariaSamples: aria,
    nameSamples: nameSamples
  };
})()"#;

    /// Active-speaker name only, for the PulseAudio transport.
    ///
    /// Takes the same three selector tokens as [`CAPTURE`], so it is filled
    /// from the one selector table like everything else here. Returns
    /// `{speaker: string|null}`; `null` whenever Meet has reshuffled the
    /// speaking indicator, which degrades a transcript line to "Unknown"
    /// rather than failing the call.
    pub const SPEAKER: &str = r#"(() => {
  'use strict';
  var SPEAKING_SELECTORS = __MEETBOT_SPEAKING_SELECTORS__;
  var SPEAKER_NAME_SELECTORS = __MEETBOT_SPEAKER_NAME_SELECTORS__;
  var SPEAKER_TILE_SELECTORS = __MEETBOT_SPEAKER_TILE_SELECTORS__;

  function clean(text) {
    if (!text) { return null; }
    var t = String(text).replace(/\s+/g, ' ').trim();
    if (!t || t.length > 80) { return null; }
    return t;
  }

  // Meet's per-participant "More options for <Name>" button.
  //
  // As of 6 Aug 2026 this is the only reliable place a participant's name still
  // lives. `data-participant-id` and `data-participant-name` both return 0 hits
  // in a live call: measured with `meetbot doctor`, which dumps the in-call DOM.
  // That is why the ExampleVendor meeting produced 124 utterances all labelled
  // "Unknown" -- the speaking indicator may well have fired, but nameFor() had
  // nothing left to read, so every lookup returned null.
  var MORE_OPTIONS = /^more options for\s+/i;

  function fromMoreOptions(root) {
    var btns;
    try { btns = root.querySelectorAll('[aria-label*="More options for" i]'); }
    catch (e) { return null; }
    for (var i = 0; i < btns.length; i++) {
      var label = btns[i].getAttribute('aria-label') || '';
      if (MORE_OPTIONS.test(label)) {
        var name = clean(label.replace(MORE_OPTIONS, ''));
        if (name) { return name; }
      }
    }
    return null;
  }

  function nameFor(node) {
    var roots = [node];
    for (var s = 0; s < SPEAKER_TILE_SELECTORS.length; s++) {
      try {
        var tile = node.closest(SPEAKER_TILE_SELECTORS[s]);
        if (tile) { roots.push(tile); }
      } catch (e) { /* selector unsupported by this Chrome */ }
    }
    if (node.parentElement) { roots.push(node.parentElement); }

    // Walk UP rather than trusting a tile selector.
    //
    // The old code could only look inside whatever `SPEAKER_TILE_SELECTORS`
    // matched, so when Google dropped that attribute the name became
    // unreachable even though it was still on the page a few levels up. Climbing
    // the ancestors costs nothing and does not depend on any attribute name
    // surviving the next Meet release.
    var up = node;
    for (var d = 0; d < 6 && up; d++) {
      var viaMore = fromMoreOptions(up);
      if (viaMore) { return viaMore; }
      up = up.parentElement;
    }
    for (var r = 0; r < roots.length; r++) {
      var root = roots[r];
      for (var i = 0; i < SPEAKER_NAME_SELECTORS.length; i++) {
        var sel = SPEAKER_NAME_SELECTORS[i];
        if (sel === ':self') {
          var own = clean(root.getAttribute && (root.getAttribute('data-participant-name') || root.getAttribute('aria-label')));
          if (own) { return own; }
          continue;
        }
        try {
          var found = root.querySelector(sel);
          if (found) {
            var got = clean(found.getAttribute('aria-label') || found.textContent);
            if (got) { return got; }
          }
        } catch (e) { /* selector unsupported */ }
      }
    }
    return null;
  }

  // Counts, not just the answer.
  //
  // The ExampleVendor call on 6 Aug produced 124 utterances all labelled
  // "Unknown", and a bare null cannot say whether the speaking indicator
  // matched nothing (Google reshuffled the markup) or matched fine and the
  // name lookup failed. Those need opposite fixes, so report both: `hits` is
  // per SPEAKING_SELECTORS entry, `tiles` is how many participant tiles exist
  // at all, which separates "wrong selector" from "nobody is speaking".
  var hits = [];
  var speaker = null;
  for (var i = 0; i < SPEAKING_SELECTORS.length; i++) {
    var nodes;
    try { nodes = document.querySelectorAll(SPEAKING_SELECTORS[i]); } catch (e) { hits.push(-1); continue; }
    hits.push(nodes.length);
    if (speaker === null) {
      for (var j = 0; j < nodes.length; j++) {
        var name = nameFor(nodes[j]);
        if (name) { speaker = name; break; }
      }
    }
  }
  var tiles = 0;
  for (var t = 0; t < SPEAKER_TILE_SELECTORS.length; t++) {
    try { tiles += document.querySelectorAll(SPEAKER_TILE_SELECTORS[t]).length; } catch (e) {}
  }
  return { speaker: speaker, hits: hits, tiles: tiles };
})()"#;

    /// Shared prelude: the text normaliser every text-matching probe uses. Folds
    /// whitespace and curly apostrophes so the plain-ASCII phrases in the
    /// selector table match Google's typographic markup.
    pub const NORM: &str = r#"
      var __norm = function (s) {
        return String(s == null ? '' : s)
          .replace(/[‘’ʼ]/g, "'")
          .replace(/\s+/g, ' ')
          .trim()
          .toLowerCase();
      };
    "#;

    /// One DOM read that answers every question the state machine asks.
    /// Returns
    /// `{ url, inCall, waiting, denied, ended, removed, participants, diag }`.
    ///
    /// `diag` maps each named selector group to the first candidate that
    /// matched, or `null` when none did. It is what makes a 22:00 failure
    /// actionable: the log line names the group and the exact selector rather
    /// than reporting a silent non-admission.
    pub const PROBE: &str = r#"
(() => {
  __NORM__
  var IN_CALL = __IN_CALL__;
  var NOT_IN_CALL = __NOT_IN_CALL__;
  var TILES = __TILES__;
  var COUNTERS = __COUNTERS__;
  var WAITING = __WAITING__;
  var WAITING_SEL = __WAITING_SEL__;
  var DENIED = __DENIED__;
  var DENIED_SEL = __DENIED_SEL__;
  var ENDED = __ENDED__;
  var REMOVED = __REMOVED__;
  var DIAG = __DIAG__;

  var firstMatch = function (sels) {
    for (var i = 0; i < sels.length; i++) {
      try { if (document.querySelector(sels[i])) return sels[i]; } catch (e) {}
    }
    return null;
  };
  var present = function (sels) { return firstMatch(sels) !== null; };

  var body = __norm(document.body ? document.body.innerText : '');
  var hasText = function (list) {
    for (var i = 0; i < list.length; i++) {
      if (body.indexOf(__norm(list[i])) !== -1) return list[i];
    }
    return null;
  };

  var participants = null;
  for (var i = 0; i < COUNTERS.length; i++) {
    var el = null;
    try { el = document.querySelector(COUNTERS[i]); } catch (e) { continue; }
    if (!el) continue;
    var src = (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '');
    var m = src.match(/\d+/);
    if (m) { participants = parseInt(m[0], 10); break; }
  }
  if (participants === null) {
    for (var j = 0; j < TILES.length; j++) {
      var nodes = null;
      try { nodes = document.querySelectorAll(TILES[j]); } catch (e) { continue; }
      if (nodes && nodes.length > 0) { participants = nodes.length; break; }
    }
  }

  var diag = {};
  for (var d = 0; d < DIAG.length; d++) { diag[DIAG[d][0]] = firstMatch(DIAG[d][1]); }

  // A selector that also lives on the pre-join / rejection surface cannot prove
  // admission. NOT_IN_CALL is the veto that keeps an over-broad IN_CALL entry
  // from making the bot record a call it is not in.
  var inCall = present(IN_CALL) && !present(NOT_IN_CALL);

  return {
    url: location.href,
    inCall: inCall,
    waiting: hasText(WAITING) || firstMatch(WAITING_SEL),
    denied: hasText(DENIED) || firstMatch(DENIED_SEL),
    ended: hasText(ENDED),
    removed: hasText(REMOVED),
    participants: participants,
    diag: diag
  };
})()
"#;

    /// Clicks the first visible, enabled control whose accessible name or text
    /// starts with one of `__TEXTS__`. Returns `{ clicked: <text>|null }`.
    /// Same matching rules as [`CLICK_BY_TEXT`], but reports without clicking.
    ///
    /// Needed for controls that must be DETECTED and never activated — see
    /// `selectors::SESSION_STEAL_BUTTON_TEXTS`, where clicking would eject a
    /// live participant.
    pub const FIND_BY_TEXT: &str = r#"
(() => {
  __NORM__
  // Material icon ligatures render as TEXT INSIDE the button, so a control's
  // textContent arrives as e.g. "add_to_queueJoin here too". Prefix matching
  // then misses it entirely -- which on 20 Jul made the bot fall through to
  // `Switch here` and eject the operator from a live meeting. The ligature is always a
  // lowercase/underscore run butted directly against the label's capital.
  var __delig = function (s) { return (s || '').replace(/^[a-z][a-z_]*(?=[A-Z])/, ''); };
  var TEXTS = __TEXTS__;
  var nodes = document.querySelectorAll(
    "button, [role='button'], [role='link'], [role='menuitem'], a[href], input[type='submit']"
  );
  for (var t = 0; t < TEXTS.length; t++) {
    var want = __norm(TEXTS[t]);
    if (!want) continue;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var rect = n.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      var label = __norm(n.getAttribute('aria-label'));
      if (!label) label = __norm(__delig(n.textContent));
      if (label === want || label.indexOf(want) === 0) {
        return { clicked: TEXTS[t] };
      }
    }
  }
  return { clicked: null };
})()
"#;

    pub const CLICK_BY_TEXT: &str = r#"
(() => {
  __NORM__
  // Material icon ligatures render as TEXT INSIDE the button, so a control's
  // textContent arrives as e.g. "add_to_queueJoin here too". Prefix matching
  // then misses it entirely -- which on 20 Jul made the bot fall through to
  // `Switch here` and eject the operator from a live meeting. The ligature is always a
  // lowercase/underscore run butted directly against the label's capital.
  var __delig = function (s) { return (s || '').replace(/^[a-z][a-z_]*(?=[A-Z])/, ''); };
  var TEXTS = __TEXTS__;
  var nodes = document.querySelectorAll(
    "button, [role='button'], [role='link'], [role='menuitem'], a[href], input[type='submit']"
  );
  for (var t = 0; t < TEXTS.length; t++) {
    var want = __norm(TEXTS[t]);
    if (!want) continue;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.disabled) continue;
      if (n.getAttribute('aria-disabled') === 'true') continue;
      var rect = n.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      var label = __norm(n.getAttribute('aria-label'));
      if (!label) label = __norm(__delig(n.textContent));
      if (label === want || label.indexOf(want) === 0) {
        try { n.click(); } catch (e) { continue; }
        return { clicked: TEXTS[t] };
      }
    }
  }
  return { clicked: null };
})()
"#;

    /// Clicks the first VISIBLE element matching any of `__SELS__`, returning
    /// `{ clicked: <selector>|null }`.
    ///
    /// Deliberately does **not** skip `disabled` / `aria-disabled` elements, in
    /// contrast to [`CLICK_BY_TEXT`]. Teams' `prejoin-join-button` never carries
    /// either attribute even with an empty name field (VERIFIED), so
    /// enabled-ness is not a usable readiness signal there — and refusing to
    /// click a control that is merely *marked* disabled would strand the join.
    pub const CLICK_BY_SELECTOR: &str = r#"
(() => {
  var SELS = __SELS__;
  for (var i = 0; i < SELS.length; i++) {
    var nodes = null;
    try { nodes = document.querySelectorAll(SELS[i]); } catch (e) { continue; }
    for (var k = 0; k < nodes.length; k++) {
      var n = nodes[k];
      var rect = n.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      try { n.click(); } catch (e) { continue; }
      return { clicked: SELS[i] };
    }
  }
  return { clicked: null };
})()
"#;

    /// Reads back the value of the first matching field.
    /// Returns `{ found: bool, value: string }`.
    pub const READ_FIELD: &str = r#"
(() => {
  var SELS = __SELS__;
  for (var i = 0; i < SELS.length; i++) {
    var el = null;
    try { el = document.querySelector(SELS[i]); } catch (e) { continue; }
    if (el) return { found: true, value: String(el.value == null ? '' : el.value) };
  }
  return { found: false, value: '' };
})()
"#;

    /// Sets the first matching field through the prototype's own value setter
    /// and fires `input`/`change`, so the framework behind Meet actually
    /// registers the change. Used only when native key-event typing did not
    /// stick. Returns `{ ok: bool }`.
    pub const SET_FIELD: &str = r#"
(() => {
  var SELS = __SELS__;
  var VALUE = __VALUE__;
  for (var i = 0; i < SELS.length; i++) {
    var el = null;
    try { el = document.querySelector(SELS[i]); } catch (e) { continue; }
    if (!el) continue;
    try {
      var proto = Object.getPrototypeOf(el);
      var desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) { desc.set.call(el, VALUE); } else { el.value = VALUE; }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true };
    } catch (e) { /* try the next candidate */ }
  }
  return { ok: false };
})()
"#;

    /// One-shot active-speaker read. Returns `{ found: bool, value: string }`.
    pub const ACTIVE_SPEAKER: &str = r#"
(() => {
  var SPEAKING = __SPEAKING__;
  var TILES = __TILES__;
  var NAMES = __NAMES__;
  var clean = function (t) {
    if (!t) return null;
    var s = String(t).replace(/\s+/g, ' ').trim();
    return (s && s.length < 80) ? s : null;
  };
  var nameFor = function (node) {
    var roots = [node];
    for (var i = 0; i < TILES.length; i++) {
      try { var tile = node.closest(TILES[i]); if (tile) roots.push(tile); } catch (e) {}
    }
    if (node.parentElement) roots.push(node.parentElement);
    for (var r = 0; r < roots.length; r++) {
      for (var j = 0; j < NAMES.length; j++) {
        var sel = NAMES[j];
        if (sel === ':self') {
          var own = roots[r].getAttribute
            ? clean(roots[r].getAttribute('data-participant-name') || roots[r].getAttribute('aria-label'))
            : null;
          if (own) return own;
          continue;
        }
        try {
          var el = roots[r].querySelector(sel);
          if (el) {
            var got = clean(el.getAttribute('aria-label') || el.textContent);
            if (got) return got;
          }
        } catch (e) {}
      }
    }
    return null;
  };
  for (var i = 0; i < SPEAKING.length; i++) {
    var nodes = null;
    try { nodes = document.querySelectorAll(SPEAKING[i]); } catch (e) { continue; }
    for (var k = 0; k < nodes.length; k++) {
      var name = nameFor(nodes[k]);
      if (name) return { found: true, value: name };
    }
  }
  return { found: false, value: '' };
})()
"#;

    /// Tears the audio tap down from inside the page. Returns `{ ok: bool }`.
    pub const STOP_CAPTURE: &str = r#"
(() => {
  if (typeof window.__meetbotStopCapture === 'function') {
    try { window.__meetbotStopCapture(); } catch (e) {}
    return { ok: true };
  }
  return { ok: false };
})()
"#;
}

// ---------------------------------------------------------------------------
// Wire shapes for the JS probes
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
struct PageProbe {
    #[serde(default)]
    url: String,
    #[serde(rename = "inCall", default)]
    in_call: bool,
    #[serde(default)]
    waiting: Option<String>,
    #[serde(default)]
    denied: Option<String>,
    #[serde(default)]
    ended: Option<String>,
    #[serde(default)]
    removed: Option<String>,
    #[serde(default)]
    participants: Option<i64>,
    /// Named selector group -> the first candidate that matched, or `None`.
    /// Purely diagnostic; nothing branches on it.
    #[serde(default)]
    diag: std::collections::BTreeMap<String, Option<String>>,
}

impl PageProbe {
    /// One-line rendering of the diag map for a log field: matched groups as
    /// `group=selector`, unmatched ones as `group=MISS`. Reading it is how a
    /// maintainer tells "never reached the pre-join screen" from "reached it and
    /// the name field moved".
    fn diag_line(&self) -> String {
        if self.diag.is_empty() {
            return "-".to_string();
        }
        self.diag
            .iter()
            .map(|(group, hit)| match hit {
                Some(sel) => format!("{group}={sel}"),
                None => format!("{group}=MISS"),
            })
            .collect::<Vec<_>>()
            .join(" ")
    }

    /// Whether a named diag group matched on this read.
    fn matched(&self, group: &str) -> bool {
        self.diag.get(group).is_some_and(Option::is_some)
    }
}

#[derive(Debug, Deserialize)]
struct ClickReply {
    #[serde(default)]
    clicked: Option<String>,
}

#[derive(Debug, Deserialize)]
struct FieldReply {
    #[serde(default)]
    found: bool,
    #[serde(default)]
    value: String,
}

#[derive(Debug, Deserialize)]
struct OkReply {
    #[serde(default)]
    ok: bool,
}

#[derive(Debug, Deserialize)]
struct StatusReply {
    #[serde(default)]
    status: String,
}

/// What `assets/capture_ws.js` returns from its IIFE (its `stats()` shape).
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WsStatsReply {
    #[serde(default)]
    attached_elements: u64,
    /// Set by the script only when `start()` threw.
    #[serde(default)]
    error: Option<String>,
}

/// The subset of either tap's stats hook that [`MeetSession::await_audio_clock`]
/// needs. Both scripts expose `ctxTime`/`ctxState`.
///
/// `attached` is the one that matters when a call comes back empty. The clock
/// advances whenever the graph is running, with or without a participant stream
/// wired into the mixer, so `ctxTime` alone cannot tell "capturing the room"
/// apart from "capturing silence". On 5 Aug 2026 a bot sat in a live standup for
/// 37 minutes, logged `capture is live`, and produced zero utterances because
/// `attachStreams()` never found a single media element. The count was already
/// in the page; nothing read it. Never drop it from this struct again.
#[derive(Debug, Deserialize)]
struct ClockReply {
    #[serde(default, rename = "ctxTime")]
    ctx_time: Option<f64>,
    #[serde(default, alias = "contextState", rename = "ctxState")]
    ctx_state: Option<String>,
    /// Remote participant streams wired into the mixer. `capture.js` reports
    /// `attached`; `capture_ws.js` reports `attachedElements`.
    #[serde(default, alias = "attachedElements", rename = "attached")]
    attached: Option<u64>,
    /// Frames the ScriptProcessor has actually pulled.
    #[serde(default, rename = "seq")]
    seq: Option<u64>,
    #[serde(default, rename = "sentSamples")]
    sent_samples: Option<u64>,
    /// Cumulative tracks ever wired in. Distinguishes "never found anything"
    /// from "found things that carry no audio" once `attached` starts churning.
    #[serde(default, rename = "attachedEver")]
    attached_ever: Option<u64>,
    /// Media elements found on the last deep walk, by where they live. If
    /// shadow and frame stay 0 while `peak` stays 0.0, Meet is not delivering
    /// remote audio through media elements and the DOM is the wrong target.
    #[serde(default, rename = "elemsTop")]
    elems_top: Option<u64>,
    #[serde(default, rename = "elemsShadow")]
    elems_shadow: Option<u64>,
    #[serde(default, rename = "elemsFrame")]
    elems_frame: Option<u64>,
    /// Loudest sample since the previous stats read.
    ///
    /// The one field here that separates "recording the meeting" from
    /// "recording silence". `attached`, `seq`, `sent_samples` and `ctx_time` all
    /// advance identically in both cases, which is exactly how the 5 Aug 2026
    /// outage stayed invisible: every throughput counter looked perfect while
    /// the mixer carried nothing. Peak is 0.0 on silence and non-zero on speech.
    #[serde(default, rename = "peak")]
    peak: Option<f64>,
    /// Share of 256 ms blocks above the silence floor since the previous read.
    #[serde(default, rename = "loudRatio")]
    loud_ratio: Option<f64>,
    /// Which source is feeding the mixer: `"tab"` once getDisplayMedia has
    /// handed over the tab's own output, `"dom"` while the old media-element
    /// tap is still the only thing wired in.
    #[serde(default, rename = "mode")]
    mode: Option<String>,
    /// Outcome of the tab-capture attempt: `pending`, `ok`, `unsupported`,
    /// `no-audio`, or `err:<DOMException name>`. This is what to read first
    /// when peak is still 0.0 after the fix — it separates "Chrome refused the
    /// capture" from "the capture worked and the tab really is silent".
    #[serde(default, rename = "tabState")]
    tab_state: Option<String>,
}

/// One frame as `assets/capture.js` serialises it.
#[derive(Debug, Deserialize)]
struct AudioPayload {
    #[serde(default)]
    offset: f64,
    #[serde(default)]
    rate: u32,
    #[serde(default)]
    speaker: Option<String>,
    #[serde(default)]
    pcm: String,
}

// ---------------------------------------------------------------------------
// MeetSession
// ---------------------------------------------------------------------------

/// Owns the browser, the page, and the chromiumoxide handler task.
pub struct MeetSession {
    page: Page,
    /// `None` after [`MeetSession::close`] has taken it.
    browser: Mutex<Option<Browser>>,
    /// Drives the chromiumoxide event loop. When it finishes, CDP is gone.
    handler_task: JoinHandle<()>,
    /// Pumps `Runtime.bindingCalled` into the frames channel.
    capture_task: Mutex<Option<JoinHandle<()>>>,
    /// False when we attached to someone else's browser — never kill that one.
    launched: bool,
    /// Temp profile we created and must clean up.
    owned_user_data_dir: Option<PathBuf>,
    /// URL `join()` navigated to, for navigated-away detection.
    nav_url: Mutex<Option<String>>,
    /// The meeting id `join()` was given. Kept separately from [`Self::nav_url`]
    /// because Teams' final URL does not contain it in plain text — it is
    /// base64-wrapped inside the `coords` parameter — so "am I still on my
    /// meeting?" cannot be answered by string-slicing the URL we navigated to.
    nav_code: Mutex<Option<String>>,
    /// Platform of the meeting `join()` was given, selecting which selector
    /// table every subsequent DOM read is filled from. `None` before `join()`.
    nav_platform: Mutex<Option<Platform>>,
    /// Wall-clock ceiling on `watch_call`, in seconds. See
    /// [`DEFAULT_MAX_CALL_DURATION`].
    max_call_secs: std::sync::atomic::AtomicU64,
    /// Set by [`MeetSession::close`] so [`Drop`] does not redo its work.
    /// `close` takes `self` by value but does not destructure it, so the
    /// destructor still runs on the way out.
    closed: std::sync::atomic::AtomicBool,
    /// Held for the whole session when the bot runs on the shared signed-in
    /// profile IN PLACE (not a copy). Serialises access so exactly one Chrome
    /// ever opens that `user-data-dir` — and, more importantly, so Google sees a
    /// single stable device instead of a fresh "new device" per copied profile,
    /// which was getting the login revoked within hours. Released on drop.
    _profile_guard: Option<tokio::sync::OwnedMutexGuard<()>>,
}

/// Process-global lock over the one signed-in Chrome profile used in place.
static SIGNED_IN_PROFILE_LOCK: std::sync::OnceLock<std::sync::Arc<tokio::sync::Mutex<()>>> =
    std::sync::OnceLock::new();

fn signed_in_profile_lock() -> std::sync::Arc<tokio::sync::Mutex<()>> {
    SIGNED_IN_PROFILE_LOCK
        .get_or_init(|| std::sync::Arc::new(tokio::sync::Mutex::new(())))
        .clone()
}

impl MeetSession {
    /// Launches (or attaches to) a browser and opens a blank page on it.
    pub async fn launch(opts: &BrowserOptions) -> Result<MeetSession> {
        // Startup sweep, once per process: profiles orphaned by an earlier
        // crash are tens of MB each and nothing else ever removes them.
        static SWEEP_ONCE: std::sync::Once = std::sync::Once::new();
        SWEEP_ONCE.call_once(|| {
            let removed = sweep_stale_profiles();
            if removed > 0 {
                tracing::info!(removed, "swept orphaned chrome profiles from a previous run");
            }
        });

        let (browser, handler, launched, owned_dir, profile_guard) = if let Some(port) = opts.attach_cdp_port {
            // Attach mode. The browser belongs to somebody else (in production
            // that is the systemd `tln-browser.service` on 9222), so we never
            // close, kill or reconfigure it — we only add and later drop a tab.
            //
            // `Browser::connect` has no internal timeout: it fetches
            // `/json/version` with a bare reqwest client, so an endpoint that
            // accepts TCP and then stalls hangs this future forever and pins the
            // session in `joining`. Bound it.
            let connect = Browser::connect(format!("http://127.0.0.1:{port}"));
            let (browser, handler) = match tokio::time::timeout(ATTACH_CONNECT_TIMEOUT, connect)
                .await
            {
                Ok(res) => res.with_context(|| {
                    format!("failed to attach to the CDP endpoint on port {port}")
                })?,
                Err(_) => bail!(
                    "timed out after {}s attaching to the CDP endpoint on port {port}; \
                     the port accepted the connection but never completed the handshake",
                    ATTACH_CONNECT_TIMEOUT.as_secs()
                ),
            };
            (browser, handler, false, None, None)
        } else {
            if !opts.chromium_path.exists() {
                bail!(
                    "chromium binary not found at {}",
                    opts.chromium_path.display()
                );
            }

            // Profile selection. Two modes:
            //
            //  * signed-in profile IN PLACE (profile_template set): use the real
            //    directory directly, serialised behind a global lock. NOT copied
            //    — copying presented Google a fresh "new device" every session
            //    and the login was revoked within hours. Concurrency is 1 here
            //    by construction (Chrome locks the dir); a second overlapping
            //    meeting fails fast rather than corrupting the profile.
            //  * throwaway per session (no template): a fresh temp dir, cleaned
            //    up on drop. This is the anonymous-guest path.
            let (dir, owned, profile_guard) = if let Some(template) = &opts.profile_template {
                let lock = signed_in_profile_lock();
                let guard = lock.try_lock_owned().map_err(|_| {
                    anyhow!(
                        "the signed-in profile is already in use by another bot; \
                         one meeting at a time on a single Google account"
                    )
                })?;
                if !template.is_dir() {
                    bail!(
                        "signed-in profile {} does not exist; seed it once by signing in \
                         (see .agent/skills/meetbot/SKILL.md)",
                        template.display()
                    );
                }
                // A stale SingletonLock from a crashed run makes Chrome refuse
                // to start; clear it now that we hold the process lock.
                for name in ["SingletonLock", "SingletonSocket", "SingletonCookie"] {
                    let _ = std::fs::remove_file(template.join(name));
                }
                (template.clone(), None, Some(guard))
            } else {
                let dir = match &opts.user_data_dir {
                    Some(d) => d.clone(),
                    None => std::env::temp_dir()
                        .join(format!("{PROFILE_PREFIX}{}", uuid::Uuid::new_v4())),
                };
                std::fs::create_dir_all(&dir)
                    .with_context(|| format!("failed to create user-data-dir {}", dir.display()))?;
                let owned = opts.user_data_dir.is_none().then(|| dir.clone());
                (dir, owned, None)
            };

            let mut builder = BrowserConfig::builder()
                .chrome_executable(&opts.chromium_path)
                .user_data_dir(&dir)
                .window_size(opts.window_size.0, opts.window_size.1)
                // --no-sandbox + --disable-setuid-sandbox: WSL2 has no usable
                // user namespaces, Chrome will not start without this.
                .no_sandbox()
                .launch_timeout(Duration::from_secs(45));

            // One `.arg()` per flag, in chromiumoxide's key/value form. See the
            // LANDMINE note on LAUNCH_FLAGS: the `"--flag=value"` string form
            // compiles fine and is silently discarded by Chrome.
            for (key, value) in LAUNCH_FLAGS {
                builder = match value {
                    Some(v) => builder.arg((*key, *v)),
                    None => builder.arg(*key),
                };
            }

            // Derived from the binary we are about to run, never hardcoded: the
            // version here must agree with the Client-Hints Chrome will emit.
            let ua = user_agent_for_binary(&opts.chromium_path).unwrap_or_else(|| {
                tracing::warn!(
                    path = %opts.chromium_path.display(),
                    "could not read the chromium version; falling back to a pinned \
                     user-agent, which Meet may reject as a Client-Hints mismatch"
                );
                FALLBACK_USER_AGENT.to_string()
            });
            builder = builder.arg(("user-agent", ua.as_str()));

            // Headless is selected by OUR OWN flag, never by
            // `new_headless_mode()`.
            //
            // chromiumoxide's headless arms emit `--headless=new` together with
            // a bare `--mute-audio` (config.rs:426), and `--mute-audio` silences
            // Chrome's audio OUTPUT. That is fatal for the PulseAudio loopback
            // capture, which records the sink Chrome plays into: muted Chrome
            // writes silence to the sink and every meeting is deaf again, for a
            // reason nothing in this file would explain.
            //
            // The flag is presence-based, so it cannot be neutralised by giving
            // it a value. Measured 6 Aug 2026 against a page playing a 440 Hz
            // tone at gain 0.5, reading `RDPSink.monitor`:
            //   no flag             -> peak 0.5000
            //   --mute-audio=false  -> peak 0.0000
            //   --mute-audio        -> peak 0.0000
            // `--mute-audio=false` mutes exactly as hard as `--mute-audio`, and
            // LAUNCH_FLAGS carried that "false" for months believing it did the
            // opposite.
            //
            // Staying in headed mode and passing `--headless=new` ourselves is
            // the only way to get the new renderer without the mute. The legacy
            // `--headless` has no WebAudio at all, so the value matters.
            // Chrome must play into OUR sink. Passed as process env rather than
            // a flag because Chrome has no sink-selection flag; libpulse reads
            // PULSE_SINK.
            if let Some(sink) = opts.pulse_sink.as_deref() {
                builder = builder.env("PULSE_SINK", sink);
            }

            let builder = if opts.headless {
                builder.with_head().arg(("headless", "new"))
            } else {
                builder.with_head()
            };

            let cfg = builder
                .build()
                .map_err(|e| anyhow!("invalid browser config: {e}"))?;

            let (browser, handler) = Browser::launch(cfg)
                .await
                .context("failed to launch chromium")?;
            (browser, handler, true, owned, profile_guard)
        };

        // chromiumoxide's `Handler` is an inert `Stream`: nothing at all happens
        // on the connection unless something polls it, for the whole lifetime of
        // the session.
        let handler_task = tokio::spawn(async move {
            let mut handler = handler;
            while let Some(res) = handler.next().await {
                if let Err(e) = res {
                    tracing::debug!(error = %e, "cdp handler event error");
                }
            }
            tracing::debug!("cdp handler loop ended");
        });

        let page = match browser.new_page("about:blank").await {
            Ok(page) => page,
            Err(e) => {
                handler_task.abort();
                if let Some(dir) = &owned_dir {
                    let _ = std::fs::remove_dir_all(dir);
                }
                return Err(anyhow!("failed to open a page: {e}"));
            }
        };

        // Belt and braces with the `--user-agent` launch flag: the CDP override
        // also covers attach mode, where we did not choose the launch flags.
        //
        // Ask the browser what it actually is rather than assuming. `Browser
        // .getVersion` returns the native UA — including the `HeadlessChrome`
        // token when applicable — carrying the true version, so stripping just
        // that token yields a UA that agrees with the Client-Hints by
        // construction. This is what keeps the fix alive across Chromium bumps.
        let ua = match browser.version().await {
            Ok(v) if v.user_agent.contains("Chrome/") => strip_headless_token(&v.user_agent),
            other => {
                if let Err(e) = other {
                    tracing::warn!(error = %e, "could not read the browser version over CDP");
                }
                user_agent_for_binary(&opts.chromium_path)
                    .unwrap_or_else(|| FALLBACK_USER_AGENT.to_string())
            }
        };
        tracing::debug!(user_agent = %ua, "pinned the user-agent");
        if let Err(e) = page.set_user_agent(&ua).await {
            tracing::warn!(error = %e, "could not override the user-agent over CDP");
        }

        // Pin the UI language. Meet takes `?hl=en` on the URL, but **Teams has
        // no `?hl=` equivalent at all** — its UI language follows
        // `Accept-Language`, so without this header every English text anchor in
        // `teams::selectors` silently stops matching on a host with a non-English
        // locale, and the bot degrades to "waited out the admission timeout" with
        // no indication why. `--lang=en-US` on the command line is the other half
        // and is already in `LAUNCH_FLAGS`; the header also covers attach mode,
        // where we did not choose the launch flags.
        if let Err(e) = set_accept_language(&page).await {
            tracing::warn!(
                error = %e,
                "could not pin Accept-Language; Teams text anchors may not match on a \
                 non-English host"
            );
        }

        Ok(MeetSession {
            page,
            browser: Mutex::new(Some(browser)),
            handler_task,
            capture_task: Mutex::new(None),
            launched,
            owned_user_data_dir: owned_dir,
            _profile_guard: profile_guard,
            nav_url: Mutex::new(None),
            nav_code: Mutex::new(None),
            nav_platform: Mutex::new(None),
            max_call_secs: std::sync::atomic::AtomicU64::new(
                default_max_call_duration().as_secs(),
            ),
            closed: std::sync::atomic::AtomicBool::new(false),
        })
    }

    /// Current wall-clock ceiling on the in-call phase.
    pub fn max_call_duration(&self) -> Duration {
        Duration::from_secs(
            self.max_call_secs
                .load(std::sync::atomic::Ordering::Relaxed),
        )
    }

    /// Overrides the in-call ceiling for this session.
    ///
    /// Called by `session.rs` right after [`MeetSession::launch`] when
    /// `Config::max_call_duration_min` is set. When it is absent the session
    /// keeps [`DEFAULT_MAX_CALL_DURATION`] (or the `MEETBOT_MAX_CALL_MIN` env
    /// override), so the cap is active either way. A zero or absurd duration is
    /// clamped back to the default rather than disabling the cap — an unbounded
    /// call is exactly the failure mode this exists to prevent.
    pub fn set_max_call_duration(&self, d: Duration) {
        self.max_call_secs
            .store(clamp_max_call_secs(d), std::sync::atomic::Ordering::Relaxed);
    }

    /// Navigates to the meeting, fills the bot name, deals with the passcode,
    /// and clicks join. Errors here drive `Joining -> Failed`.
    ///
    /// Dispatches on platform. Both paths end in the same place — the caller
    /// then runs the *shared* [`MeetSession::wait_for_admission`],
    /// [`MeetSession::start_capture`] and [`MeetSession::watch_call`], so the
    /// SPEC §0.1 semantics (never-admitted finishes `completed` with zero
    /// segments), the wall-clock cap and the `CallExit` mapping are identical
    /// for Meet and Teams by construction rather than by parallel maintenance.
    pub async fn join(
        &self,
        key: &MeetingKey,
        bot_name: &str,
        passcode: Option<&str>,
    ) -> Result<()> {
        *self.nav_platform.lock().await = Some(key.platform);
        *self.nav_code.lock().await = Some(key.native_id.clone());

        match key.platform {
            Platform::GoogleMeet => self.join_meet(key, bot_name, passcode).await,
            Platform::Teams => self.join_teams(key, bot_name, passcode).await,
        }
    }

    /// The Google Meet green-room dance.
    async fn join_meet(
        &self,
        key: &MeetingKey,
        bot_name: &str,
        passcode: Option<&str>,
    ) -> Result<()> {
        let url = join_url(key);
        tracing::info!(url = %url, bot_name = %bot_name, "navigating to the meeting");

        self.page
            .goto(url.clone())
            .await
            .with_context(|| format!("failed to navigate to {url}"))?;
        *self.nav_url.lock().await = Some(url.clone());

        // Meet's green room is client-rendered; the load event fires long before
        // the name field exists.
        tokio::time::sleep(Duration::from_millis(2_500)).await;

        let deadline = Instant::now() + JOIN_TIMEOUT;
        let mut named = false;
        let mut silenced = false;
        let mut expanded = false;
        let mut passcode_done = passcode.is_none();

        loop {
            if Instant::now() >= deadline {
                let last = self.probe().await.ok();
                bail!(
                    "timed out after {}s trying to click a join control on {url} (last probe: {:?})",
                    JOIN_TIMEOUT.as_secs(),
                    last
                );
            }

            let probe = self.probe().await?;

            // Already inside (pre-admitted / instant join): nothing left to do.
            if probe.in_call {
                tracing::info!("already in call straight off the green room");
                return Ok(());
            }
            // Already knocking: the click landed on an earlier iteration.
            if probe.waiting.is_some() {
                tracing::info!(marker = ?probe.waiting, "waiting room reached");
                return Ok(());
            }
            if let Some(denied) = &probe.denied {
                // NOT an error: `SPEC.md` §0.1 wants this to finish `completed`.
                // `wait_for_admission` sees the same copy and reports
                // `Admission::Denied`.
                tracing::warn!(marker = %denied, "meeting refused the bot at the green room");
                return Ok(());
            }

            // Interstitials first — they overlay both the name field and the
            // join button.
            let _ = self.click_by_text(selectors::DISMISS_BUTTON_TEXTS).await;

            // Silence the bot BEFORE it enters. Attempt the toggles EVERY
            // iteration, not once: the "Turn off ..." labels only match while a
            // device is on, so re-clicking is self-limiting and harmless, and a
            // camera control that renders a beat late still gets caught. The old
            // code latched after the first toggle and let exactly that late
            // camera through, which is how the green pattern reached the room.
            while let Ok(Some(which)) =
                self.click_by_text(selectors::SELF_MUTE_BUTTON_TEXTS).await
            {
                if !silenced {
                    silenced = true;
                }
                tracing::debug!(control = %which, "turned a bot input off before joining");
            }

            if !named {
                match self.fill_field(selectors::NAME_INPUT, bot_name).await {
                    Ok(true) => {
                        named = true;
                        tracing::debug!("bot name filled");
                    }
                    Ok(false) => { /* signed-in, or no name field on this render */ }
                    Err(e) => tracing::warn!(error = %e, "failed to fill the bot name"),
                }
            }

            if !passcode_done
                && let Some(code) = passcode {
                    match self.fill_field(selectors::PASSCODE_INPUT, code).await {
                        Ok(true) => {
                            passcode_done = true;
                            tracing::debug!("passcode filled");
                        }
                        Ok(false) => { /* no passcode field on this platform */ }
                        Err(e) => tracing::warn!(error = %e, "failed to fill the passcode"),
                    }
                }

            // Reveal the hidden join controls before choosing one; without this
            // the only visible control is the session-stealing `Switch here`.
            // Google renders the disclosure menu asynchronously, so allow more
            // settle time and let it re-fire if the first click did not take.
            if self.find_by_text(selectors::JOIN_BUTTON_TEXTS).await?.is_none()
                && let Ok(Some(_)) = self.click_by_text(selectors::EXPANDER_BUTTON_TEXTS).await {
                    expanded = true;
                    tracing::debug!("expanded 'Other ways to join'");
                    tokio::time::sleep(Duration::from_millis(900)).await;
                }
            let _ = expanded; // retained for logging/telemetry only

            // Do NOT join while the camera is still live: joining then would
            // broadcast the green test pattern for the instant before the mute
            // lands. Loop back, let the mute step above turn it off, and only
            // then take the join control. Bounded by the outer JOIN_TIMEOUT.
            if self.find_by_text(selectors::CAMERA_LIVE_TEXTS).await?.is_some() {
                tracing::debug!("camera still live; deferring join until it is off");
                tokio::time::sleep(POLL_INTERVAL).await;
                continue;
            }

            if let Some(clicked) = self.click_by_text(selectors::JOIN_BUTTON_TEXTS).await? {
                tracing::info!(button = %clicked, "clicked join");
                // Let Meet transition to the waiting room / call before the
                // caller starts polling for admission.
                tokio::time::sleep(Duration::from_millis(1_500)).await;
                return Ok(());
            }

            // Only now, with every real join control ruled out: if the ONLY
            // affirmative control left is `Switch here`, joining would eject the
            // session already in the call. Bail instead of stealing it.
            // Checked last precisely because `Join here too` sits on this same
            // screen and is the control we actually want.
            if let Some(found) = self.find_by_text(selectors::SESSION_STEAL_BUTTON_TEXTS).await? {
                tracing::warn!(
                    marker = %found,
                    "the only control offered would move this identity's live \
                     session here; leaving without joining"
                );
                bail!(SESSION_ALREADY_PRESENT);
            }

            tokio::time::sleep(POLL_INTERVAL).await;
        }
    }

    /// The Microsoft Teams anonymous-join dance.
    ///
    /// Shape of the flow (all of it VERIFIED against live captures unless
    /// flagged; see `spike/teams_recon.md`):
    ///
    /// 1. Navigate to `teams.microsoft.com/meet/<id>[?p=<passcode>]`. Teams then
    ///    performs **five** document navigations in ~10-15 s, including a silent
    ///    MSAL `prompt=none` probe that always fails for an anonymous visitor —
    ///    that failure is expected and is not an error.
    /// 2. Wait for the chain to settle on `/light-meetings/launch?...&coords=…`
    ///    and the pre-join screen to render. There is **no** "Continue on this
    ///    browser" interstitial: the launcher auto-forwards. If this ever stalls
    ///    on `/dl/launcher/launcher.html`, Microsoft has reintroduced one.
    /// 3. Verify the meeting id survived the chain via
    ///    [`crate::teams::url_contains_code`], which decodes the base64 `coords`
    ///    parameter. This is the check the old code got wrong.
    /// 4. Fill the display name through the native value setter (it is a
    ///    controlled React input; assigning `.value` does not stick).
    /// 5. Click join. The button is **never** disabled, so we gate on presence
    ///    and visibility only.
    ///
    /// The passcode is *not* typed: it rides as `?p=` on the initial navigation
    /// and Teams carries it through the whole chain into `coords.passcode`. On
    /// the anonymous path there is no pre-join passcode prompt to type into —
    /// a missing or wrong passcode surfaces only as the post-join retry screen,
    /// which is indistinguishable from a bad meeting id and is terminal.
    ///
    /// Returning `Ok(())` after seeing the retry screen is deliberate, and
    /// matches the Meet path's handling of a green-room denial: `SPEC.md` §0.1
    /// makes a non-admission a `completed` session with zero segments, not a
    /// failure. Only a genuinely broken join dance returns `Err`.
    async fn join_teams(
        &self,
        key: &MeetingKey,
        bot_name: &str,
        passcode: Option<&str>,
    ) -> Result<()> {
        let url = teams::join_url(&key.native_id, passcode);
        let logged_url = redact_passcode(&url);
        tracing::info!(
            url = %logged_url,
            bot_name = %bot_name,
            passcode = passcode.is_some(),
            "navigating to the Teams meeting"
        );

        self.page
            .goto(url.clone())
            .await
            .with_context(|| format!("failed to navigate to {logged_url}"))?;
        *self.nav_url.lock().await = Some(url.clone());

        // The chain settles in ~10-15 s. Sleeping through the bulk of it saves a
        // dozen probes against pages that are mid-navigation (and therefore not
        // evaluatable); the loop below still polls for the rest.
        tokio::time::sleep(TEAMS_REDIRECT_SETTLE).await;

        let started = Instant::now();
        let deadline = started + JOIN_TIMEOUT;
        let mut stage = JoinStage::Redirect;
        let mut named = false;
        let mut passcode_typed = passcode.is_none();
        let mut code_checked = false;
        let mut probe_errors = 0u32;
        let mut last: Option<PageProbe> = None;

        loop {
            if Instant::now() >= deadline {
                // DIAGNOSTIC FAILURE (requirement 6): name the stage, the
                // selector groups that missed, and whether each was ever
                // observed live. A Teams meeting failing at 22:00 must leave a
                // trace someone can act on in the morning.
                bail!(
                    "{}",
                    teams_stage_failure(stage, &logged_url, started.elapsed(), last.as_ref())
                );
            }

            let probe = match self.probe().await {
                Ok(p) => {
                    probe_errors = 0;
                    p
                }
                Err(e) => {
                    // Five navigations in fifteen seconds: a page caught
                    // mid-navigation cannot be evaluated. Tolerate a run of
                    // them before calling the browser broken.
                    probe_errors += 1;
                    if probe_errors >= 5 {
                        return Err(e.context(format!(
                            "browser stopped responding during the Teams '{}' stage",
                            stage.as_str()
                        )));
                    }
                    tracing::debug!(
                        error = %e,
                        stage = stage.as_str(),
                        "transient probe error while the Teams redirect chain settles"
                    );
                    tokio::time::sleep(POLL_INTERVAL).await;
                    continue;
                }
            };

            // VERIFIED terminal rejection. It replaces the entire pre-join tree,
            // the URL does not change, and `calling-retry-rejoinbutton` is
            // disabled even with both fields prefilled — so there is nothing to
            // retry and this is the end of the road.
            if let Some(marker) = &probe.denied {
                tracing::warn!(
                    stage = stage.as_str(),
                    marker = %marker,
                    diag = %probe.diag_line(),
                    "Teams refused the bot. Bad meeting id, wrong passcode and an ended \
                     meeting render an IDENTICAL screen, so meetbot cannot tell them apart \
                     (SPEC.md §0.1: all three are a non-admission). Finishing as completed \
                     with zero segments."
                );
                self.log_teams_rejection_details(&key.native_id, passcode).await;
                return Ok(());
            }
            if probe.in_call {
                tracing::info!(diag = %probe.diag_line(), "already in the Teams call");
                return Ok(());
            }
            if probe.waiting.is_some() {
                tracing::info!(
                    marker = ?probe.waiting,
                    diag = %probe.diag_line(),
                    "Teams lobby reached"
                );
                return Ok(());
            }

            // Any of the three pre-join anchors is enough: they are separate
            // candidate groups precisely so one of them drifting is survivable.
            let at_prejoin = probe.matched("prejoin_root")
                || probe.matched("name_input")
                || probe.matched("join_button");

            if !at_prejoin {
                tracing::debug!(
                    stage = JoinStage::Redirect.as_str(),
                    url = %probe.url,
                    diag = %probe.diag_line(),
                    "waiting for the Teams redirect chain to reach the pre-join screen"
                );
                stage = JoinStage::Redirect;
                last = Some(probe);
                tokio::time::sleep(POLL_INTERVAL).await;
                continue;
            }
            stage = JoinStage::PreJoin;

            // THE ROOT-CAUSE CHECK. At this point the URL is
            // /light-meetings/launch and the meeting id is base64-wrapped inside
            // `coords`. A plain substring test fails here and reports a healthy
            // page as "navigated away" — that is the bug that made every Teams
            // meeting vanish as a green-heartbeat skip.
            if !code_checked {
                if !teams::url_contains_code(&probe.url, &key.native_id) {
                    bail!(
                        "Teams join failed at stage '{}': the meeting id {} is not in the \
                         current URL, nor inside its base64 `coords` parameter. {} \
                         (url={}, coords_decoded={})",
                        JoinStage::CodeCheck.as_str(),
                        key.native_id,
                        JoinStage::CodeCheck.hint(),
                        probe.url,
                        teams::decode_coords(&probe.url)
                            .as_deref()
                            .unwrap_or("<none>")
                    );
                }
                code_checked = true;
                tracing::info!(
                    stage = JoinStage::CodeCheck.as_str(),
                    passcode_in_coords = teams::coords_passcode(&probe.url).is_some(),
                    "meeting id survived the Teams redirect chain"
                );
            }

            if !named {
                // The name field is a controlled React input: go straight to the
                // native value setter rather than trying key events first.
                match self.set_field(teams::selectors::NAME_INPUT, bot_name).await {
                    Ok(true) => {
                        named = true;
                        tracing::debug!(stage = stage.as_str(), "bot name filled");
                    }
                    // Not fatal: the bot joins under the browser default name,
                    // which still records the meeting. Loud, so the selector
                    // gets fixed.
                    Ok(false) => tracing::warn!(
                        stage = stage.as_str(),
                        group = "name_input",
                        status = teams::Verified::Yes.label(),
                        candidates = ?teams::selectors::NAME_INPUT,
                        diag = %probe.diag_line(),
                        "no Teams display-name field matched; joining under the browser \
                         default name"
                    ),
                    Err(e) => tracing::warn!(error = %e, "failed to fill the Teams bot name"),
                }
            }

            // Belt and braces for recon gap E: `?p=` is the VERIFIED delivery
            // path, but if a real meeting turns out to show a pre-join passcode
            // prompt, fill it too. Absent field -> no-op.
            if !passcode_typed
                && let Some(code) = passcode
            {
                match self
                    .fill_field(teams::selectors::PREJOIN_PASSCODE_INPUT, code)
                    .await
                {
                    Ok(true) => {
                        passcode_typed = true;
                        tracing::info!(
                            "a pre-join passcode field was present and has been filled \
                             (recon gap E: this surface had never been observed)"
                        );
                    }
                    Ok(false) => { /* expected: the passcode rode in on `?p=` */ }
                    Err(e) => tracing::debug!(error = %e, "pre-join passcode fill failed"),
                }
            }

            stage = JoinStage::JoinClick;
            if let Some(sel) = self
                .click_by_selector(teams::selectors::JOIN_BUTTON)
                .await?
            {
                tracing::info!(selector = %sel, stage = stage.as_str(), "clicked Teams join");
                // Let the retry screen or the lobby render before the caller
                // starts polling for admission.
                tokio::time::sleep(Duration::from_millis(1_500)).await;
                return Ok(());
            }

            tracing::debug!(
                stage = stage.as_str(),
                diag = %probe.diag_line(),
                "no Teams join button clickable yet"
            );
            last = Some(probe);
            tokio::time::sleep(POLL_INTERVAL).await;
        }
    }

    /// Logs what the VERIFIED rejection screen echoes back.
    ///
    /// The screen itself refuses to say *why* it rejected us, but its two
    /// prefilled inputs do carry evidence: `meeting-code-input` echoes the id
    /// Teams parsed out of `coords` (a mismatch means the redirect chain mangled
    /// the code, which is an upstream problem, not a DOM one) and
    /// `meeting-passcode-input` echoes the passcode (empty when we never sent
    /// one, which separates "no passcode supplied" from "passcode was wrong").
    /// Best-effort throughout: this is a diagnostic, never a decision.
    async fn log_teams_rejection_details(&self, expected_code: &str, passcode: Option<&str>) {
        let echoed_code = self
            .read_field(teams::selectors::RETRY_CODE_INPUT)
            .await
            .unwrap_or_default();
        let echoed_passcode = self
            .read_field(teams::selectors::RETRY_PASSCODE_INPUT)
            .await
            .unwrap_or_default();

        tracing::warn!(
            expected_code = %expected_code,
            echoed_code = %echoed_code.as_deref().unwrap_or("<absent>"),
            code_matches = echoed_code.as_deref() == Some(expected_code),
            passcode_sent = passcode.is_some(),
            passcode_echoed = echoed_passcode.as_deref().is_some_and(|v| !v.is_empty()),
            copy = %teams::selectors::RETRY_SCREEN_TEXT[0],
            "Teams retry-screen diagnostics"
        );
    }

    /// Polls the DOM every 2 s until admitted, denied, or timed out.
    ///
    /// Returning `TimedOut`/`Denied` is a normal outcome, not an error: the
    /// session finalizes as `completed` with zero segments and the Python client
    /// files it as `skipped_not_admitted`.
    pub async fn wait_for_admission(&self, timeout: Duration) -> Result<Admission> {
        let deadline = Instant::now() + timeout;
        let mut consecutive_probe_errors = 0u32;

        loop {
            match self.probe().await {
                Ok(probe) => {
                    consecutive_probe_errors = 0;

                    if probe.in_call {
                        tracing::info!("admitted to the call");
                        return Ok(Admission::Admitted);
                    }
                    if let Some(marker) = probe.denied {
                        tracing::info!(marker = %marker, "admission denied");
                        return Ok(Admission::Denied);
                    }
                    // Meet drops a denied guest back to the landing page.
                    if !self.on_meeting_url(&probe.url).await {
                        tracing::info!(
                            url = %probe.url,
                            "navigated away from the meeting while waiting"
                        );
                        return Ok(Admission::Denied);
                    }
                }
                Err(e) => {
                    // A page mid-navigation cannot be evaluated; tolerate a few
                    // failures in a row before calling the browser broken.
                    consecutive_probe_errors += 1;
                    if consecutive_probe_errors >= 5 || self.handler_task.is_finished() {
                        return Err(e.context("browser stopped responding in the waiting room"));
                    }
                    tracing::debug!(error = %e, "transient probe error in the waiting room");
                }
            }

            let now = Instant::now();
            if now >= deadline {
                tracing::info!(
                    timeout_sec = timeout.as_secs(),
                    "admission timed out; finishing as completed with zero segments"
                );
                return Ok(Admission::TimedOut);
            }
            let remaining = deadline.saturating_duration_since(now);
            tokio::time::sleep(POLL_INTERVAL.min(remaining)).await;
        }
    }

    /// Injects the tap and starts pushing frames. Must be called only after
    /// `Admission::Admitted`.
    pub async fn start_capture(&self, frames: mpsc::Sender<AudioFrame>) -> Result<()> {
        // The binding has to exist before capture.js looks it up, and the
        // Runtime domain must be enabled or `Runtime.bindingCalled` is never
        // delivered.
        self.page
            .enable_runtime()
            .await
            .map_err(|e| anyhow!("could not enable the CDP Runtime domain: {e}"))?;
        self.page
            .execute(AddBindingParams::new(AUDIO_BINDING))
            .await
            .map_err(|e| anyhow!("could not create the `{AUDIO_BINDING}` binding: {e}"))?;

        let mut events = self
            .page
            .event_listener::<EventBindingCalled>()
            .await
            .map_err(|e| anyhow!("could not subscribe to Runtime.bindingCalled: {e}"))?;

        // `eval_with_gesture`, not `eval`. capture.js calls getDisplayMedia to
        // grab the tab's own audio, and Chrome rejects that with
        // NotAllowedError unless the calling frame has transient user
        // activation. A plain Runtime.evaluate has none, so the tab path would
        // silently fall back to the DOM tap that broke on 5 Aug 2026.
        let reply: StatusReply = self
            .eval_with_gesture(capture_script(self.platform().await))
            .await
            .context("failed to inject assets/capture.js")?;

        match reply.status.as_str() {
            "started" | "already-running" => {
                tracing::info!(status = %reply.status, "audio tap running");
            }
            other => bail!("audio tap refused to start: {other}"),
        }

        let task = tokio::spawn(async move {
            let mut frames_sent: u64 = 0;
            let mut dropped: u64 = 0;

            while let Some(ev) = events.next().await {
                if ev.name != AUDIO_BINDING {
                    continue;
                }
                let payload: AudioPayload = match serde_json::from_str(&ev.payload) {
                    Ok(p) => p,
                    Err(e) => {
                        tracing::warn!(error = %e, "undecodable audio payload from the page");
                        continue;
                    }
                };

                let Some(bytes) = base64_decode(&payload.pcm) else {
                    tracing::warn!("audio payload was not valid base64");
                    continue;
                };

                let mut pcm = crate::audio::pcm_from_le_bytes(&bytes);
                if pcm.is_empty() {
                    continue;
                }
                // The page asks Chrome for a 16 kHz AudioContext, but that is
                // only a hint; resample whenever it was not honoured.
                if payload.rate != 0 && payload.rate != crate::audio::SAMPLE_RATE {
                    pcm = crate::audio::resample_to_16k(&pcm, payload.rate);
                }

                let frame = AudioFrame {
                    pcm,
                    offset_sec: payload.offset,
                    speaker: payload
                        .speaker
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty()),
                };

                // Never block the CDP event loop on a slow consumer: a stalled
                // pump pushes back-pressure all the way into the browser.
                match tokio::time::timeout(FRAME_SEND_TIMEOUT, frames.send(frame)).await {
                    Ok(Ok(())) => frames_sent += 1,
                    Ok(Err(_)) => {
                        tracing::debug!("frames channel closed; stopping the audio pump");
                        break;
                    }
                    Err(_) => {
                        dropped += 1;
                        if dropped % 10 == 1 {
                            tracing::warn!(dropped, "segmenter is behind; dropping audio frames");
                        }
                    }
                }
            }

            tracing::info!(frames_sent, dropped, "audio pump finished");
        });

        *self.capture_task.lock().await = Some(task);

        self.await_audio_clock("window.__meetbotCaptureStats()")
            .await
            .context("the CDP audio tap never started pumping samples")?;
        self.report_tab_capture().await;
        Ok(())
    }

    /// Logs, once, which source the tap ended up on.
    ///
    /// The clock gate resolves in well under a second, while `getDisplayMedia`
    /// takes a moment to settle, so `mode` is still `dom`/`pending` at that
    /// point and the gate cannot answer the only question that matters. Without
    /// this, the first honest signal is the `tap stats` line a full minute into
    /// the call — and on 5 Aug the cost of a slow signal was two days.
    ///
    /// Never fatal. A bot on the DOM fallback might still record a usable
    /// meeting, and killing a live call over a log line is strictly worse than
    /// recording it and saying so loudly.
    async fn report_tab_capture(&self) {
        let deadline = Instant::now() + TAB_CAPTURE_SETTLE;
        loop {
            let stats = self.eval::<ClockReply>(TAP_STATS_EXPR.to_string()).await;
            let settled = match &stats {
                Ok(s) => s.tab_state.as_deref().unwrap_or("pending") != "pending",
                Err(_) => false,
            };
            if settled || Instant::now() >= deadline {
                let (mode, tab_state) = match &stats {
                    Ok(s) => (
                        s.mode.as_deref().unwrap_or("unknown"),
                        s.tab_state.as_deref().unwrap_or("unknown"),
                    ),
                    Err(_) => ("unknown", "unreadable"),
                };
                if mode == "tab" {
                    tracing::info!(mode, tab_state, "capturing the tab's own audio output");
                } else {
                    tracing::warn!(
                        mode,
                        tab_state,
                        "tab capture did not take; falling back to the media-element tap. \
                         Meet stopped exposing remote audio through media elements on \
                         5 Aug 2026, so expect silence. Check that \
                         --auto-accept-this-tab-capture is on the command line."
                    );
                }
                return;
            }
            tokio::time::sleep(AUDIO_CLOCK_POLL).await;
        }
    }

    /// WebSocket-transport variant of [`MeetSession::start_capture`], selected by
    /// `state::CaptureTransport::WebSocket`.
    ///
    /// Frames do **not** flow through `meet.rs` on this path: the page opens a
    /// socket straight to `audio::start_ingest_server`, which owns the frames
    /// sender. `meet.rs` only injects the tap and proves it is pumping, so there
    /// is no pump task to register here. The caller must keep the `IngestServer`
    /// alive for the whole call — dropping it closes the frames channel and
    /// flushes the segmenter.
    pub async fn start_capture_ws(&self, ingest_url: &str) -> Result<()> {
        let stats: WsStatsReply = self
            .eval(capture_ws_script(self.platform().await, ingest_url))
            .await
            .context("failed to inject assets/capture_ws.js")?;

        if let Some(err) = stats.error.as_deref() {
            bail!("audio tap refused to start: {err}");
        }

        tracing::info!(
            url = %ingest_url,
            attached = stats.attached_elements,
            "websocket audio tap injected"
        );

        self.await_audio_clock("window.__meetbotWsCapture.stats()")
            .await
            .context("the websocket audio tap never started pumping samples")?;
        Ok(())
    }

    /// Blocks until the page's `AudioContext` clock is demonstrably advancing.
    ///
    /// Per the headless-capture spike this is mandatory and must not be replaced
    /// by a fixed sleep: with `--headless=new` a context can report `running`
    /// while the render quantum is never pumped, and `currentTime` is the only
    /// witness that distinguishes a live graph from a stalled one. It advances
    /// whenever the graph is running, so silence in the room does not trip it —
    /// this gates on the tap working, never on anyone speaking.
    async fn await_audio_clock(&self, probe_expr: &str) -> Result<()> {
        let deadline = Instant::now() + AUDIO_CLOCK_TIMEOUT;
        let mut first: Option<f64> = None;

        loop {
            let stats: ClockReply = self
                .eval(probe_expr.to_string())
                .await
                .context("could not read the audio tap's stats hook")?;

            match (stats.ctx_time, first) {
                (Some(now), Some(before)) if now > before => {
                    // `attached` is deliberately NOT a gate here. The bot joins
                    // several minutes early to be the account's first presence,
                    // so an empty room with zero streams is the normal state at
                    // this point. The alarm for "running but deaf" lives in
                    // `watch_call`, which knows whether anyone else has shown up.
                    tracing::info!(
                        ctx_time = now,
                        state = stats.ctx_state.as_deref().unwrap_or("unknown"),
                        attached = stats.attached.unwrap_or(0),
                        seq = stats.seq.unwrap_or(0),
                        "audio clock is advancing; capture is live"
                    );
                    return Ok(());
                }
                (Some(now), None) => first = Some(now),
                _ => {}
            }

            if Instant::now() >= deadline {
                bail!(
                    "AudioContext clock did not advance within {}s (state={}, currentTime={:?}). \
                     The context is suspended or the render graph is stalled — check that \
                     --autoplay-policy=no-user-gesture-required is on the command line.",
                    AUDIO_CLOCK_TIMEOUT.as_secs(),
                    stats.ctx_state.as_deref().unwrap_or("unknown"),
                    stats.ctx_time
                );
            }
            tokio::time::sleep(AUDIO_CLOCK_POLL).await;
        }
    }

    /// Watches for meeting end / removal / stop. `stop` resolving means the
    /// session got `SessionCommand::Stop`. Returns when the call is over.
    ///
    /// `audio` is the out-of-browser capture, when one is running. With
    /// `CaptureTransport::Pulse` there is no tap in the page to read, so the
    /// deafness check comes from the recorder's own counters instead. `None`
    /// keeps the historic behaviour of reading `__meetbotCaptureStats()` off
    /// the page. Either way the check is the same one -- peak amplitude while
    /// other people are present -- because that is the only measurement that
    /// separates recording the meeting from recording nothing.
    pub async fn watch_call(
        &self,
        lonely_grace: Duration,
        empty_room_grace: Duration,
        stop: oneshot::Receiver<()>,
        audio: Option<std::sync::Arc<crate::pulse::PulseStats>>,
        speaker: Option<std::sync::Arc<std::sync::Mutex<Option<String>>>>,
        pulse_source: Option<String>,
    ) -> CallExit {
        let mut stop = stop;
        let mut alone_since: Option<Instant> = None;
        // The bot joins several minutes early to be the account's first presence,
        // so it starts alone in an empty room. That is NOT the "everyone left"
        // case the short `lonely_grace` is for — until at least one other person
        // has appeared, tolerate the empty room for the much longer
        // `empty_room_grace`. Once someone shows, secondary to the short grace so a
        // genuinely-ended meeting is left promptly.
        let mut seen_others = false;
        let mut consecutive_probe_errors = 0u32;
        // Pulse reader liveness. `last_frames` is the previous tick's count and
        // `stalled_since` when it stopped moving. See PULSE_STALL_GRACE.
        // Last speaker-selector diagnostic, surfaced in `tap stats`, plus whether
        // ANY name resolved during the call. `spk_hits=0/0/0` for a whole
        // meeting means the speaking indicator has drifted and every line will
        // come out "Unknown".
        let mut speaker_diag = String::from("n/a");
        let mut speaker_seen = false;
        let mut last_frames: Option<u64> = None;
        let mut stalled_since: Option<Instant> = None;
        // Audio-liveness watchdog state. See `DEAF_IN_CALL_GRACE`.
        let mut ticks = 0u32;
        let mut deaf_since: Option<Instant> = None;

        // Wall-clock backstop. Every other exit from this loop depends on the
        // DOM still being readable the way `selectors` expects; this one does
        // not, which is the entire point. See `DEFAULT_MAX_CALL_DURATION`.
        let max_call = self.max_call_duration();
        let call_deadline = Instant::now() + max_call;

        loop {
            if Instant::now() >= call_deadline {
                tracing::warn!(
                    max_call_sec = max_call.as_secs(),
                    "in-call wall-clock cap reached; ending the call and keeping the audio \
                     captured so far. If the meeting was genuinely still running, the \
                     participant-count selectors have probably drifted — check \
                     selectors::PARTICIPANT_COUNT and selectors::PARTICIPANT_TILES."
                );
                return CallExit::MeetingEnded;
            }

            tokio::select! {
                // Either an explicit Stop, or session.rs dropped the sender.
                // Both mean "wind this call up now".
                _ = &mut stop => return CallExit::Stopped,
                _ = tokio::time::sleep(POLL_INTERVAL) => {}
            }

            if self.handler_task.is_finished() {
                return CallExit::BrowserError("CDP connection closed".to_string());
            }

            let probe = match self.probe().await {
                Ok(p) => {
                    consecutive_probe_errors = 0;
                    p
                }
                Err(e) => {
                    consecutive_probe_errors += 1;
                    if consecutive_probe_errors >= 3 {
                        return CallExit::BrowserError(format!("page stopped responding: {e:#}"));
                    }
                    continue;
                }
            };

            // Removal is checked first: Meet renders the removal notice and the
            // generic "call ended" copy on the same screen, and the two map to
            // different terminal statuses (Stopped vs Completed).
            if let Some(marker) = &probe.removed {
                tracing::info!(marker = %marker, "bot was removed by the host");
                return CallExit::RemovedByHost;
            }
            if let Some(marker) = &probe.ended {
                tracing::info!(marker = %marker, "meeting ended");
                return CallExit::MeetingEnded;
            }
            if !self.on_meeting_url(&probe.url).await {
                tracing::info!(url = %probe.url, "navigated away from the meeting");
                return CallExit::MeetingEnded;
            }
            // The toolbar vanishing without any end-of-call copy still means the
            // bot is out of the call.
            if !probe.in_call && probe.participants.is_none() {
                tracing::info!("in-call UI disappeared");
                return CallExit::MeetingEnded;
            }

            // Empty-room exit. Two very different cases share this branch:
            //   * nobody has arrived yet  -> long `empty_room_grace` (waiting for
            //     the meeting the bot joined early to actually start)
            //   * others were here and left -> short `lonely_grace`
            // `seen_others` selects between them.
            match probe.participants {
                Some(n) if n >= 2 => {
                    seen_others = true;
                    alone_since = None;
                }
                Some(n) if n <= 1 => {
                    let grace = if seen_others { lonely_grace } else { empty_room_grace };
                    let since = *alone_since.get_or_insert_with(Instant::now);
                    if since.elapsed() >= grace {
                        tracing::info!(
                            grace_sec = grace.as_secs(),
                            seen_others,
                            "empty room past the grace period; ending the call"
                        );
                        return CallExit::MeetingEnded;
                    }
                }
                _ => alone_since = None,
            }

            // Audio-liveness watchdog. Everything above this point can be true
            // of a bot that is capturing nothing: it is in the call, the UI is
            // intact, people are present. Only the tap's own counters say
            // whether any of that audio is reaching us.
            ticks = ticks.wrapping_add(1);
            let due = ticks % STATS_LOG_EVERY_TICKS == 0;
            // Throttle the deafness probe.
            //
            // This originally evaluated on EVERY tick once others were present,
            // which at POLL_INTERVAL = 2s roughly doubled the CDP round-trips of
            // a live call. That matters more than it looks: `99a311a` fixed the
            // 22-29 Jul navigation timeouts by cutting CDP event volume through
            // the single chromiumoxide handler, nav timeouts then sat at ZERO
            // for 33 sessions across 30 Jul - 4 Aug, and came back 3-in-14 on
            // 5 Aug on the builds that added this probe. Not proven to be the
            // cause, but this probe is the new load and it does not need
            // 2-second resolution to enforce a 180-second grace.
            let probe_deaf = seen_others && ticks % DEAF_PROBE_EVERY_TICKS == 0;

            // Refresh the speaker label for the out-of-browser recorder. Meet
            // stays the only place a participant's NAME exists, so with the
            // Pulse transport the audio comes from the sink and the label comes
            // from the DOM. Best effort: a miss degrades one utterance to
            // "Unknown" and is never fatal.
            if let Some(slot) = speaker.as_ref() {
                let (name, diag) = self.current_speaker().await;
                speaker_diag = diag;
                if name.is_some() {
                    speaker_seen = true;
                }
                if let Ok(mut g) = slot.lock() {
                    *g = name;
                }
            }

            // Pulse transport: the witness lives in the recorder, not the page.
            if let Some(pulse) = audio.as_ref() {
                use std::sync::atomic::Ordering;
                let (peak, loud_ratio) = pulse.take_peak();
                let peak = peak as f64;
                if due {
                    tracing::info!(
                        mode = "pulse",
                        source = pulse_source.as_deref().unwrap_or("?"),
                        frames = pulse.frames.load(Ordering::Relaxed),
                        peak,
                        loud_ratio,
                        participants = probe.participants.unwrap_or(0),
                        spk_hits = %speaker_diag,
                        spk_resolved = speaker_seen,
                        "tap stats"
                    );
                }
                if pulse.stopped.load(Ordering::Relaxed) {
                    tracing::error!("the pulse capture thread died mid-call");
                    return CallExit::BrowserError(
                        "pulse capture thread stopped while the call was live".to_string(),
                    );
                }

                // A STALLED reader is not a stopped one.
                //
                // On 6 Aug 2026 the Pulse daemon wedged mid-call and
                // `pa_simple_read` simply never returned. The thread was alive,
                // nothing was flagged, and two consecutive stats lines a minute
                // apart both read `frames=285`. The session then finished
                // `completed`. Watching only for a dead thread reproduces the
                // exact blind spot the original outage was made of: a counter
                // that has stopped moving must be as loud as one that never
                // started.
                let frames = pulse.frames.load(Ordering::Relaxed);
                match last_frames {
                    Some(prev) if prev == frames => {
                        let since = *stalled_since.get_or_insert_with(Instant::now);
                        if since.elapsed() >= PULSE_STALL_GRACE {
                            tracing::error!(
                                stalled_sec = since.elapsed().as_secs(),
                                frames,
                                "the pulse reader has not produced a frame; the audio server \
                                 is most likely wedged. Check /mnt/wslg/pulseaudio.log for \
                                 `q overrun` on the sink Chrome plays into."
                            );
                            return CallExit::BrowserError(
                                "pulse capture stalled mid-call".to_string(),
                            );
                        }
                    }
                    _ => {
                        stalled_since = None;
                        last_frames = Some(frames);
                    }
                }
                if seen_others && peak <= SILENCE_FLOOR {
                    let since = *deaf_since.get_or_insert_with(Instant::now);
                    if since.elapsed() >= DEAF_IN_CALL_GRACE {
                        tracing::error!(
                            deaf_sec = since.elapsed().as_secs(),
                            participants = probe.participants.unwrap_or(0),
                            "in the call with other people and every sample off the \
                             PulseAudio monitor is silent. First thing to check is that \
                             Chrome is not muted: --mute-audio silences its OUTPUT and the \
                             flag is presence-based, so `--mute-audio=false` mutes just as \
                             hard. Then check that Chrome is playing into the sink whose \
                             monitor is configured as `pulse_source`."
                        );
                        return CallExit::BrowserError(
                            "pulse capture recorded only silence while others were present"
                                .to_string(),
                        );
                    }
                } else if peak > SILENCE_FLOOR {
                    deaf_since = None;
                }
                continue;
            }

            if due || probe_deaf {
                if let Ok(stats) = self.eval::<ClockReply>(TAP_STATS_EXPR.to_string()).await {
                    let peak = stats.peak.unwrap_or(0.0);
                    if due {
                        tracing::info!(
                            mode = stats.mode.as_deref().unwrap_or("unknown"),
                            tab_state = stats.tab_state.as_deref().unwrap_or("unknown"),
                            attached = stats.attached.unwrap_or(0),
                            attached_ever = stats.attached_ever.unwrap_or(0),
                            el_top = stats.elems_top.unwrap_or(0),
                            el_shadow = stats.elems_shadow.unwrap_or(0),
                            el_frame = stats.elems_frame.unwrap_or(0),
                            seq = stats.seq.unwrap_or(0),
                            sent_samples = stats.sent_samples.unwrap_or(0),
                            peak,
                            loud_ratio = stats.loud_ratio.unwrap_or(0.0),
                            ctx_time = stats.ctx_time.unwrap_or(0.0),
                            ctx_state = stats.ctx_state.as_deref().unwrap_or("unknown"),
                            participants = probe.participants.unwrap_or(0),
                            "tap stats"
                        );
                    }

                    // Deafness is measured as SILENCE, not as an unattached
                    // stream. `attached` counts media elements carrying an audio
                    // track and Meet reports 3 of those with the bot alone in an
                    // empty room, so it is nearly always non-zero and cannot
                    // carry this check. Peak amplitude can.
                    //
                    // Gated on other people being present. A quiet room with
                    // nobody in it is not a fault, and the bot deliberately
                    // joins early into exactly that.
                    if seen_others && peak <= SILENCE_FLOOR {
                        let since = *deaf_since.get_or_insert_with(Instant::now);
                        if since.elapsed() >= DEAF_IN_CALL_GRACE {
                            tracing::error!(
                                deaf_sec = since.elapsed().as_secs(),
                                participants = probe.participants.unwrap_or(0),
                                mode = stats.mode.as_deref().unwrap_or("unknown"),
                                tab_state = stats.tab_state.as_deref().unwrap_or("unknown"),
                                attached = stats.attached.unwrap_or(0),
                                sent_samples = stats.sent_samples.unwrap_or(0),
                                "in the call with other people, audio flowing, and every sample \
                                 silent; this meeting is recording nothing. Ending it so the \
                                 profile is freed for the next meeting. Read `tab_state` first: \
                                 anything other than `ok` means Chrome never handed over the \
                                 tab's audio (check that --auto-accept-this-tab-capture is on \
                                 the command line and that the injection carried a user \
                                 gesture), and `mode=dom` means the bot fell back to the \
                                 media-element tap that Meet stopped feeding on 5 Aug 2026. \
                                 `tab_state=ok` with silence is a different fault: the capture \
                                 works and the tab itself is producing nothing."
                            );
                            return CallExit::BrowserError(
                                "audio tap captured only silence while others were present"
                                    .to_string(),
                            );
                        }
                    } else if peak > SILENCE_FLOOR {
                        deaf_since = None;
                    }
                }
            }
        }
    }

    /// Participants currently visible in the tray, bot included.
    pub async fn participant_count(&self) -> Result<usize> {
        let probe = self.probe().await?;
        Ok(probe.participants.unwrap_or(0).max(0) as usize)
    }

    /// Display name of whoever the UI marks as speaking right now.
    ///
    /// Best-effort: `None` whenever Meet's speaking indicator has drifted away
    /// from [`selectors::SPEAKING_INDICATORS`]. Per-frame speaker attribution
    /// normally comes from the injected tap, not from this call.
    pub async fn active_speaker(&self) -> Result<Option<String>> {
        let (speaking, tiles, names) = speaker_selectors(self.platform().await);
        let expr = js::ACTIVE_SPEAKER
            .replace("__SPEAKING__", &json_list(speaking))
            .replace("__TILES__", &json_list(tiles))
            .replace("__NAMES__", &json_list(names));
        let reply: FieldReply = self.eval(expr).await?;
        if !reply.found || reply.value.trim().is_empty() {
            return Ok(None);
        }
        Ok(Some(reply.value.trim().to_string()))
    }

    /// Clicks leave. Best effort; never errors the session.
    pub async fn leave(&self) {
        // Stop the tap first, so no frame is emitted from a half-torn-down page.
        if let Ok(reply) = self.eval::<OkReply>(js::STOP_CAPTURE.to_string()).await {
            tracing::debug!(stopped = reply.ok, "in-page audio tap torn down");
        }

        let (leave_texts, leave_css) = leave_selectors(self.platform().await);
        match self.click_by_text(leave_texts).await {
            Ok(Some(text)) => tracing::debug!(button = %text, "clicked leave"),
            _ => {
                // Fall back to the CSS anchors.
                for sel in leave_css {
                    if let Ok(el) = self.page.find_element(*sel).await
                        && el.click().await.is_ok() {
                            tracing::debug!(selector = %sel, "clicked leave via selector");
                            break;
                        }
                }
            }
        }

        // Give Meet a moment to send the leave signal before the tab dies.
        tokio::time::sleep(Duration::from_millis(750)).await;
    }

    /// Drops the frames sender, closes the page, and kills the browser when it
    /// was launched (never when attached). Idempotent.
    pub async fn close(self) {
        // NOTE: `closed` is claimed at the *end*, not here. Every await below is
        // an unbounded CDP round-trip, so `session::drive` runs this whole
        // function under `BROWSER_TEARDOWN_BUDGET`; on expiry the future is
        // dropped mid-flight, which drops `self` and runs the destructor.
        // Claiming the teardown up front would make that destructor a no-op and
        // leak exactly what it exists to reclaim — the temp profile and, in
        // attach mode, the orphan tab on the shared browser. Setting the flag
        // last means a completed close() still suppresses the destructor, and a
        // cancelled one still gets best-effort cleanup.
        if let Some(task) = self.capture_task.lock().await.take() {
            // Aborting drops the task's `mpsc::Sender<AudioFrame>`, which closes
            // the frames channel and lets `run_segmenter` flush.
            task.abort();
        }

        // Cloned because `MeetSession` now has a destructor, so nothing may be
        // moved out of it. `Page` is an `Arc` handle; the clone closes the same
        // target.
        if let Err(e) = self.page.clone().close().await {
            tracing::debug!(error = %e, "page was already gone at close");
        }

        let browser = self.browser.lock().await.take();
        if let Some(mut browser) = browser {
            if self.launched {
                if let Err(e) = browser.close().await {
                    tracing::debug!(error = %e, "graceful browser close failed; killing");
                }
                if let Some(Err(e)) = browser.kill().await {
                    tracing::debug!(error = %e, "browser kill failed");
                }
                if let Err(e) = browser.wait().await {
                    tracing::debug!(error = %e, "waiting on the browser child failed");
                }
            } else {
                // Attach mode: that process is somebody else's. Dropping our
                // handle detaches; killing it would take down the shared
                // tln-browser.service.
                tracing::debug!("attached browser left running");
                drop(browser);
            }
        }

        self.handler_task.abort();

        if let Some(dir) = &self.owned_user_data_dir {
            remove_profile_dir(dir).await;
        }

        // Teardown is complete: suppress the destructor that runs as `self`
        // goes out of scope here.
        self.closed
            .store(true, std::sync::atomic::Ordering::SeqCst);
    }

    // -- internals ----------------------------------------------------------

    /// Evaluates an expression and deserializes its (object) result.
    async fn eval<T: DeserializeOwned>(&self, expr: String) -> Result<T> {
        let result = self
            .page
            .evaluate_expression(expr)
            .await
            .map_err(|e| anyhow!("page evaluate failed: {e}"))?;
        result
            .into_value::<T>()
            .map_err(|e| anyhow!("unexpected shape from the page: {e}"))
    }

    /// [`MeetSession::eval`] with transient user activation on the frame.
    ///
    /// Only the capture injection needs this, and it needs it absolutely:
    /// `getDisplayMedia` is gated on user activation and returns
    /// `NotAllowedError` without it. Everything else on the page is a DOM read
    /// or a click and must keep using plain `eval`, because a spurious gesture
    /// can dismiss Meet's own transient UI.
    async fn eval_with_gesture<T: DeserializeOwned>(&self, expr: String) -> Result<T> {
        let params = chromiumoxide::cdp::js_protocol::runtime::EvaluateParams::builder()
            .expression(expr)
            .user_gesture(true)
            .await_promise(true)
            .return_by_value(true)
            .build()
            .map_err(|e| anyhow!("could not build the evaluate params: {e}"))?;
        let result = self
            .page
            .evaluate(params)
            .await
            .map_err(|e| anyhow!("page evaluate failed: {e}"))?;
        result
            .into_value::<T>()
            .map_err(|e| anyhow!("unexpected shape from the page: {e}"))
    }

    /// Active speaker's display name, or `None`.
    ///
    /// Only the PulseAudio transport needs this: the other two taps resolve the
    /// speaker in the page where the audio already is. Swallows every error on
    /// purpose -- an unreadable DOM costs a speaker label, never a recording.
    async fn current_speaker(&self) -> (Option<String>, String) {
        #[derive(Deserialize)]
        struct Reply {
            #[serde(default)]
            speaker: Option<String>,
            #[serde(default)]
            hits: Vec<i64>,
            #[serde(default)]
            tiles: u64,
        }
        let expr = speaker_script(self.platform().await);
        match self.eval::<Reply>(expr).await {
            Ok(r) => {
                let hits = r
                    .hits
                    .iter()
                    .map(|n| n.to_string())
                    .collect::<Vec<_>>()
                    .join("/");
                (r.speaker, format!("{hits} tiles={}", r.tiles))
            }
            Err(_) => (None, "eval-failed".to_string()),
        }
    }

    /// Dumps what the in-call DOM actually offers for speaker attribution.
    ///
    /// For `doctor`, and for exactly one reason: on 6 Aug 2026 a whole meeting
    /// came back with every line labelled "Unknown", and the only honest way to
    /// fix that is to look at the markup Meet is shipping TODAY rather than
    /// guess at new selectors. Reports the current selectors' hit counts
    /// alongside the `data-*` attributes that actually exist on the page, most
    /// common first, so a drifted attribute name is visible immediately.
    pub async fn dump_speaker_markup(&self) -> Result<serde_json::Value> {
        let (speaking, tile, name) = speaker_selectors(self.platform().await);
        let expr = js::SPEAKER_MARKUP
            .replace("__MEETBOT_SPEAKING_SELECTORS__", &json_list(speaking))
            .replace("__MEETBOT_SPEAKER_TILE_SELECTORS__", &json_list(tile))
            .replace("__MEETBOT_SPEAKER_NAME_SELECTORS__", &json_list(name));
        self.eval::<serde_json::Value>(expr).await
    }

    /// Platform of the meeting this session joined. Defaults to Google Meet
    /// before `join()` has run, which keeps every pre-join caller (and the whole
    /// existing Meet path) behaving exactly as it did.
    async fn platform(&self) -> Platform {
        self.nav_platform
            .lock()
            .await
            .unwrap_or(Platform::GoogleMeet)
    }

    /// One DOM read answering every state-machine question, against whichever
    /// platform's selector table this session is driving.
    async fn probe(&self) -> Result<PageProbe> {
        self.eval(probe_expr(&probe_spec(self.platform().await)))
            .await
    }

    /// Clicks the first control matching any of `texts`; `Ok(None)` when none of
    /// them is on the page right now.
    async fn click_by_text(&self, texts: &[&str]) -> Result<Option<String>> {
        let expr = js::CLICK_BY_TEXT
            .replace("__NORM__", js::NORM)
            .replace("__TEXTS__", &json_list(texts));
        let reply: ClickReply = self.eval(expr).await?;
        Ok(reply.clicked)
    }

    /// Reports whether any of `texts` is on the page, WITHOUT clicking it.
    async fn find_by_text(&self, texts: &[&str]) -> Result<Option<String>> {
        let expr = js::FIND_BY_TEXT
            .replace("__NORM__", js::NORM)
            .replace("__TEXTS__", &json_list(texts));
        let reply: ClickReply = self.eval(expr).await?;
        Ok(reply.clicked)
    }

    /// Clicks the first visible element matching any of `candidates`, returning
    /// the selector that matched. `Ok(None)` means none of them is on the page
    /// right now — not an error.
    ///
    /// Used by the Teams path, whose join control is anchored on `data-tid`
    /// rather than on its accessible text, and which must be clicked without
    /// consulting `disabled` (it never carries it, VERIFIED).
    async fn click_by_selector(&self, candidates: &[&str]) -> Result<Option<String>> {
        let expr = js::CLICK_BY_SELECTOR.replace("__SELS__", &json_list(candidates));
        let reply: ClickReply = self.eval(expr).await?;
        Ok(reply.clicked)
    }

    /// Current value of the first matching field, or `None` when no candidate
    /// is on the page.
    async fn read_field(&self, candidates: &[&str]) -> Result<Option<String>> {
        let expr = js::READ_FIELD.replace("__SELS__", &json_list(candidates));
        let reply: FieldReply = self.eval(expr).await?;
        Ok(reply.found.then_some(reply.value))
    }

    /// Writes `value` into the first matching field through the prototype's own
    /// value setter, then fires bubbling `input` + `change`.
    ///
    /// This is the ONLY thing that works on Teams' display-name box: it is a
    /// controlled React input, and both `element.value = x` and synthesized key
    /// events leave React's internal state untouched, so the name silently
    /// reverts. `Ok(false)` means no candidate matched.
    async fn set_field(&self, candidates: &[&str], value: &str) -> Result<bool> {
        let expr = js::SET_FIELD
            .replace("__SELS__", &json_list(candidates))
            .replace("__VALUE__", &json_str(value));
        let reply: OkReply = self.eval(expr).await?;
        Ok(reply.ok)
    }

    /// Types `value` into the first field matching `candidates`.
    ///
    /// Prefers real key events (Meet's state only updates on those), and falls
    /// back to native-setter injection when the value did not stick. `Ok(false)`
    /// means no such field exists on this render — not an error.
    async fn fill_field(&self, candidates: &[&str], value: &str) -> Result<bool> {
        let read = js::READ_FIELD.replace("__SELS__", &json_list(candidates));
        let before: FieldReply = self.eval(read.clone()).await?;
        if !before.found {
            return Ok(false);
        }
        if before.value.trim() == value.trim() {
            return Ok(true);
        }

        for sel in candidates {
            let Ok(el) = self.page.find_element(*sel).await else {
                continue;
            };
            let _ = el.click().await;
            if el.type_str(value).await.is_ok() {
                break;
            }
        }

        let after: FieldReply = self.eval(read).await?;
        if after.value.trim() == value.trim() {
            return Ok(true);
        }

        let set = js::SET_FIELD
            .replace("__SELS__", &json_list(candidates))
            .replace("__VALUE__", &json_str(value));
        let reply: OkReply = self.eval(set).await?;
        Ok(reply.ok)
    }

    /// True while the page is still on the meeting we joined. Both platforms
    /// bounce a denied or ejected guest away from the meeting, which is a
    /// reliable exit signal even when the on-screen copy has changed.
    ///
    /// **Teams needs its own answer here, and getting it wrong is what made
    /// every Teams meeting disappear.** Meet keeps the meeting code in the URL
    /// for the whole call, so a substring test is enough. Teams does not: the
    /// five-hop chain lands on `/light-meetings/launch?...&coords=<BASE64>` and
    /// the code is only present base64-encoded inside `coords`. The old
    /// substring test therefore read a *successful* landing as "navigated away",
    /// returned `Admission::Denied`, and finished the session `completed` with
    /// zero segments — which the Python client files as `skipped_not_admitted`
    /// with a **green** heartbeat. See [`crate::teams::url_contains_code`].
    async fn on_meeting_url(&self, current: &str) -> bool {
        let platform = self.platform().await;

        // Prefer the id we were handed; fall back to slicing the URL we
        // navigated to, which is what this did before `nav_code` existed.
        let code = match self.nav_code.lock().await.clone() {
            Some(code) => code,
            None => {
                let Some(expected) = self.nav_url.lock().await.clone() else {
                    return true;
                };
                expected
                    .rsplit('/')
                    .next()
                    .map(|tail| tail.split('?').next().unwrap_or(tail).to_string())
                    .unwrap_or_default()
            }
        };
        if code.is_empty() {
            return true;
        }

        match platform {
            Platform::GoogleMeet => current.contains(&code),
            Platform::Teams => teams::url_contains_code(current, &code),
        }
    }
}

/// Last-resort cleanup for the paths [`MeetSession::close`] never reaches: a
/// panic in `session::run`, a task abort, an `Err(?)` that drops the session
/// early, or a hard process exit.
///
/// Two things leak without this, and both leak *silently*:
///
/// * **The temp profile.** `/tmp/meetbot-chrome-<uuid>/` is tens of MB. Nothing
///   else on the box removes them, so a crash loop fills the disk.
/// * **In ATTACH mode, the Meet tab.** `close()` is where `page.close()` lives,
///   so a skipped `close()` leaves an orphan tab on the *shared*
///   `tln-browser.service` — one live renderer per session, each roughly the
///   500 MB this project exists to eliminate. Killing that browser is never an
///   option (it is somebody else's, and the rollback path), so closing our own
///   target is the only lever there is.
///
/// A destructor cannot await, so both are best-effort: the page close is handed
/// to the runtime when one is still up, and the directory removal is spawned
/// rather than blocking a worker thread. [`sweep_stale_profiles`] at startup is
/// the backstop for whatever this misses.
impl Drop for MeetSession {
    fn drop(&mut self) {
        // `close()` already did all of this properly.
        if self.closed.load(std::sync::atomic::Ordering::SeqCst) {
            return;
        }

        tracing::warn!(
            attached = !self.launched,
            "MeetSession dropped without close(); running best-effort cleanup"
        );

        self.handler_task.abort();
        if let Ok(mut guard) = self.capture_task.try_lock()
            && let Some(task) = guard.take()
        {
            task.abort();
        }

        // Attach mode only: drop our tab off the shared browser. In launch mode
        // the whole child dies with the `Browser` handle (chromiumoxide sets
        // `kill_on_drop`), so closing the page first buys nothing.
        let runtime = tokio::runtime::Handle::try_current().ok();
        if !self.launched && let Some(handle) = &runtime {
            let page = self.page.clone();
            handle.spawn(async move {
                if let Err(e) = page.close().await {
                    tracing::debug!(error = %e, "orphan page close failed on drop");
                }
            });
        }

        let Some(dir) = self.owned_user_data_dir.clone() else {
            return;
        };
        match &runtime {
            // Chrome's crashpad handler outlives the process by a few hundred
            // ms, so the first `remove_dir_all` usually loses to `ENOTEMPTY`.
            // The async helper retries; blocking a worker thread here to do the
            // same would be worse than the leak it prevents.
            Some(handle) => {
                handle.spawn(async move { remove_profile_dir(&dir).await });
            }
            None => {
                let _ = std::fs::remove_dir_all(&dir);
            }
        }
    }
}

/// Removes throwaway Chrome profiles left behind by a previous meetbot process.
///
/// Called once per process from [`MeetSession::launch`]; also `pub` so `main.rs`
/// can run it at boot next to `db.sweep_stale()`. Only touches directories under
/// the temp dir whose name starts with [`PROFILE_PREFIX`] and whose mtime is
/// older than [`PROFILE_STALE_AFTER`], so a profile belonging to a live session
/// (Chrome keeps writing to it) is never pulled out from under it. Returns the
/// number of directories removed.

pub fn sweep_stale_profiles() -> usize {
    sweep_stale_profiles_in(&std::env::temp_dir())
}

/// [`sweep_stale_profiles`] against an explicit root, so the sweep can be tested
/// without touching the real `/tmp` (where a live session's profile lives).
fn sweep_stale_profiles_in(root: &std::path::Path) -> usize {
    let Ok(entries) = std::fs::read_dir(root) else {
        return 0;
    };
    let now = std::time::SystemTime::now();
    let mut removed = 0usize;

    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        if !name.starts_with(PROFILE_PREFIX) {
            continue;
        }
        let Ok(meta) = entry.metadata() else { continue };
        if !meta.is_dir() {
            continue;
        }
        // A profile we cannot date is left alone: deleting a live session's
        // profile is far worse than leaving a stale one on disk.
        let stale = meta
            .modified()
            .ok()
            .and_then(|m| now.duration_since(m).ok())
            .is_some_and(|age| age >= PROFILE_STALE_AFTER);
        if !stale {
            continue;
        }
        match std::fs::remove_dir_all(entry.path()) {
            Ok(()) => removed += 1,
            Err(e) => tracing::debug!(
                error = %e,
                dir = %entry.path().display(),
                "could not sweep a stale chrome profile"
            ),
        }
    }
    removed
}

/// Normalises a caller-supplied in-call ceiling to whole seconds.
///
/// Zero means "no cap" to a naive reader, and an unbounded call is precisely the
/// failure this cap exists to prevent, so zero falls back to the default instead
/// of disabling the backstop.
fn clamp_max_call_secs(d: Duration) -> u64 {
    match d.as_secs() {
        0 => DEFAULT_MAX_CALL_DURATION.as_secs(),
        secs => secs,
    }
}

/// The in-call ceiling for a fresh session: [`DEFAULT_MAX_CALL_DURATION`] unless
/// `MEETBOT_MAX_CALL_MIN` overrides it. An unparseable or zero value falls back
/// to the default rather than uncapping the call.
fn default_max_call_duration() -> Duration {
    match std::env::var(MAX_CALL_ENV) {
        Ok(raw) => match raw.trim().parse::<u64>() {
            Ok(min) if min > 0 => Duration::from_secs(min * 60),
            _ => {
                tracing::warn!(
                    value = %raw,
                    "ignoring an invalid {MAX_CALL_ENV}; using the default cap"
                );
                DEFAULT_MAX_CALL_DURATION
            }
        },
        Err(_) => DEFAULT_MAX_CALL_DURATION,
    }
}

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

/// Chromium flags every meetbot session needs, as `(key, optional value)`.
///
/// **LANDMINE — write flags here WITHOUT the leading `--`.** chromiumoxide's
/// `Arg` takes a bare key plus values and formats them as `--{key}={values}`;
/// its `From<String>` impl uses the *entire* string as the key. Passing
/// `"--autoplay-policy=no-user-gesture-required"` therefore puts
/// `----autoplay-policy=no-user-gesture-required` on the command line, which
/// Chrome silently ignores. That failure is invisible and catastrophic: the
/// autoplay flag never applies, the page's AudioContext stays `suspended`
/// forever, `onaudioprocess` never fires, and EVERY meeting finishes with zero
/// segments — which the Python client files as `skipped_not_admitted` rather
/// than as an error. Verified: bare-key/tuple form gives `state: "running"`,
/// the `"--flag=value"` string form gives `state: "suspended"`.
///
/// `--no-sandbox`, `--user-data-dir` and the window size are emitted by
/// `BrowserConfig::launch` from the builder settings, so they are not repeated
/// here. `--headless=new` is passed at the launch site rather than from this
/// table, because selecting headless through chromiumoxide would drag
/// `--mute-audio` in with it -- see the comment there.
///
/// **There must be no `mute-audio` entry here, in any form.** It carried
/// `Some("false")` for months, which reads as "audio on" and is not: the flag
/// is presence-based, so `--mute-audio=false` mutes Chrome's OUTPUT exactly as
/// hard as `--mute-audio`, and that silences the sink the PulseAudio capture
/// records from. `launch_flags_never_mute_audio` guards this.
pub const LAUNCH_FLAGS: &[(&str, Option<&str>)] = &[
    // Auto-accept the mic/cam permission prompt instead of blocking on it.
    ("use-fake-ui-for-media-stream", None),
    // A synthetic capture device. It renders as a green test pattern, so the
    // camera MUST be turned off before joining (the join loop verifies this and
    // will not click Join while the camera is still on). It is kept rather than
    // dropped because with no camera at all, Meet's green room reads "Camera not
    // found" and the layout shifts. Note (measured 21 Jul, operator present on
    // the same account): the fake device does NOT surface "Join here too" —
    // Google buries it inside the collapsed "Other ways to join" disclosure and
    // shows only "Switch here" regardless. The join loop must expand that
    // disclosure to reach it. This is Google's deliberate same-account UX and is
    // inherently fragile; a separate bot Google account avoids the whole class.
    ("use-fake-device-for-media-stream", None),
    // Remote audio elements have to start on their own; there is no user gesture
    // in a headless session. Without this the AudioContext never leaves
    // `suspended` and no audio is ever captured.
    ("autoplay-policy", Some("no-user-gesture-required")),
    // Resolves the getDisplayMedia surface picker headlessly when the request
    // carries `preferCurrentTab: true`, which is how `assets/capture.js` gets
    // the tab's own audio. Without it the promise hangs on a picker no one can
    // click and capture falls back to the DOM tap that broke on 5 Aug 2026.
    //
    // Bare key, no leading `--` — see the LANDMINE note above this table.
    ("auto-accept-this-tab-capture", None),
    // /dev/shm is 64 MB in most containers and Chrome crashes on it.
    ("disable-dev-shm-usage", None),
    ("disable-gpu", None),
    ("disable-software-rasterizer", None),
    ("disable-notifications", None),
    ("disable-popup-blocking", None),
    ("no-first-run", None),
    ("no-default-browser-check", None),
    // ROOT CAUSE of the 2026-07-19 "You can't join this video call" outage.
    //
    // chromiumoxide appends `--enable-automation` unconditionally from its own
    // `DEFAULT_ARGS` (config.rs), and we never disable those. That flag makes
    // Blink expose `navigator.webdriver === true`, which Google Meet treats as
    // a hard bot signal: it refuses the join before rendering a green room at
    // all — no name field, no "Ask to join", just the interstitial about five
    // seconds after navigation. This flag is the documented antidote; it turns
    // `navigator.webdriver` back to `false`.
    //
    // Measured against a live meeting on 2026-07-19, holding everything else
    // equal (all four runs, same call, same minute):
    //   webdriver=true,  UA Chrome/149  -> REFUSED
    //   webdriver=false, UA Chrome/149  -> green room
    //   webdriver=false, UA Chrome/128  -> green room  (stale UA is survivable)
    //   webdriver=false, UA HeadlessChrome -> REFUSED  (headless token is not)
    //
    // So there are two INDEPENDENTLY FATAL signals — `navigator.webdriver` and
    // the `HeadlessChrome` UA token — and meetbot was tripping both. Do not
    // remove this flag on the grounds that the user-agent is now correct; they
    // are separate gates and each one alone is enough to block every join.
    // vexa clears the same signal via puppeteer-extra-stealth, which is why it
    // joins the identical meeting while meetbot could not.
    ("disable-blink-features", Some("AutomationControlled")),
    // NOTE: `user-agent` is deliberately NOT here. It cannot be a constant —
    // it has to carry the running binary's real version or Meet refuses the
    // join (see the LANDMINE on `FALLBACK_USER_AGENT`). `MeetSession::launch`
    // appends it from `user_agent_for_binary`.
    ("lang", Some("en-US")),
];

/// Deletes a throwaway Chrome profile, retrying briefly.
///
/// Chrome's crashpad handler and lock-file teardown outlive `wait()` on the
/// parent by a few hundred milliseconds, so a single `remove_dir_all` loses the
/// race and fails with `ENOTEMPTY` — observed on every clean shutdown. Failing
/// to clean up is not worth erroring a finished session over, so the last
/// attempt only logs.
async fn remove_profile_dir(dir: &std::path::Path) {
    const ATTEMPTS: u32 = 5;
    for attempt in 1..=ATTEMPTS {
        match std::fs::remove_dir_all(dir) {
            Ok(()) => return,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return,
            Err(e) if attempt == ATTEMPTS => {
                // Warn, not debug: this used to give up silently, and nothing
                // else on the box removes these. `sweep_stale_profiles()` will
                // collect it on the next process start, but a run of these lines
                // means Chrome is not letting go of its profile and the disk is
                // filling up between restarts.
                tracing::warn!(
                    error = %e,
                    dir = %dir.display(),
                    "could not remove the temp profile after {ATTEMPTS} attempts; \
                     leaving it for the next startup sweep"
                );
            }
            Err(_) => tokio::time::sleep(Duration::from_millis(200 * attempt as u64)).await,
        }
    }
}

/// The URL the Google Meet join path navigates to. `?hl=en` pins the UI
/// language so the English text anchors in [`selectors`] keep matching
/// regardless of host locale.
///
/// Teams does not come through here: it has no `?hl=` equivalent (the language
/// is pinned by [`ACCEPT_LANGUAGE`] instead) and it needs `?p=<passcode>`, so
/// its URL is built by [`crate::teams::join_url`] which takes the passcode.
fn join_url(key: &MeetingKey) -> String {
    let base = key.url();
    match key.platform {
        Platform::GoogleMeet => {
            if base.contains('?') {
                format!("{base}&hl=en")
            } else {
                format!("{base}?hl=en")
            }
        }
        Platform::Teams => base,
    }
}

/// `assets/capture.js` with its `__MEETBOT_*__` tokens filled from the selector
/// table, so the injected script carries no markup knowledge of its own.
///
/// The tap itself is platform-agnostic — it walks the page's media elements —
/// so Teams reuses it unchanged. Only the speaker-attribution selectors differ,
/// and those come from [`speaker_selectors`].
pub fn capture_script(platform: Platform) -> String {
    fill_selectors(js::CAPTURE, platform)
        .replace("__MEETBOT_BINDING__", &json_str(AUDIO_BINDING))
        .replace("__MEETBOT_FRAME_SAMPLES__", &FRAME_SAMPLES.to_string())
        .replace(
            "__MEETBOT_TARGET_RATE__",
            &crate::audio::SAMPLE_RATE.to_string(),
        )
}

/// `assets/capture_ws.js` bound to a live ingest URL and the same canonical
/// selector table the CDP tap uses. Both transports therefore share one source
/// of markup knowledge — `selectors::` — so a DOM fix lands in both at once.
fn capture_ws_script(platform: Platform, ingest_url: &str) -> String {
    fill_selectors(js::CAPTURE_WS, platform).replace("__MEETBOT_INGEST_URL__", ingest_url)
}

/// Reads the active speaker's name off the page, and nothing else.
///
/// The two taps resolve the speaker inside the page, per frame, because the
/// audio is already there. The PulseAudio transport captures outside the
/// browser entirely, so it has no such hook -- but Meet's DOM is still the only
/// place a participant's NAME exists. This is the smallest script that answers
/// just that question, evaluated once per watch tick.
///
/// Deliberately a copy of `capture.js`'s `nameFor`/`currentSpeaker` logic
/// rather than an import: both are filled from `selectors::` by
/// [`fill_selectors`], so the markup knowledge still lives in exactly one
/// place, which is the rule that matters.
fn speaker_script(platform: Platform) -> String {
    fill_selectors(js::SPEAKER, platform)
}

/// Substitutes the speaker-attribution selector tokens shared by both taps.
/// Going through `serde_json` means a selector containing a quote can never
/// break out of the template.
fn fill_selectors(js: &str, platform: Platform) -> String {
    let (speaking, tile, name) = speaker_selectors(platform);
    js.replace("__MEETBOT_SPEAKING_SELECTORS__", &json_list(speaking))
        .replace("__MEETBOT_SPEAKER_NAME_SELECTORS__", &json_list(name))
        .replace("__MEETBOT_SPEAKER_TILE_SELECTORS__", &json_list(tile))
}

/// Fills the shared [`js::PROBE`] template from one platform's [`ProbeSpec`].
/// Free-standing so the tests can render it without a browser.
fn probe_expr(spec: &ProbeSpec) -> String {
    let diag = serde_json::to_string(&spec.diag).unwrap_or_else(|_| "[]".to_string());
    js::PROBE
        .replace("__NORM__", js::NORM)
        .replace("__IN_CALL__", &json_list(spec.in_call))
        .replace("__NOT_IN_CALL__", &json_list(spec.not_in_call))
        .replace("__TILES__", &json_list(spec.tiles))
        .replace("__COUNTERS__", &json_list(spec.counters))
        .replace("__WAITING__", &json_list(spec.waiting_texts))
        .replace("__WAITING_SEL__", &json_list(spec.waiting_sel))
        .replace("__DENIED__", &json_list(spec.denied_texts))
        .replace("__DENIED_SEL__", &json_list(spec.denied_sel))
        .replace("__ENDED__", &json_list(spec.ended_texts))
        .replace("__REMOVED__", &json_list(spec.removed_texts))
        .replace("__DIAG__", &diag)
}

/// A URL safe to put in a log line: the `?p=<passcode>` value is replaced.
/// Meeting passcodes are shared secrets and logs are read over someone's
/// shoulder; nothing downstream needs the plaintext.
fn redact_passcode(url: &str) -> String {
    match url.split_once("?p=") {
        Some((head, _)) => format!("{head}?p=<redacted>"),
        None => url.to_string(),
    }
}

/// The diagnostic message a failed Teams join dance returns.
///
/// Requirement: a Teams meeting that fails overnight has to leave an actionable
/// trace rather than a silent skip. This names the stage, quotes the last DOM
/// read, and — crucially — says of each missing selector group whether it was
/// ever observed live. A **VERIFIED** group that stopped matching means
/// Microsoft changed their markup; an **INFERRED** group that never matched
/// means our guess was wrong from the start. Those need opposite fixes.
fn teams_stage_failure(
    stage: JoinStage,
    url: &str,
    elapsed: Duration,
    last: Option<&PageProbe>,
) -> String {
    let missing = last
        .map(|p| {
            let misses: Vec<String> = p
                .diag
                .iter()
                .filter(|(_, hit)| hit.is_none())
                .map(|(group, _)| match teams::group_verification(group) {
                    Some(v) => format!("{group} [{}]", v.label()),
                    None => group.clone(),
                })
                .collect();
            if misses.is_empty() {
                "none (every selector group matched)".to_string()
            } else {
                misses.join(", ")
            }
        })
        .unwrap_or_else(|| "unknown (no DOM read ever succeeded)".to_string());

    format!(
        "Teams join failed at stage '{}' after {}s on {url}. Selector groups that did NOT \
         match: {missing}. {} Last DOM read: {}",
        stage.as_str(),
        elapsed.as_secs(),
        stage.hint(),
        last.map(|p| format!("url={} diag={}", p.url, p.diag_line()))
            .unwrap_or_else(|| "<none>".to_string()),
    )
}

/// Pins `Accept-Language` for every request the page makes.
///
/// Teams has no `?hl=en`; its UI language is chosen from this header. See the
/// call site in [`MeetSession::launch`].
async fn set_accept_language(page: &Page) -> Result<()> {
    use chromiumoxide::cdp::browser_protocol::network::{
        DisableParams, EnableParams, Headers, SetExtraHttpHeadersParams,
    };

    // `Network.setExtraHTTPHeaders` requires the Network domain to be enabled.
    page.execute(EnableParams::default())
        .await
        .map_err(|e| anyhow!("could not enable the CDP Network domain: {e}"))?;
    let headers = Headers::new(serde_json::json!({ "Accept-Language": ACCEPT_LANGUAGE }));
    page.execute(SetExtraHttpHeadersParams::new(headers))
        .await
        .map_err(|e| anyhow!("could not set Accept-Language: {e}"))?;

    // Then disable the Network domain again. CONFIRMED ROOT CAUSE of the
    // 2026-07-22 nav-timeout regression (verified 29 Jul by on-demand dummy
    // joins: with the enable left in, every join died at chromiumoxide's 30s
    // request_timeout; with this disable added, navigation succeeds and the
    // join proceeds to the green-room step): with Network enabled, chromiumoxide's
    // single Handler stream must demux a `Network.*` event for every resource
    // the page fetches. `page.goto()` is `execute(Page.navigate).await`, which
    // waits for the navigate command's RESPONSE to route back through that same
    // Handler; when the event volume from a heavy page (Meet grew its request
    // count around that date) saturates the demux, the response is starved past
    // chromiumoxide's 30s request_timeout and every join dies with
    // `failed to navigate ... Request timed out`. Reproduced on demand via a
    // dummy /bots join; a raw-CDP probe that drains events in a tight loop never
    // hit it, which is exactly the Handler-throughput difference. The extra
    // header persists in the browser's network state after the domain is
    // disabled, so Teams' Accept-Language is unaffected; disabling only stops
    // the event stream we never consume.
    let _ = page.execute(DisableParams::default()).await;
    Ok(())
}

/// Renders a selector list as a JS array literal. Going through `serde_json`
/// means a selector containing a quote can never break out of the template.
fn json_list(items: &[&str]) -> String {
    serde_json::to_string(items).unwrap_or_else(|_| "[]".to_string())
}

/// Renders a string as a JS string literal.
fn json_str(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "\"\"".to_string())
}

/// Base64 decoder for the PCM payloads and for Teams' `coords` parameter.
/// Hand-rolled so the crate does not gain a dependency for ~30 lines; tolerant
/// of missing padding and whitespace, strict about anything else.
///
/// Accepts BOTH alphabets: standard (`+/`, what the audio tap's `btoa` emits and
/// what Teams uses) and URL-safe (`-_`). Teams percent-encodes `+` and `/` inside
/// the query string, so `coords` arrives standard once percent-decoded — but
/// accepting the URL-safe alphabet too costs two match arms and means a future
/// switch on Microsoft's side is not a silent decode failure that reads as
/// "navigated away from the meeting".
pub(crate) fn base64_decode(input: &str) -> Option<Vec<u8>> {
    fn val(b: u8) -> Option<u32> {
        match b {
            b'A'..=b'Z' => Some((b - b'A') as u32),
            b'a'..=b'z' => Some((b - b'a') as u32 + 26),
            b'0'..=b'9' => Some((b - b'0') as u32 + 52),
            b'+' | b'-' => Some(62),
            b'/' | b'_' => Some(63),
            _ => None,
        }
    }

    let mut out = Vec::with_capacity(input.len() / 4 * 3);
    let mut acc: u32 = 0;
    let mut bits: u32 = 0;

    for &b in input.as_bytes() {
        if b == b'=' || b.is_ascii_whitespace() {
            continue;
        }
        let v = val(b)?;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((acc >> bits) & 0xff) as u8);
        }
    }

    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_known_vectors() {
        assert_eq!(base64_decode("").unwrap(), Vec::<u8>::new());
        assert_eq!(base64_decode("TWE=").unwrap(), b"Ma".to_vec());
        assert_eq!(base64_decode("TWFu").unwrap(), b"Man".to_vec());
        assert_eq!(base64_decode("TWFuTWFu").unwrap(), b"ManMan".to_vec());
        // Missing padding is tolerated: a truncated frame must not take the
        // audio pump down.
        assert_eq!(base64_decode("TWE").unwrap(), b"Ma".to_vec());
        assert!(base64_decode("not base64!").is_none());
    }

    #[test]
    fn json_list_escapes_quotes() {
        assert_eq!(json_list(&["a'b"]), r#"["a'b"]"#);
        assert_eq!(json_list(&["a\"b"]), r#"["a\"b"]"#);
    }

    #[test]
    fn capture_script_leaves_no_unfilled_tokens() {
        let script = capture_script(Platform::GoogleMeet);
        assert!(
            !script.contains("__MEETBOT_"),
            "capture.js left a token unfilled"
        );
        assert!(script.contains("meetbotAudio"));
    }

    /// The 5 Aug 2026 fix, guarded at the source.
    ///
    /// Meet stopped exposing remote participant audio through media elements,
    /// so the DOM tap alone records silence in a room full of people. Tab
    /// capture is the only path that hears anyone; if these three pieces ever
    /// go missing the bot still joins, still reports `completed`, and still
    /// produces nothing.
    #[test]
    fn capture_script_asks_for_the_tabs_own_audio() {
        let script = capture_script(Platform::GoogleMeet);
        assert!(
            script.contains("getDisplayMedia"),
            "capture.js must capture the tab's output, not just media elements"
        );
        assert!(
            script.contains("preferCurrentTab"),
            "without preferCurrentTab, --auto-accept-this-tab-capture does not apply"
        );
        assert!(
            script.contains("tabState"),
            "a failed tab grab must be reportable; silent fallback is what hid the outage"
        );
    }

    /// capture_ws.js is a template, not standalone JS: an unsubstituted token is
    /// a syntax error in the page, so the tap would die on injection.
    #[test]
    fn capture_ws_script_leaves_no_unfilled_tokens() {
        let script = capture_ws_script(Platform::GoogleMeet, "ws://127.0.0.1:41000/ingest");
        assert!(
            !script.contains("__MEETBOT_SPEAK"),
            "capture_ws.js left a selector token unfilled"
        );
        assert!(
            !script.contains("__MEETBOT_SPEAKER_TILE_SELECTORS__"),
            "capture_ws.js left the tile-selector token unfilled"
        );
        assert!(script.contains("ws://127.0.0.1:41000/ingest"));
    }

    /// Both taps must be driven off the one canonical selector table, so a DOM
    /// fix never has to be applied twice.
    #[test]
    fn both_taps_share_the_selector_table() {
        let cdp = capture_script(Platform::GoogleMeet);
        let ws = capture_ws_script(Platform::GoogleMeet, "ws://127.0.0.1:1/ingest");
        for sel in selectors::SPEAKING_INDICATORS {
            assert!(cdp.contains(sel), "cdp tap is missing {sel}");
            assert!(ws.contains(sel), "ws tap is missing {sel}");
        }
        for sel in selectors::SPEAKER_TILE {
            assert!(cdp.contains(sel), "cdp tap is missing {sel}");
            assert!(ws.contains(sel), "ws tap is missing {sel}");
        }
    }

    /// The probe is a template: an unsubstituted token is a syntax error in the
    /// page, so EVERY platform's rendering has to come out clean.
    #[test]
    fn probe_template_leaves_no_unfilled_tokens() {
        for platform in [Platform::GoogleMeet, Platform::Teams] {
            let expr = probe_expr(&probe_spec(platform));
            for token in [
                "__NORM__",
                "__IN_CALL__",
                "__NOT_IN_CALL__",
                "__TILES__",
                "__COUNTERS__",
                "__WAITING__",
                "__WAITING_SEL__",
                "__DENIED__",
                "__DENIED_SEL__",
                "__ENDED__",
                "__REMOVED__",
                "__DIAG__",
            ] {
                assert!(
                    !expr.contains(token),
                    "{platform} probe left {token} unfilled"
                );
            }
        }
        assert!(probe_expr(&probe_spec(Platform::GoogleMeet)).contains("Leave call"));
    }

    #[test]
    fn join_url_pins_english_for_meet() {
        let key = MeetingKey::new(Platform::GoogleMeet, "bqy-ybgi-pbb");
        assert_eq!(join_url(&key), "https://meet.google.com/bqy-ybgi-pbb?hl=en");
    }

    #[test]
    fn launch_flags_carry_the_mandatory_media_flags() {
        for expected in [
            "use-fake-ui-for-media-stream",
            "use-fake-device-for-media-stream",
            "disable-dev-shm-usage",
        ] {
            assert!(
                LAUNCH_FLAGS.iter().any(|(k, v)| *k == expected && v.is_none()),
                "missing {expected}"
            );
        }
        assert!(
            LAUNCH_FLAGS
                .iter()
                .any(|(k, v)| *k == "autoplay-policy" && *v == Some("no-user-gesture-required")),
            "without autoplay-policy the AudioContext never leaves `suspended`"
        );
        assert!(
            LAUNCH_FLAGS
                .iter()
                .any(|(k, v)| *k == "auto-accept-this-tab-capture" && v.is_none()),
            "without auto-accept-this-tab-capture the getDisplayMedia picker never resolves \
             headlessly and every call falls back to the deaf media-element tap"
        );
    }

    /// The 6 Aug 2026 mute bug, guarded so it cannot come back.
    ///
    /// `--mute-audio` silences Chrome's audio OUTPUT, which is the signal the
    /// PulseAudio transport records. The flag is presence-based, so a value
    /// cannot disable it: this table carried `("mute-audio", Some("false"))`
    /// for months, which reads as "audio on" and mutes exactly as hard as the
    /// bare flag. Measured against a 440 Hz page at gain 0.5, reading
    /// `RDPSink.monitor`: no flag -> peak 0.5000, `=false` -> 0.0000,
    /// bare -> 0.0000.
    ///
    /// The launch site must also keep passing `--headless=new` itself rather
    /// than going through chromiumoxide's headless mode, which appends its own
    /// bare `--mute-audio` (its config.rs:426).
    #[test]
    fn launch_flags_never_mute_audio() {
        assert!(
            !LAUNCH_FLAGS.iter().any(|(k, _)| *k == "mute-audio"),
            "mute-audio must not appear here in ANY form; `=false` mutes too"
        );
    }

    /// Guards the LANDMINE on `LAUNCH_FLAGS`: a leading `--` here becomes
    /// `----flag` on Chrome's command line and is silently ignored, which
    /// silently disables audio capture for every meeting.
    #[test]
    fn launch_flags_have_no_leading_dashes() {
        for (key, value) in LAUNCH_FLAGS {
            assert!(
                !key.starts_with('-'),
                "flag key `{key}` must not carry a leading `--`"
            );
            assert!(
                !key.contains('='),
                "flag key `{key}` must not embed its value; use the tuple form"
            );
            if let Some(v) = value {
                assert!(!v.is_empty(), "flag `{key}` has an empty value");
            }
        }
    }

    // -- Teams is DRIVEN, not rejected (SPEC.md §1.3.1, v1.3) ---------------

    /// Both platforms must reach a real join path. The 400 that used to sit in
    /// front of Teams is gone; what replaced it is a second selector table and
    /// a `coords`-aware URL check, and `probe_spec` is the only dispatch point.
    #[test]
    fn both_platforms_have_a_probe_spec() {
        for platform in [Platform::GoogleMeet, Platform::Teams] {
            let spec = probe_spec(platform);
            assert!(!spec.in_call.is_empty(), "{platform} has no in-call marker");
            assert!(!spec.diag.is_empty(), "{platform} reports no diagnostics");
        }
    }

    /// Teams must be driven off `teams::selectors` and Meet off `selectors`.
    /// A leak either way is the bug the one-table rule exists to prevent, and
    /// it is invisible at runtime — it looks like a meeting that was never
    /// admitted.
    #[test]
    fn the_two_selector_tables_never_leak_into_each_other() {
        let meet = probe_expr(&probe_spec(Platform::GoogleMeet));
        let teams_expr = probe_expr(&probe_spec(Platform::Teams));

        assert!(meet.contains("Leave call"), "meet probe lost its own anchors");
        assert!(
            !meet.contains("calling-prejoin-screen"),
            "a Teams anchor leaked into the Google Meet probe"
        );

        assert!(
            teams_expr.contains("calling-retry-screen"),
            "the Teams probe must watch the verified rejection surface"
        );
        assert!(
            !teams_expr.contains("Leave call"),
            "a Google Meet anchor leaked into the Teams probe"
        );
    }

    /// The Teams probe must veto an in-call claim whenever the (VERIFIED)
    /// pre-join or rejection surface is on screen. Without the veto, an
    /// over-broad `IN_CALL_ROOT` guess makes the bot inject the audio tap into
    /// the green room and record a call it never joined.
    #[test]
    fn teams_probe_carries_the_not_in_call_veto() {
        let spec = probe_spec(Platform::Teams);
        assert!(!spec.not_in_call.is_empty());
        let expr = probe_expr(&spec);
        assert!(expr.contains("var NOT_IN_CALL ="));
        assert!(expr.contains("present(IN_CALL) && !present(NOT_IN_CALL)"));
        // Meet keeps its historical behaviour: no veto list, so the guard is
        // inert there.
        assert!(probe_spec(Platform::GoogleMeet).not_in_call.is_empty());
    }

    /// The verified rejection surface is a DOM subtree, not a phrase — Teams
    /// renders identical copy for a bad id, a wrong passcode and an ended
    /// meeting, so branching on the text would be branching on nothing.
    #[test]
    fn teams_denial_is_anchored_on_the_dom_not_the_copy() {
        let spec = probe_spec(Platform::Teams);
        assert!(
            spec.denied_sel
                .contains(&"[data-tid='calling-retry-screen']"),
            "the retry screen is the terminal rejection signal"
        );
    }

    /// Teams' final URL hides the meeting id inside base64 `coords`, so the
    /// audio tap and the state machine must both be told which platform they
    /// are driving. Defaulting to Meet keeps every pre-join caller unchanged.
    #[test]
    fn capture_scripts_follow_the_platform() {
        let meet_tap = capture_script(Platform::GoogleMeet);
        let teams_tap = capture_script(Platform::Teams);
        assert!(meet_tap.contains("data-is-speaking='true'"));
        assert!(teams_tap.contains("participant-speaking-indicator"));
        assert!(
            !teams_tap.contains("[data-self-name]"),
            "a Google Meet speaker anchor leaked into the Teams tap"
        );
    }

    /// A passcode is a shared secret and the join URL is logged on every join.
    #[test]
    fn the_logged_join_url_never_carries_the_passcode() {
        let url = crate::teams::join_url("1234567890123", Some("AbCd1234EfGh"));
        let logged = redact_passcode(&url);
        assert!(!logged.contains("AbCd1234EfGh"));
        assert!(logged.starts_with("https://teams.microsoft.com/meet/1234567890123"));
        // A URL with no passcode is passed through untouched.
        let plain = crate::teams::join_url("1234567890123", None);
        assert_eq!(redact_passcode(&plain), plain);
    }

    /// The whole point of the diagnostic path: a failure must name the stage,
    /// the groups that missed, AND whether each was ever observed live. A
    /// VERIFIED group going dark is Microsoft changing markup; an INFERRED one
    /// never matching is our guess being wrong. Opposite fixes.
    #[test]
    fn a_failed_teams_stage_names_the_stage_and_the_missing_selectors() {
        let mut diag = std::collections::BTreeMap::new();
        diag.insert("prejoin_root".to_string(), None);
        diag.insert(
            "join_button".to_string(),
            Some("button[data-tid='prejoin-join-button']".to_string()),
        );
        diag.insert("in_call_root".to_string(), None);
        let probe = PageProbe {
            url: "https://teams.microsoft.com/light-meetings/launch?coords=QUJD".to_string(),
            in_call: false,
            waiting: None,
            denied: None,
            ended: None,
            removed: None,
            participants: None,
            diag,
        };

        let msg = teams_stage_failure(
            JoinStage::PreJoin,
            "https://teams.microsoft.com/meet/1234567890123",
            Duration::from_secs(90),
            Some(&probe),
        );

        assert!(msg.contains("stage 'prejoin'"), "{msg}");
        assert!(msg.contains("prejoin_root [VERIFIED]"), "{msg}");
        assert!(msg.contains("in_call_root [INFERRED"), "{msg}");
        assert!(
            !msg.contains("join_button ["),
            "a group that matched must not be listed as missing: {msg}"
        );
        // The matched selector still shows up in the trailing DOM read.
        assert!(msg.contains("prejoin-join-button"), "{msg}");
    }

    /// With no DOM read at all (the page never became evaluatable), the message
    /// must say so rather than claim every selector missed.
    #[test]
    fn a_teams_failure_with_no_dom_read_says_so() {
        let msg = teams_stage_failure(
            JoinStage::Redirect,
            "https://teams.microsoft.com/meet/1234567890123",
            Duration::from_secs(90),
            None,
        );
        assert!(msg.contains("stage 'redirect'"), "{msg}");
        assert!(msg.contains("no DOM read ever succeeded"), "{msg}");
    }

    #[test]
    fn diag_line_distinguishes_hits_from_misses() {
        let mut diag = std::collections::BTreeMap::new();
        diag.insert("a".to_string(), Some("sel-a".to_string()));
        diag.insert("b".to_string(), None);
        let probe = PageProbe {
            url: String::new(),
            in_call: false,
            waiting: None,
            denied: None,
            ended: None,
            removed: None,
            participants: None,
            diag,
        };
        assert_eq!(probe.diag_line(), "a=sel-a b=MISS");
        assert!(probe.matched("a"));
        assert!(!probe.matched("b"));
        assert!(!probe.matched("never-heard-of-it"));
    }

    /// Teams has no `?hl=en`, so the header is the only lever on UI language.
    #[test]
    fn teams_language_is_pinned_by_header_and_launch_flag() {
        assert_eq!(ACCEPT_LANGUAGE, "en-US,en");
        assert!(
            LAUNCH_FLAGS
                .iter()
                .any(|(k, v)| *k == "lang" && *v == Some("en-US")),
            "--lang=en-US is the other half of pinning the Teams UI language"
        );
    }

    // -- Wall-clock cap on the in-call phase (review finding 2) -------------

    /// Four hours, and never zero: with the cap disabled a single Google UI
    /// release that drifts PARTICIPANT_COUNT and PARTICIPANT_TILES together
    /// strands the bot in an endless call holding a live Chrome, a concurrency
    /// permit and a non-terminal DB row.
    #[test]
    fn max_call_duration_defaults_to_four_hours() {
        assert_eq!(DEFAULT_MAX_CALL_DURATION, Duration::from_secs(4 * 60 * 60));
        assert!(!DEFAULT_MAX_CALL_DURATION.is_zero());
    }

    #[test]
    fn max_call_duration_is_never_uncapped() {
        assert_eq!(
            clamp_max_call_secs(Duration::ZERO),
            DEFAULT_MAX_CALL_DURATION.as_secs(),
            "zero must not be read as `no cap`"
        );
        assert_eq!(clamp_max_call_secs(Duration::from_secs(90 * 60)), 5_400);
    }

    // -- Profile sweep (review finding 4) -----------------------------------

    /// A profile older than the threshold belongs to a dead process and must be
    /// collected; a fresh one may belong to a live session and must not be.
    #[test]
    fn sweep_removes_stale_profiles_and_spares_fresh_ones() {
        use std::time::SystemTime;

        let root = std::env::temp_dir().join(format!("meetbot-sweep-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();

        let stale = root.join(format!("{PROFILE_PREFIX}{}", uuid::Uuid::new_v4()));
        let fresh = root.join(format!("{PROFILE_PREFIX}{}", uuid::Uuid::new_v4()));
        let unrelated = root.join("someone-elses-tempdir");
        for d in [&stale, &fresh, &unrelated] {
            std::fs::create_dir_all(d).unwrap();
            // Non-empty, like a real profile: `remove_dir_all` is the operation
            // under test, not `remove_dir`.
            std::fs::write(d.join("Preferences"), b"{}").unwrap();
        }

        let old = SystemTime::now() - (PROFILE_STALE_AFTER + Duration::from_secs(60));
        let times = std::fs::FileTimes::new().set_modified(old);
        std::fs::File::options()
            .write(true)
            .open(&stale)
            .or_else(|_| std::fs::File::open(&stale))
            .unwrap()
            .set_times(times)
            .unwrap();

        let removed = sweep_stale_profiles_in(&root);

        assert!(!stale.exists(), "a stale profile must be swept");
        assert!(fresh.exists(), "a live session's profile must survive");
        assert!(unrelated.exists(), "only meetbot profiles may be touched");
        assert_eq!(removed, 1);

        let _ = std::fs::remove_dir_all(&root);
    }

    /// Sweeping the real temp dir must be safe to call blind at startup.
    #[test]
    fn sweep_on_a_missing_root_is_a_no_op() {
        let missing = std::env::temp_dir().join(format!("meetbot-absent-{}", uuid::Uuid::new_v4()));
        assert_eq!(sweep_stale_profiles_in(&missing), 0);
    }

    #[test]
    fn user_agent_does_not_advertise_headless() {
        assert!(
            !FALLBACK_USER_AGENT.contains("HeadlessChrome"),
            "Meet serves a degraded, audio-less page to headless user agents"
        );
        assert_eq!(
            strip_headless_token(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) \
                 HeadlessChrome/149.0.7827.55 Safari/537.36"
            ),
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) \
             Chrome/149.0.7827.55 Safari/537.36",
            "de-headlessing must rewrite only the product token, never the version"
        );
    }

    /// Guards the root cause of the 2026-07-19 outage. chromiumoxide's
    /// `DEFAULT_ARGS` always append `--enable-automation`, which sets
    /// `navigator.webdriver = true`; Meet refuses every join on that signal
    /// alone. Only this flag clears it, and nothing else in the tree does.
    #[test]
    fn automation_controlled_is_disabled() {
        assert!(
            LAUNCH_FLAGS
                .iter()
                .any(|(k, v)| *k == "disable-blink-features"
                    && v.is_some_and(|v| v.contains("AutomationControlled"))),
            "without this, navigator.webdriver is true and Meet refuses the join \
             before the green room renders"
        );
    }

    /// Guards the LANDMINE on `FALLBACK_USER_AGENT`: a constant user-agent in
    /// `LAUNCH_FLAGS` is how the "You can't join this video call" outage of
    /// 2026-07-19 happened. The flag has to be derived per-binary at launch.
    #[test]
    fn user_agent_is_not_a_constant_launch_flag() {
        assert!(
            !LAUNCH_FLAGS.iter().any(|(k, _)| *k == "user-agent"),
            "the user-agent must be derived from the running binary's version, \
             not pinned in LAUNCH_FLAGS"
        );
    }

    #[test]
    fn user_agent_is_derived_from_the_binary_version() {
        let fake = std::env::temp_dir().join(format!("meetbot-ua-{}", uuid::Uuid::new_v4()));
        std::fs::write(&fake, "#!/bin/sh\necho 'Google Chrome for Testing 149.0.7827.55'\n")
            .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let ua = user_agent_for_binary(&fake).expect("should parse the version");
        assert!(ua.contains("Chrome/149.0.7827.55"), "got {ua}");
        assert!(!ua.contains("HeadlessChrome"), "got {ua}");
        let _ = std::fs::remove_file(&fake);
    }
}
