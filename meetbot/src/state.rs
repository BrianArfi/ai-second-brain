//! Shared vocabulary: `Platform`, `MeetingKey`, `Config`, `SessionPhase`,
//! `SessionCommand`, `SessionHandle`, `AppState`, `SharedState`.
//!
//! Contract: `SPEC.md` §4. Owner: the `state` builder agent.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use tokio::sync::{RwLock, Semaphore, mpsc, oneshot};
use uuid::Uuid;

use crate::db::Db;

/// Fallback when `ip route show default` cannot be parsed (SPEC.md §4).
const GATEWAY_FALLBACK: &str = "172.25.32.1";

// ---------------------------------------------------------------------------
// Platform
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Platform {
    /// wire: `"google_meet"`
    GoogleMeet,
    /// wire: `"teams"`
    Teams,
}

impl Platform {
    pub fn as_str(&self) -> &'static str {
        match self {
            Platform::GoogleMeet => "google_meet",
            Platform::Teams => "teams",
        }
    }

    pub fn parse(s: &str) -> Option<Platform> {
        match s {
            "google_meet" => Some(Platform::GoogleMeet),
            "teams" => Some(Platform::Teams),
            _ => None,
        }
    }

    /// Validates a native id for this platform.
    /// GoogleMeet: `^[a-z]{3}-[a-z]{4}-[a-z]{3}$`. Teams: `^\d{10,20}$`.
    pub fn validate_native_id(&self, id: &str) -> bool {
        match self {
            Platform::GoogleMeet => {
                let parts: Vec<&str> = id.split('-').collect();
                if parts.len() != 3 {
                    return false;
                }
                let widths = [3usize, 4, 3];
                parts.iter().zip(widths.iter()).all(|(part, want)| {
                    part.len() == *want && part.bytes().all(|b| b.is_ascii_lowercase())
                })
            }
            Platform::Teams => {
                let n = id.len();
                (10..=20).contains(&n) && id.bytes().all(|b| b.is_ascii_digit())
            }
        }
    }

    /// GoogleMeet -> `https://meet.google.com/{id}`
    /// Teams      -> `https://teams.microsoft.com/meet/{id}`
    pub fn meeting_url(&self, native_id: &str) -> String {
        match self {
            Platform::GoogleMeet => format!("https://meet.google.com/{native_id}"),
            Platform::Teams => format!("https://teams.microsoft.com/meet/{native_id}"),
        }
    }
}

impl std::fmt::Display for Platform {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

// ---------------------------------------------------------------------------
// MeetingKey
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct MeetingKey {
    pub platform: Platform,
    pub native_id: String,
}

impl MeetingKey {
    pub fn new(platform: Platform, native_id: impl Into<String>) -> Self {
        MeetingKey {
            platform,
            native_id: native_id.into(),
        }
    }

    /// `"google_meet/bqy-ybgi-pbb"`
    pub fn as_path(&self) -> String {
        format!("{}/{}", self.platform.as_str(), self.native_id)
    }

    pub fn url(&self) -> String {
        self.platform.meeting_url(&self.native_id)
    }
}

impl std::fmt::Display for MeetingKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.as_path())
    }
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

/// Every `Option<T>` field carries `#[serde(default)]` (serde does NOT make
/// Option fields optional on its own), so omitting a key in config.toml
/// yields `None`. Non-Option fields are required.
#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub http_port: u16,
    pub http_bind: String,
    /// Path to vexa_token.env; the `VEXA_API_KEY=` line is read out of it.
    pub api_key_file: PathBuf,
    /// Overrides `api_key_file` when set (tests).
    #[serde(default)]
    pub api_key: Option<String>,
    pub admin_token: String,
    pub whisper_port: u16,
    /// `None` => resolve the WSL gateway IP at runtime.
    #[serde(default)]
    pub whisper_host: Option<String>,
    pub whisper_path: String,
    pub max_concurrent_bots: usize,
    pub admission_timeout_min: u64,
    pub lonely_grace_sec: u64,
    /// How long the bot tolerates an empty room BEFORE anyone else has ever
    /// joined. This is the "arrived early, waiting for the meeting to start"
    /// case, and it must be long — the bot deliberately joins several minutes
    /// ahead so it is the account's first presence (a later human join then gets
    /// the working "Join here too"). Distinct from `lonely_grace_sec`, which is
    /// the short "everyone left" grace applied only AFTER others have appeared.
    /// Defaults to 900s (15 min) when absent.
    #[serde(default = "default_empty_room_grace_sec")]
    pub empty_room_grace_sec: u64,
    pub headless: bool,
    pub chromium_path: PathBuf,
    /// Signed-in Chrome profile each session's throwaway profile is copied from.
    ///
    /// Unset means anonymous-guest joins, which Meet's post-knock bot check
    /// auto-declines. See `meet::BrowserOptions::profile_template`.
    #[serde(default)]
    pub profile_template: Option<PathBuf>,
    /// Attach instead of launch when set.
    #[serde(default)]
    pub cdp_port: Option<u16>,
    pub bot_name: String,
    /// Holds `audio/` and `meetbot.db`.
    pub data_dir: PathBuf,
    pub db_path: PathBuf,
    /// Retained for config compatibility (SPEC.md §4) but no longer gates the
    /// startup sweep, which is unconditional — see `AppState::new`. Nothing else
    /// reads it; it is deliberately not wired to a periodic sweeper, because an
    /// age-gated sweep running against a *live* server would fail a legitimately
    /// long call mid-recording.
    pub stale_after_hours: i64,
    /// How the injected page ships PCM back to Rust. Defaults to the SPEC §7
    /// CDP binding; see [`CaptureTransport`].
    #[serde(default)]
    pub capture_transport: CaptureTransport,
    /// Whether `POST /bots` refuses (503) when `transcribe_enabled` is set and
    /// whisper is unreachable. **Default `true`** — see SPEC.md §1.3.2 for the
    /// tradeoff. `false` accepts the bot anyway (what Vexa did): the meeting is
    /// still joined and recorded, and a whisper that comes back mid-call still
    /// gets its utterances, at the cost of losing the loud up-front alarm.
    #[serde(default = "default_require_whisper")]
    pub require_whisper_for_bots: bool,
    /// Hard wall-clock ceiling on the IN-CALL phase, in whole minutes (SPEC.md
    /// §7.2). `None` — the default — leaves the session on
    /// [`crate::meet::DEFAULT_MAX_CALL_DURATION`] (4h), or on whatever the
    /// `MEETBOT_MAX_CALL_MIN` env override resolves to, so omitting the key
    /// does not silently disable that override. A configured value is clamped
    /// by [`crate::meet::MeetSession::set_max_call_duration`]; zero is clamped
    /// back to the default and is never read as "uncapped".
    #[serde(default)]
    pub max_call_duration_min: Option<u64>,
    /// PulseAudio source to record when `capture_transport = "pulse"`.
    ///
    /// Almost always the MONITOR of the sink Chrome plays into, e.g.
    /// `"RDPSink.monitor"` under WSLg. There is deliberately no default: the
    /// default Pulse source is a microphone, so guessing would record the room
    /// the laptop is in rather than the meeting, and do it silently.
    /// `python3 tools/pulse_probe.py` lists what this host offers.
    #[serde(default)]
    pub pulse_source: Option<String>,
    /// Dedicated PulseAudio sink Chrome plays into, created on demand.
    ///
    /// Set this rather than pointing `pulse_source` at a real device's monitor.
    /// On WSLg the default sink is `RDPSink`, the bridge to Windows audio;
    /// pushing a bot's meetings through it overran its queue and took the whole
    /// audio server down on 6 Aug 2026, after which every session failed at
    /// capture start. A null sink has no such bridge and cannot wedge.
    ///
    /// When set, meetbot creates it if missing and exports `PULSE_SINK` to
    /// Chrome, and `pulse_source` should be `"<pulse_sink>.monitor"`.
    #[serde(default)]
    pub pulse_sink: Option<String>,
}

/// SPEC.md §1.3.2: fail fast by default.
fn default_require_whisper() -> bool {
    true
}

/// 15 minutes: long enough to cover an early join plus a late-starting meeting,
/// short enough that a cancelled meeting frees the slot without a human.
fn default_empty_room_grace_sec() -> u64 {
    900
}

/// Transport the in-page audio tap uses to reach Rust.
///
/// SPEC.md §7 specifies the CDP binding, and that is the default and the only
/// path the acceptance tests exercise. The WebSocket transport is an
/// integrator-added fallback (see SPEC.md §7.1): `Runtime.bindingCalled`
/// delivers every frame as base64 JSON through the single CDP session that also
/// carries the DOM polling, so a long call with a slow consumer can back the
/// protocol up behind other traffic. The WebSocket path moves audio onto its own
/// socket. Switch only if the CDP path misbehaves on a real call; it is the less
/// exercised of the two.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureTransport {
    /// `Runtime.addBinding("meetbotAudio")` — SPEC §7, the default.
    #[default]
    Cdp,
    /// `assets/capture_ws.js` -> `audio::start_ingest_server`.
    WebSocket,
    /// Record the PulseAudio sink Chrome plays into, from outside the browser
    /// entirely (`crate::pulse`). The only transport that still hears anything
    /// as of 6 Aug 2026: Meet stopped exposing remote audio through media
    /// elements, and every in-browser alternative dies on Chrome's audio
    /// service having no capture device. Requires `pulse_source`.
    Pulse,
}

impl Config {
    pub fn load(path: &Path) -> anyhow::Result<Config> {
        let raw = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("failed to read config at {}: {e}", path.display()))?;
        let cfg: Config = toml::from_str(&raw)
            .map_err(|e| anyhow::anyhow!("failed to parse config at {}: {e}", path.display()))?;
        Ok(cfg)
    }

    /// Resolved key: `api_key` if set, else `VEXA_API_KEY` from `api_key_file`.
    pub fn resolved_api_key(&self) -> anyhow::Result<String> {
        if let Some(key) = self.inline_api_key() {
            return Ok(key);
        }

        let raw = std::fs::read_to_string(&self.api_key_file).map_err(|e| {
            anyhow::anyhow!(
                "failed to read api_key_file {}: {e}",
                self.api_key_file.display()
            )
        })?;

        extract_vexa_api_key(&raw).ok_or_else(|| {
            anyhow::anyhow!(
                "no non-empty VEXA_API_KEY= line in {}",
                self.api_key_file.display()
            )
        })
    }

    /// The non-empty inline `api_key` override, if configured.
    fn inline_api_key(&self) -> Option<String> {
        self.api_key
            .as_ref()
            .map(|k| k.trim().to_string())
            .filter(|k| !k.is_empty())
    }

    /// Boot-time key resolution. Same as [`Config::resolved_api_key`] when a key
    /// already exists; **mints and persists one** when `api_key_file` is absent
    /// or carries no `VEXA_API_KEY=` line.
    ///
    /// Without this the install is circular: `AppState::new` hard-fails at
    /// startup when `vexa_token.env` is missing, but the only thing that
    /// regenerates that file is `vexa_bots.py setup`, which calls
    /// `POST /admin/users/{uid}/tokens` — a route that just echoes back the key
    /// meetbot read out of the same missing file. So a lost token file meant
    /// meetbot could neither boot nor be re-bootstrapped. Minting on first boot
    /// breaks the cycle; an existing file is read, never rewritten, so a live
    /// install is untouched.
    pub fn ensure_api_key(&self) -> anyhow::Result<String> {
        if let Some(key) = self.inline_api_key() {
            return Ok(key);
        }

        match std::fs::read_to_string(&self.api_key_file) {
            Ok(raw) => {
                if let Some(key) = extract_vexa_api_key(&raw) {
                    return Ok(key);
                }
                // File exists but has no usable key line: keep whatever else it
                // holds and append ours.
                let key = generate_api_key();
                write_api_key_file(&self.api_key_file, &raw, &key)?;
                log_minted_key(&self.api_key_file, &key);
                Ok(key)
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let key = generate_api_key();
                write_api_key_file(&self.api_key_file, "", &key)?;
                log_minted_key(&self.api_key_file, &key);
                Ok(key)
            }
            // Anything else (EACCES, EISDIR) is a real misconfiguration; do NOT
            // paper over it by overwriting a file we simply could not read.
            Err(e) => Err(anyhow::anyhow!(
                "failed to read api_key_file {}: {e}",
                self.api_key_file.display()
            )),
        }
    }

    /// `http://{host}:{whisper_port}{whisper_path}`, host resolved live.
    pub fn whisper_endpoint(&self) -> String {
        let host = match self.whisper_host.as_ref() {
            Some(h) if !h.trim().is_empty() => h.trim().to_string(),
            _ => Config::gateway_ip(),
        };
        format!("http://{}:{}{}", host, self.whisper_port, self.whisper_path)
    }

    /// Default route gateway from `ip route show default`; `"172.25.32.1"` on failure.
    pub fn gateway_ip() -> String {
        let out = std::process::Command::new("ip")
            .args(["route", "show", "default"])
            .output();

        let stdout = match out {
            Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).into_owned(),
            _ => return GATEWAY_FALLBACK.to_string(),
        };

        for line in stdout.lines() {
            let mut fields = line.split_whitespace();
            while let Some(field) = fields.next() {
                if field == "via"
                    && let Some(ip) = fields.next()
                    && !ip.is_empty()
                {
                    return ip.to_string();
                }
            }
        }

        GATEWAY_FALLBACK.to_string()
    }

    pub fn admission_timeout(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.admission_timeout_min.saturating_mul(60))
    }

    /// The in-call ceiling configured in config.toml, if any.
    ///
    /// `None` means "leave the session's own default alone", which is what
    /// keeps the `MEETBOT_MAX_CALL_MIN` env override working when the key is
    /// absent. The 4h cap is active either way — this only overrides it.
    pub fn max_call_duration(&self) -> Option<std::time::Duration> {
        self.max_call_duration_min
            .map(|m| std::time::Duration::from_secs(m.saturating_mul(60)))
    }

    /// `data_dir/audio`
    pub fn audio_dir(&self) -> PathBuf {
        self.data_dir.join("audio")
    }
}

// ---------------------------------------------------------------------------
// API-key file handling
// ---------------------------------------------------------------------------

/// Pulls the first non-empty `VEXA_API_KEY=` value out of a `vexa_token.env`.
pub fn extract_vexa_api_key(raw: &str) -> Option<String> {
    for line in raw.lines() {
        let line = line.trim();
        // `export VEXA_API_KEY=...` is what a shell-sourced env file often holds.
        let line = line.strip_prefix("export ").unwrap_or(line);
        if let Some(rest) = line.strip_prefix("VEXA_API_KEY=") {
            let value = rest.trim().trim_matches('"').trim_matches('\'').trim();
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

/// A fresh 256-bit-class API key, hex encoded.
///
/// Built from two v4 UUIDs, i.e. `getrandom` under the hood (the same CSPRNG
/// source a dedicated `rand` dependency would use) — 244 bits of entropy, well
/// past brute-force range for a loopback-bound service.
pub fn generate_api_key() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

/// Writes `existing` plus a `VEXA_API_KEY=<key>` line to `path`, mode 0600.
///
/// `existing` is the current file body (empty when the file is absent); it is
/// preserved so an env file carrying other variables does not lose them.
fn write_api_key_file(path: &Path, existing: &str, key: &str) -> anyhow::Result<()> {
    use std::io::Write;

    if let Some(parent) = path.parent()
        && !parent.as_os_str().is_empty()
    {
        std::fs::create_dir_all(parent).map_err(|e| {
            anyhow::anyhow!(
                "failed to create api_key_file dir {}: {e}",
                parent.display()
            )
        })?;
    }

    let mut body = existing.to_string();
    if !body.is_empty() && !body.ends_with('\n') {
        body.push('\n');
    }
    body.push_str(&format!("VEXA_API_KEY={key}\n"));

    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }

    let mut f = opts
        .open(path)
        .map_err(|e| anyhow::anyhow!("failed to write api_key_file {}: {e}", path.display()))?;
    f.write_all(body.as_bytes())
        .map_err(|e| anyhow::anyhow!("failed to write api_key_file {}: {e}", path.display()))?;

    // `mode()` only applies at creation, so an existing file keeps whatever
    // permissions it had. Clamp it either way: this file is a bearer token.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| anyhow::anyhow!("failed to chmod 0600 {}: {e}", path.display()))?;
    }

    Ok(())
}

/// Logged exactly once, at boot, when a key had to be minted. The key is in the
/// log on purpose: it is the only chance to copy it into any client that is not
/// reading `api_key_file` directly.
fn log_minted_key(path: &Path, key: &str) {
    tracing::warn!(
        api_key_file = %path.display(),
        api_key = %key,
        "no VEXA_API_KEY found; minted a new one and wrote it (0600). \
         Clients must present this as X-API-Key."
    );
}

// ---------------------------------------------------------------------------
// Session phase / commands / handle
// ---------------------------------------------------------------------------

/// Coarse lifecycle of a live session; mirrors SPEC.md §2. Wire form is snake_case.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionPhase {
    Joining,
    WaitingRoom,
    InCall,
    Finalizing,
    Completed,
    Failed,
    Stopped,
}

impl SessionPhase {
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            SessionPhase::Completed | SessionPhase::Failed | SessionPhase::Stopped
        )
    }

    /// Completed/Failed/Stopped -> the matching `MeetingStatus`; others -> `None`.
    pub fn terminal_status(&self) -> Option<crate::db::MeetingStatus> {
        match self {
            SessionPhase::Completed => Some(crate::db::MeetingStatus::Completed),
            SessionPhase::Failed => Some(crate::db::MeetingStatus::Failed),
            SessionPhase::Stopped => Some(crate::db::MeetingStatus::Stopped),
            _ => None,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            SessionPhase::Joining => "joining",
            SessionPhase::WaitingRoom => "waiting_room",
            SessionPhase::InCall => "in_call",
            SessionPhase::Finalizing => "finalizing",
            SessionPhase::Completed => "completed",
            SessionPhase::Failed => "failed",
            SessionPhase::Stopped => "stopped",
        }
    }
}

impl std::fmt::Display for SessionPhase {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug)]
pub enum SessionCommand {
    /// Leave the call and finalize.
    Stop,
    /// Ask the running session for its current phase.
    Query(oneshot::Sender<SessionPhase>),
}

#[derive(Debug, Clone)]
pub struct SessionHandle {
    pub meeting_id: Uuid,
    pub key: MeetingKey,
    pub title: Option<String>,
    pub started_at: DateTime<Utc>,
    pub phase: Arc<RwLock<SessionPhase>>,
    pub cmd_tx: mpsc::Sender<SessionCommand>,
}

impl SessionHandle {
    pub async fn phase(&self) -> SessionPhase {
        *self.phase.read().await
    }

    /// Best-effort; `Err` only if the session task is already gone.
    pub async fn stop(&self) -> anyhow::Result<()> {
        self.cmd_tx
            .send(SessionCommand::Stop)
            .await
            .map_err(|_| anyhow::anyhow!("session {} is no longer running", self.key))
    }
}

// ---------------------------------------------------------------------------
// AppState
// ---------------------------------------------------------------------------

pub struct AppState {
    pub cfg: Config,
    pub api_key: String,
    pub db: Db,
    pub http: reqwest::Client,
    pub whisper: Arc<crate::whisper::WhisperClient>,
    /// Live sessions only; entries are removed when the session task ends.
    pub sessions: RwLock<HashMap<MeetingKey, SessionHandle>>,
    /// Permits == `cfg.max_concurrent_bots`.
    pub slots: Arc<Semaphore>,
    pub started_at: DateTime<Utc>,
}

pub type SharedState = Arc<AppState>;

impl AppState {
    /// Opens the DB, runs migrations, sweeps stale rows, builds the HTTP client.
    pub fn new(cfg: Config) -> anyhow::Result<SharedState> {
        // Mints + persists a key when `api_key_file` is missing, so a lost
        // vexa_token.env does not brick the install (see `ensure_api_key`).
        let api_key = cfg.ensure_api_key()?;

        std::fs::create_dir_all(&cfg.data_dir).map_err(|e| {
            anyhow::anyhow!("failed to create data_dir {}: {e}", cfg.data_dir.display())
        })?;
        let audio_dir = cfg.audio_dir();
        std::fs::create_dir_all(&audio_dir).map_err(|e| {
            anyhow::anyhow!("failed to create audio dir {}: {e}", audio_dir.display())
        })?;

        // `Db::open` already runs `migrate` (SPEC.md §5); no second call.
        let db = Db::open(&cfg.db_path)?;

        // Zero cut-off on purpose: sweep EVERY non-terminal row, not just those
        // older than `stale_after_hours`.
        //
        // SPEC.md §2 contradicts itself here — "marks every non-terminal row
        // older than stale_after" against "Never leave a row non-terminal across
        // a restart". Acceptance test 9 settles it: killing the server with a
        // live session must leave that row `failed` on the next read, and a
        // session that had been running ten minutes is not six hours old, so an
        // age-gated sweep would leave it non-terminal and `vexa_bots.py` would
        // poll it forever.
        //
        // This is safe because sessions live only in this process's memory: once
        // we are back at startup, no non-terminal row can still have an owner.
        // SPEC.md §2 has been corrected to match; see also §4 on the now
        // vestigial `stale_after_hours`.
        let swept = db.sweep_stale(
            chrono::Duration::zero(),
            "server restarted while session was live",
        )?;
        if swept > 0 {
            tracing::warn!(rows = swept, "swept stale non-terminal meetings to failed");
        }

        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()
            .map_err(|e| anyhow::anyhow!("failed to build http client: {e}"))?;

        let whisper = Arc::new(crate::whisper::WhisperClient::new(
            http.clone(),
            cfg.whisper_endpoint(),
        ));

        let slots = Arc::new(Semaphore::new(cfg.max_concurrent_bots));

        Ok(Arc::new(AppState {
            cfg,
            api_key,
            db,
            http,
            whisper,
            sessions: RwLock::new(HashMap::new()),
            slots,
            started_at: Utc::now(),
        }))
    }

    pub async fn register(&self, handle: SessionHandle) {
        let key = handle.key.clone();
        self.sessions.write().await.insert(key, handle);
    }

    pub async fn unregister(&self, key: &MeetingKey) {
        self.sessions.write().await.remove(key);
    }

    pub async fn get_session(&self, key: &MeetingKey) -> Option<SessionHandle> {
        self.sessions.read().await.get(key).cloned()
    }

    pub async fn active_count(&self) -> usize {
        self.sessions.read().await.len()
    }

    pub async fn list_sessions(&self) -> Vec<SessionHandle> {
        self.sessions.read().await.values().cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn platform_roundtrip() {
        assert_eq!(Platform::parse("google_meet"), Some(Platform::GoogleMeet));
        assert_eq!(Platform::parse("teams"), Some(Platform::Teams));
        assert_eq!(Platform::parse("zoom"), None);
        assert_eq!(Platform::GoogleMeet.as_str(), "google_meet");
    }

    #[test]
    fn meet_native_id_validation() {
        let p = Platform::GoogleMeet;
        assert!(p.validate_native_id("bqy-ybgi-pbb"));
        assert!(!p.validate_native_id("never-sent-xyz"));
        assert!(!p.validate_native_id("BQY-YBGI-PBB"));
        assert!(!p.validate_native_id("bqy-ybgi"));
        assert!(!p.validate_native_id("bq1-ybgi-pbb"));
    }

    #[test]
    fn teams_native_id_validation() {
        let p = Platform::Teams;
        assert!(p.validate_native_id("1234567890"));
        assert!(p.validate_native_id("12345678901234567890"));
        assert!(!p.validate_native_id("123456789"));
        assert!(!p.validate_native_id("123456789012345678901"));
        assert!(!p.validate_native_id("12345678a0"));
    }

    #[test]
    fn key_paths_and_urls() {
        let k = MeetingKey::new(Platform::GoogleMeet, "bqy-ybgi-pbb");
        assert_eq!(k.as_path(), "google_meet/bqy-ybgi-pbb");
        assert_eq!(k.url(), "https://meet.google.com/bqy-ybgi-pbb");
        assert_eq!(k.to_string(), "google_meet/bqy-ybgi-pbb");
    }

    fn scratch_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("meetbot-test-{tag}-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&dir).expect("scratch dir");
        dir
    }

    fn cfg_with_key_file(path: PathBuf) -> Config {
        let toml = format!(
            r#"
http_port = 8060
http_bind = "127.0.0.1"
api_key_file = "{}"
admin_token = "admin"
whisper_port = 8083
whisper_path = "/v1/audio/transcriptions"
max_concurrent_bots = 4
admission_timeout_min = 10
lonely_grace_sec = 60
headless = true
chromium_path = "/nonexistent/chrome"
bot_name = "Notetaker"
data_dir = "/tmp/meetbot-test-data"
db_path = "/tmp/meetbot-test-data/meetbot.db"
stale_after_hours = 6
"#,
            path.display()
        );
        toml::from_str(&toml).expect("test config parses")
    }

    // SPEC.md §7.2. `max_call_duration_min` was documented in config.toml long
    // before `Config` carried the field, so editing the key was inert and
    // silently did nothing. These pin the config surface itself.
    #[test]
    fn max_call_duration_min_is_absent_by_default() {
        let cfg = cfg_with_key_file(PathBuf::from("/tmp/meetbot-test-token.env"));
        assert_eq!(cfg.max_call_duration_min, None);
        // None, not Some(4h): the session keeps its own default, which is what
        // lets the MEETBOT_MAX_CALL_MIN env override still apply.
        assert_eq!(cfg.max_call_duration(), None);
    }

    #[test]
    fn max_call_duration_min_parses_and_converts_to_minutes() {
        let base = cfg_with_key_file(PathBuf::from("/tmp/meetbot-test-token.env"));
        let raw = format!(
            "http_port = {}\nhttp_bind = \"{}\"\napi_key_file = \"{}\"\nadmin_token = \"{}\"\n\
             whisper_port = {}\nwhisper_path = \"{}\"\nmax_concurrent_bots = {}\n\
             admission_timeout_min = {}\nlonely_grace_sec = {}\nheadless = {}\n\
             chromium_path = \"{}\"\nbot_name = \"{}\"\ndata_dir = \"{}\"\ndb_path = \"{}\"\n\
             stale_after_hours = {}\nmax_call_duration_min = 90\n",
            base.http_port,
            base.http_bind,
            base.api_key_file.display(),
            base.admin_token,
            base.whisper_port,
            base.whisper_path,
            base.max_concurrent_bots,
            base.admission_timeout_min,
            base.lonely_grace_sec,
            base.headless,
            base.chromium_path.display(),
            base.bot_name,
            base.data_dir.display(),
            base.db_path.display(),
            base.stale_after_hours,
        );
        let cfg: Config = toml::from_str(&raw).expect("config with max_call_duration_min parses");
        assert_eq!(cfg.max_call_duration_min, Some(90));
        assert_eq!(
            cfg.max_call_duration(),
            Some(std::time::Duration::from_secs(90 * 60))
        );
    }

    #[test]
    fn extracts_key_from_env_file_forms() {
        assert_eq!(
            extract_vexa_api_key("VEXA_API_KEY=abc123\n").as_deref(),
            Some("abc123")
        );
        assert_eq!(
            extract_vexa_api_key("# comment\nexport VEXA_API_KEY=\"quoted\"\n").as_deref(),
            Some("quoted")
        );
        assert_eq!(extract_vexa_api_key("VEXA_API_KEY=\n"), None);
        assert_eq!(extract_vexa_api_key("OTHER=1\n"), None);
    }

    // Regression: bootstrap used to be circular — a missing vexa_token.env made
    // AppState::new hard-fail, and the only regeneration path (POST
    // /admin/users/{uid}/tokens) just echoed the key read from that same file.
    #[test]
    fn ensure_api_key_mints_and_persists_when_file_is_absent() {
        let dir = scratch_dir("mint");
        let file = dir.join("nested").join("vexa_token.env");
        let cfg = cfg_with_key_file(file.clone());

        assert!(cfg.resolved_api_key().is_err(), "precondition: no key yet");

        let key = cfg.ensure_api_key().expect("mints a key");
        assert!(key.len() >= 32, "key must be long: {key}");
        assert!(key.chars().all(|c| c.is_ascii_hexdigit()));

        // Persisted, and stable across a restart.
        assert_eq!(cfg.resolved_api_key().unwrap(), key);
        assert_eq!(cfg.ensure_api_key().unwrap(), key);

        // Two mints must not collide.
        let other = cfg_with_key_file(scratch_dir("mint2").join("vexa_token.env"));
        assert_ne!(other.ensure_api_key().unwrap(), key);

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&file).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "token file must not be world-readable");
        }

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn ensure_api_key_never_rewrites_an_existing_key() {
        let dir = scratch_dir("existing");
        let file = dir.join("vexa_token.env");
        std::fs::write(&file, "OTHER=1\nVEXA_API_KEY=already-here\n").unwrap();
        let cfg = cfg_with_key_file(file.clone());

        assert_eq!(cfg.ensure_api_key().unwrap(), "already-here");
        assert_eq!(
            std::fs::read_to_string(&file).unwrap(),
            "OTHER=1\nVEXA_API_KEY=already-here\n",
            "a live install's token file must be byte-identical after boot"
        );

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn ensure_api_key_appends_without_dropping_other_vars() {
        let dir = scratch_dir("append");
        let file = dir.join("vexa_token.env");
        std::fs::write(&file, "VEXA_URL=http://127.0.0.1:8060").unwrap();
        let cfg = cfg_with_key_file(file.clone());

        let key = cfg.ensure_api_key().unwrap();
        let body = std::fs::read_to_string(&file).unwrap();
        assert!(body.contains("VEXA_URL=http://127.0.0.1:8060"));
        assert!(body.contains(&format!("VEXA_API_KEY={key}")));

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn inline_api_key_wins_and_never_touches_disk() {
        let dir = scratch_dir("inline");
        let file = dir.join("vexa_token.env");
        let mut cfg = cfg_with_key_file(file.clone());
        cfg.api_key = Some("  inline-key  ".to_string());

        assert_eq!(cfg.ensure_api_key().unwrap(), "inline-key");
        assert!(!file.exists(), "inline override must not mint a file");

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn require_whisper_for_bots_defaults_to_true() {
        let cfg = cfg_with_key_file(PathBuf::from("/tmp/does-not-matter"));
        assert!(
            cfg.require_whisper_for_bots,
            "SPEC §1.3.2: fail-fast 503 is the default"
        );
    }

    #[test]
    fn phase_terminality() {
        assert!(SessionPhase::Completed.is_terminal());
        assert!(SessionPhase::Failed.is_terminal());
        assert!(SessionPhase::Stopped.is_terminal());
        assert!(!SessionPhase::InCall.is_terminal());
        assert!(SessionPhase::WaitingRoom.terminal_status().is_none());
    }
}
