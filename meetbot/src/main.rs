//! meetbot server entrypoint.
//!
//! Subcommands:
//!
//! ```text
//! meetbot [serve] [--config <path>]     start the HTTP API (default)
//! meetbot doctor <meeting-id> [--config <path>] [--wait <sec>] [--headed]
//!                              [--platform google_meet|teams] [--passcode <p>]
//! meetbot --help
//! ```
//!
//! `serve` loads `config.toml`, opens the DB (which sweeps stale rows from a
//! previous crash), serves the axum router, and on SIGTERM/Ctrl-C stops the
//! listener and then *finalizes every in-flight session* before exiting — a
//! meeting row must never survive a restart in a non-terminal status, because
//! `vexa_bots.py` would poll it forever.
//!
//! `doctor` is the maintenance tool for the day Google or Microsoft reshuffles
//! their DOM: it dry-runs the join dance against a real meeting id and prints
//! which stage (and therefore which selector, named in the error chain that
//! `meet.rs` produces) broke. It never touches the database and never sends a
//! bot into a meeting for real — it leaves as soon as the gate has been
//! evaluated.
//!
//! It drives **both** platforms. The platform is inferred from the id shape
//! (`xxx-xxxx-xxx` = Google Meet, 10-20 digits = Teams) and can be forced with
//! `--platform`. For Teams it additionally prints the verification status of
//! every selector group, because most of that table has never been observed
//! against a real meeting — a group that misses because it was always a guess
//! needs a different fix from one that used to match and stopped.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use meetbot::meet::{Admission, BrowserOptions, MeetSession};
use meetbot::state::{AppState, Config, MeetingKey, Platform, SharedState};

/// How long shutdown waits for live sessions to finalize before it force-fails
/// whatever is left. Long enough for a whisper backlog to drain, short enough
/// that systemd does not SIGKILL us first (raise TimeoutStopSec past this).
const SHUTDOWN_GRACE: Duration = Duration::from_secs(90);
/// Default waiting-room patience for `doctor` — it is a dry run, not a bot.
const DOCTOR_DEFAULT_WAIT_SEC: u64 = 20;

fn main() -> Result<()> {
    let cmd = match Cli::parse(std::env::args().skip(1).collect()) {
        Ok(cmd) => cmd,
        Err(e) => {
            eprintln!("meetbot: {e}");
            eprintln!();
            eprintln!("{USAGE}");
            std::process::exit(2);
        }
    };

    match cmd {
        Cli::Help => {
            println!("{USAGE}");
            Ok(())
        }
        Cli::Version => {
            println!("meetbot {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Cli::Serve { config } => {
            init_tracing("meetbot=info,tower_http=warn");
            runtime()?.block_on(serve(&config))
        }
        Cli::Doctor {
            config,
            code,
            wait_sec,
            headed,
            platform,
            passcode,
        } => {
            init_tracing("meetbot=warn");
            let ok = runtime()?.block_on(doctor(
                &config,
                &code,
                wait_sec,
                headed,
                platform,
                passcode.as_deref(),
            ))?;
            if !ok {
                std::process::exit(1);
            }
            Ok(())
        }
    }
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

const USAGE: &str = "\
meetbot — Vexa-compatible meeting bot

USAGE:
    meetbot [serve] [--config <path>]
    meetbot doctor <meeting-id> [--config <path>] [--wait <sec>] [--headed]
                                [--platform google_meet|teams] [--passcode <p>]
    meetbot --help | --version

COMMANDS:
    serve     Run the HTTP API (default when no command is given).
    doctor    Dry-run the join dance against <meeting-id> and report exactly
              which stage/selector failed. Reads config, never writes to the
              database. Works for both platforms:
                Google Meet  xxx-xxxx-xxx
                Teams        10-20 digits (add --passcode when the invite has one)

OPTIONS:
    --config <path>     Config file. Default: ./config.toml (or $MEETBOT_CONFIG).
    --wait <sec>        doctor only: waiting-room patience. Default: 20.
    --headed            doctor only: force a visible browser, overriding config.
    --platform <name>   doctor only: google_meet | teams. Default: inferred from
                        the shape of <meeting-id>.
    --passcode <code>   doctor only: Teams meeting passcode. Delivered as ?p= on
                        the initial navigation, which is how the real join path
                        does it.";

#[derive(Debug)]
enum Cli {
    Serve {
        config: PathBuf,
    },
    Doctor {
        config: PathBuf,
        code: String,
        wait_sec: u64,
        headed: bool,
        /// `None` => infer from the shape of `code`.
        platform: Option<Platform>,
        passcode: Option<String>,
    },
    Help,
    Version,
}

impl Cli {
    fn parse(args: Vec<String>) -> Result<Cli> {
        let default_config = || {
            std::env::var("MEETBOT_CONFIG")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("config.toml"))
        };

        let mut rest = args.as_slice();
        let mut command = "serve";

        match rest.first().map(String::as_str) {
            None => {}
            Some("-h") | Some("--help") | Some("help") => return Ok(Cli::Help),
            Some("-V") | Some("--version") | Some("version") => return Ok(Cli::Version),
            Some("serve") => {
                rest = &rest[1..];
            }
            Some("doctor") => {
                command = "doctor";
                rest = &rest[1..];
            }
            // Back-compat with the original skeleton: `meetbot <config.toml>`.
            Some(path) if !path.starts_with('-') && path.ends_with(".toml") => {
                return Ok(Cli::Serve {
                    config: PathBuf::from(path),
                });
            }
            Some(other) => anyhow::bail!("unknown command '{other}'"),
        }

        let mut config: Option<PathBuf> = None;
        let mut positional: Option<String> = None;
        let mut wait_sec = DOCTOR_DEFAULT_WAIT_SEC;
        let mut headed = false;
        let mut platform: Option<Platform> = None;
        let mut passcode: Option<String> = None;

        let mut i = 0;
        while i < rest.len() {
            let arg = rest[i].as_str();
            match arg {
                "--config" | "-c" => {
                    let v = rest
                        .get(i + 1)
                        .context("--config needs a path")?
                        .clone();
                    config = Some(PathBuf::from(v));
                    i += 2;
                }
                "--wait" | "-w" => {
                    let v = rest.get(i + 1).context("--wait needs a number")?;
                    wait_sec = v
                        .parse()
                        .with_context(|| format!("--wait: '{v}' is not a number of seconds"))?;
                    i += 2;
                }
                "--headed" => {
                    headed = true;
                    i += 1;
                }
                "--platform" | "-p" => {
                    let v = rest
                        .get(i + 1)
                        .context("--platform needs google_meet or teams")?;
                    platform = Some(Platform::parse(v.trim()).with_context(|| {
                        format!("--platform: '{v}' is not google_meet or teams")
                    })?);
                    i += 2;
                }
                "--passcode" => {
                    let v = rest.get(i + 1).context("--passcode needs a value")?;
                    passcode = Some(v.clone());
                    i += 2;
                }
                other if other.starts_with('-') => anyhow::bail!("unknown option '{other}'"),
                other => {
                    if positional.is_some() {
                        anyhow::bail!("unexpected extra argument '{other}'");
                    }
                    positional = Some(other.to_string());
                    i += 1;
                }
            }
        }

        let config = config.unwrap_or_else(default_config);

        if command == "doctor" {
            let code = positional.context(
                "doctor needs a meeting id: bqy-ybgi-pbb (Google Meet) or 1234567890123 (Teams)",
            )?;
            Ok(Cli::Doctor {
                config,
                code,
                wait_sec,
                headed,
                platform,
                passcode,
            })
        } else {
            if let Some(extra) = positional {
                anyhow::bail!("unexpected argument '{extra}'");
            }
            Ok(Cli::Serve { config })
        }
    }
}

fn runtime() -> Result<tokio::runtime::Runtime> {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .context("failed to build the tokio runtime")
}

fn init_tracing(default_filter: &str) {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(default_filter));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .init();
}

// ---------------------------------------------------------------------------
// serve
// ---------------------------------------------------------------------------

async fn serve(config_path: &Path) -> Result<()> {
    let cfg = Config::load(config_path)
        .with_context(|| format!("loading config from {}", config_path.display()))?;

    let bind = format!("{}:{}", cfg.http_bind, cfg.http_port);
    let whisper_endpoint = cfg.whisper_endpoint();
    let chromium = cfg.chromium_path.clone();
    let max_bots = cfg.max_concurrent_bots;

    // AppState::new opens the DB, migrates, and sweeps rows left non-terminal
    // by a previous crash (SPEC.md §2 "Crash recovery").
    let state = AppState::new(cfg).context("initializing application state")?;

    // The DB half of crash recovery has a filesystem twin: `/tmp/meetbot-chrome-*`
    // profiles orphaned by a previous crash are tens of MB each and nothing else
    // removes them. `MeetSession::launch` sweeps once per process, but a server
    // that boots and never launches a session would never sweep at all, so the
    // leftovers would accumulate across restarts. Safe to run alongside that
    // `Once`: the sweep is mtime-gated, so it never removes a profile a live
    // session is still writing to, and a second pass simply finds nothing.
    let swept_profiles = meetbot::meet::sweep_stale_profiles();
    if swept_profiles > 0 {
        tracing::info!(
            removed = swept_profiles,
            "swept orphaned chrome profiles from a previous run"
        );
    }

    let listener = tokio::net::TcpListener::bind(&bind)
        .await
        .with_context(|| format!("binding {bind}"))?;

    tracing::info!(
        %bind,
        whisper = %whisper_endpoint,
        chromium = %chromium.display(),
        max_concurrent_bots = max_bots,
        "meetbot listening"
    );

    let router = meetbot::api::router(state.clone());
    let serve_result = axum::serve(listener, router)
        .with_graceful_shutdown(shutdown_signal())
        .await;

    // The listener is down; drain whatever is still in a call.
    finalize_in_flight(&state).await;

    serve_result.context("http server error")?;
    tracing::info!("meetbot stopped cleanly");
    Ok(())
}

/// Resolves on SIGTERM (systemd) or Ctrl-C (interactive).
async fn shutdown_signal() {
    let ctrl_c = async {
        if tokio::signal::ctrl_c().await.is_err() {
            std::future::pending::<()>().await;
        }
    };

    #[cfg(unix)]
    let terminate = async {
        use tokio::signal::unix::{SignalKind, signal};
        match signal(SignalKind::terminate()) {
            Ok(mut sig) => {
                sig.recv().await;
            }
            Err(_) => std::future::pending::<()>().await,
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => tracing::info!("SIGINT received; shutting down"),
        _ = terminate => tracing::info!("SIGTERM received; shutting down"),
    }
}

/// Asks every live session to stop, waits for them to write their terminal
/// status, and force-fails anything still non-terminal at the deadline.
async fn finalize_in_flight(state: &SharedState) {
    let sessions = state.list_sessions().await;
    if sessions.is_empty() {
        // A row can still be non-terminal if a session task died without its
        // guard running (e.g. the process was killed mid-write last time).
        force_fail_leftovers(state);
        return;
    }

    tracing::info!(count = sessions.len(), "finalizing in-flight sessions");
    for handle in &sessions {
        if let Err(e) = handle.stop().await {
            tracing::warn!(key = %handle.key, error = %e, "stop command not delivered");
        }
    }

    let deadline = Instant::now() + SHUTDOWN_GRACE;
    loop {
        let remaining = state.active_count().await;
        if remaining == 0 {
            tracing::info!("all sessions finalized");
            break;
        }
        if Instant::now() >= deadline {
            tracing::warn!(remaining, "shutdown grace expired; force-failing the rest");
            break;
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }

    force_fail_leftovers(state);
}

/// Belt and braces for SPEC.md §2: nothing may be left non-terminal on exit.
/// `sweep_stale` with a zero cut-off touches every non-terminal row.
fn force_fail_leftovers(state: &SharedState) {
    match state.db.sweep_stale(
        chrono::Duration::zero(),
        "server shut down while session was live",
    ) {
        Ok(0) => {}
        Ok(n) => tracing::warn!(rows = n, "force-failed non-terminal rows at shutdown"),
        Err(e) => tracing::error!(error = %e, "shutdown sweep failed"),
    }
}

// ---------------------------------------------------------------------------
// doctor
// ---------------------------------------------------------------------------

/// Infers the platform from the shape of a meeting id.
///
/// Google Meet ids are `xxx-xxxx-xxx`; Teams ids are 10-20 digits (that is
/// exactly what `vexa_bots.py` scrapes off the calendar). Anything else is
/// ambiguous and the caller must pass `--platform`.
fn infer_platform(code: &str) -> Option<Platform> {
    [Platform::GoogleMeet, Platform::Teams]
        .into_iter()
        .find(|p| p.validate_native_id(code))
}

/// Dry-runs the join dance and reports the first stage that broke. Returns
/// `Ok(true)` when every stage passed.
///
/// Drives whichever platform the id belongs to. The stages differ (Teams has a
/// five-hop redirect chain and a base64 `coords` check that Meet does not) but
/// everything after the join click — admission, the SPEC §0.1 zero-segment
/// terminal state, teardown — is the same shared code both in production and
/// here, which is the point of running this rather than a bespoke script.
async fn doctor(
    config_path: &Path,
    code: &str,
    wait_sec: u64,
    headed: bool,
    platform: Option<Platform>,
    passcode: Option<&str>,
) -> Result<bool> {
    let cfg = match Config::load(config_path) {
        Ok(cfg) => cfg,
        Err(e) => {
            println!("meetbot doctor");
            println!();
            step_fail("config", &format!("cannot load {}", config_path.display()));
            print_chain(&e);
            return Ok(false);
        }
    };

    // --- 1. platform + meeting id -------------------------------------------
    let platform = match platform.or_else(|| infer_platform(code)) {
        Some(p) => p,
        None => {
            println!("meetbot doctor");
            println!();
            step_fail(
                "meeting id",
                &format!(
                    "'{code}' is neither a Google Meet id (xxx-xxxx-xxx) nor a Teams id \
                     (10-20 digits); pass --platform to force one"
                ),
            );
            return Ok(false);
        }
    };

    println!("meetbot doctor — dry-running the {platform} join dance");
    println!();
    step_ok("config", &format!("loaded {}", config_path.display()));

    if !platform.validate_native_id(code) {
        step_fail(
            "meeting id",
            &format!("'{code}' is not a valid {platform} meeting id"),
        );
        return Ok(false);
    }
    let key = MeetingKey::new(platform, code);
    step_ok("meeting id", &key.url());

    if platform == Platform::Teams {
        // The passcode is not typed into a field: it rides as `?p=` on the
        // initial navigation and Teams carries it through all five hops into
        // `coords.passcode`. Say so, because "I passed a passcode and saw no
        // passcode field" is otherwise an alarming non-event.
        match passcode {
            Some(_) => step_ok("passcode", "will be delivered as ?p= on the initial navigation"),
            None => step_ok(
                "passcode",
                "none supplied (a meeting that needs one will land on the retry screen)",
            ),
        }
    } else if passcode.is_some() {
        step_warn("passcode", "Google Meet has no passcode; ignoring --passcode");
    }

    // --- 2. browser binary ---------------------------------------------------
    match cfg.cdp_port {
        Some(port) => step_ok("browser", &format!("attaching to CDP on 127.0.0.1:{port}")),
        None => {
            if cfg.chromium_path.exists() {
                step_ok("browser", &cfg.chromium_path.display().to_string());
            } else {
                step_fail(
                    "browser",
                    &format!("chromium not found at {}", cfg.chromium_path.display()),
                );
                return Ok(false);
            }
        }
    }

    // --- 3. whisper (informational; a dead whisper never blocks a join) ------
    let endpoint = cfg.whisper_endpoint();
    let whisper = meetbot::whisper::WhisperClient::new(reqwest::Client::new(), endpoint.clone());
    if whisper.health().await {
        step_ok("whisper", &endpoint);
    } else {
        step_warn("whisper", &format!("unreachable at {endpoint} (join unaffected)"));
    }

    // --- 4. launch -----------------------------------------------------------
    // doctor must prepare the capture sink the same way a real session does, or
    // it certifies a path production never takes.
    if let Some(sink) = cfg.pulse_sink.as_deref() {
        match meetbot::pulse::ensure_null_sink(sink) {
            Ok(true) => step_ok("pulse sink", &format!("created {sink}")),
            Ok(false) => step_ok("pulse sink", &format!("{sink} already present")),
            Err(e) => step_warn("pulse sink", &format!("{e:#}")),
        }
    }

    let opts = BrowserOptions {
        chromium_path: cfg.chromium_path.clone(),
        headless: if headed { false } else { cfg.headless },
        attach_cdp_port: cfg.cdp_port,
        user_data_dir: None,
        // doctor must join exactly the way a real session does, or it certifies
        // a code path production never takes.
        profile_template: cfg.profile_template.clone(),
        window_size: (1280, 720),
        pulse_sink: cfg.pulse_sink.clone(),
    };

    let session = match MeetSession::launch(&opts).await {
        Ok(s) => {
            step_ok("launch", "chromium up, CDP attached");
            s
        }
        Err(e) => {
            step_fail("launch", "could not start or attach to chromium");
            print_chain(&e);
            return Ok(false);
        }
    };

    // --- 5. the join dance (this is the selector-sensitive part) -------------
    let join_result = session.join(&key, &cfg.bot_name, passcode).await;

    if let Err(e) = join_result {
        step_fail("join", "the join dance broke — see the chain below");
        print_chain(&e);
        println!();
        print_join_stages(platform, &key, &cfg.bot_name);
        session.close().await;
        return Ok(false);
    }
    step_ok("join", "name filled and join clicked");

    // --- 6. the waiting-room gate -------------------------------------------
    let wait = Duration::from_secs(wait_sec);
    let verdict = match session.wait_for_admission(wait).await {
        Ok(Admission::Admitted) => {
            step_ok("admission", "in-call DOM detected (bot was let in)");
            true
        }
        Ok(Admission::TimedOut) => {
            step_ok(
                "admission",
                &format!("still in the waiting room after {wait_sec}s (expected for a dry run)"),
            );
            true
        }
        Ok(Admission::Denied) => {
            let detail = match platform {
                Platform::Teams =>
                    "rejected — the retry screen was detected. Teams renders an IDENTICAL \
                     screen for a bad meeting id, a wrong passcode and an ended meeting, so \
                     this does NOT tell you which; all three finish `completed` with zero \
                     segments per SPEC §0.1",
                Platform::GoogleMeet => "host denied entry — the denial DOM was detected",
            };
            step_ok("admission", detail);
            true
        }
        Err(e) => {
            step_fail("admission", "the waiting-room watch broke");
            print_chain(&e);
            println!();
            print_admission_help(platform);
            false
        }
    };

    // Speaker attribution, the 6 Aug 2026 "every line says Unknown" bug.
    // Must run BEFORE leave(): once the bot is out of the call the participant
    // tiles are gone and the dump would report zeros that mean nothing.
    match session.dump_speaker_markup().await {
        Ok(v) => {
            println!("\n--- speaker attribution (live in-call DOM) ---");
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
        }
        Err(e) => step_warn("speaker markup", &format!("{e:#}")),
    }

    session.leave().await;
    session.close().await;

    println!();
    if verdict {
        println!("RESULT: join dance intact — every selector matched.");
    } else {
        println!("RESULT: broken — fix the failing selector above.");
    }
    Ok(verdict)
}

/// Prints the stage-by-stage map of the join dance for the failing platform,
/// so the operator knows where in the sequence to look.
///
/// For Teams every stage also carries the hint from
/// [`meetbot::teams::JoinStage`], the same text the runtime error uses, so the
/// doctor output and the production log agree word for word.
fn print_join_stages(platform: Platform, key: &MeetingKey, bot_name: &str) {
    match platform {
        Platform::GoogleMeet => {
            println!("  The failing selector is named in the error above. The join dance is:");
            println!("    1. navigate to {}", key.url());
            println!("    2. dismiss the mic/camera permission prompt");
            println!("    3. fill the name field with '{bot_name}'");
            println!("    4. click 'Ask to join' / 'Join now'");
            println!("  Fix the selector for the failing step in src/meet.rs, then re-run doctor.");
        }
        Platform::Teams => {
            use meetbot::teams::JoinStage;
            println!("  The failing stage and selector group are named in the error above.");
            println!("  The Teams join dance is:");
            for (n, stage) in [
                JoinStage::Redirect,
                JoinStage::PreJoin,
                JoinStage::CodeCheck,
                JoinStage::JoinClick,
                JoinStage::Admission,
            ]
            .iter()
            .enumerate()
            {
                println!("    {}. {:<11} {}", n + 1, stage.as_str(), stage.hint());
            }
            println!();
            print_teams_selector_status();
            println!("  Fix the selector group in src/teams.rs, then re-run doctor.");
        }
    }
}

fn print_admission_help(platform: Platform) {
    match platform {
        Platform::GoogleMeet => {
            println!("  This stage polls for the in-call DOM (leave button / participant tray)");
            println!("  and the denial banner. Both selectors live in src/meet.rs.");
        }
        Platform::Teams => {
            println!("  This stage polls for the retry screen (VERIFIED terminal rejection),");
            println!("  the lobby and the in-call DOM. All of them live in src/teams.rs.");
            println!();
            print_teams_selector_status();
        }
    }
}

/// Prints which Teams selector groups have ever been observed against a live
/// meeting and which are still guesses.
///
/// This is the single most useful thing doctor can say about Teams right now:
/// the pre-join and rejection surfaces are verified, but the lobby, the in-call
/// DOM and the denial/ended copy were never seen — there was no real meeting to
/// join during recon. A maintainer staring at a failure needs to know which kind
/// of failure they have before they start editing selectors.
fn print_teams_selector_status() {
    println!("  Teams selector groups and how much we actually know about them:");
    for (name, candidates, verified) in meetbot::teams::DIAG_GROUPS {
        println!("    {name:<22} {:<32} {}", candidates[0], verified.label());
    }
    println!(
        "  INFERRED groups were never observed live (no real Teams meeting was available\n  \
         during recon). Close them by pointing spike/teams_flow.mjs at a meeting you host."
    );
}

fn step_ok(stage: &str, detail: &str) {
    println!("  [ ok ] {stage:<10} {detail}");
}

fn step_warn(stage: &str, detail: &str) {
    println!("  [warn] {stage:<10} {detail}");
}

fn step_fail(stage: &str, detail: &str) {
    println!("  [FAIL] {stage:<10} {detail}");
}

fn print_chain(err: &anyhow::Error) {
    for (i, cause) in err.chain().enumerate() {
        if i == 0 {
            println!("         error: {cause}");
        } else {
            println!("         caused by: {cause}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(args: &[&str]) -> Cli {
        Cli::parse(args.iter().map(|s| s.to_string()).collect()).expect("should parse")
    }

    /// The id shape is unambiguous between the two platforms, so `doctor` can
    /// route without being told: `xxx-xxxx-xxx` is Meet, 10-20 digits is Teams.
    #[test]
    fn platform_is_inferred_from_the_meeting_id_shape() {
        assert_eq!(infer_platform("bqy-ybgi-pbb"), Some(Platform::GoogleMeet));
        assert_eq!(infer_platform("1234567890123"), Some(Platform::Teams));
        assert_eq!(infer_platform("12345678901"), Some(Platform::Teams));
        // Too short for Teams, wrong shape for Meet: ambiguous, needs --platform.
        assert_eq!(infer_platform("99"), None);
        assert_eq!(infer_platform("not-a-meeting"), None);
    }

    #[test]
    fn doctor_takes_a_teams_id_and_a_passcode() {
        let Cli::Doctor {
            code,
            platform,
            passcode,
            wait_sec,
            ..
        } = parse(&["doctor", "1234567890123", "--passcode", "AbCd1234EfGh"])
        else {
            panic!("expected a doctor command");
        };
        assert_eq!(code, "1234567890123");
        // Not forced on the command line; `doctor` infers it.
        assert_eq!(platform, None);
        assert_eq!(passcode.as_deref(), Some("AbCd1234EfGh"));
        assert_eq!(wait_sec, DOCTOR_DEFAULT_WAIT_SEC);
    }

    #[test]
    fn doctor_platform_can_be_forced() {
        let Cli::Doctor { platform, .. } = parse(&["doctor", "99", "--platform", "teams"]) else {
            panic!("expected a doctor command");
        };
        assert_eq!(platform, Some(Platform::Teams));

        let Cli::Doctor { platform, .. } =
            parse(&["doctor", "bqy-ybgi-pbb", "--platform", "google_meet"])
        else {
            panic!("expected a doctor command");
        };
        assert_eq!(platform, Some(Platform::GoogleMeet));
    }

    #[test]
    fn doctor_rejects_an_unknown_platform() {
        let err = Cli::parse(
            ["doctor", "123", "--platform", "zoom"]
                .iter()
                .map(|s| s.to_string())
                .collect(),
        )
        .unwrap_err();
        assert!(err.to_string().contains("zoom"), "{err}");
    }

    /// The Meet-only invocation must be untouched by the Teams work.
    #[test]
    fn doctor_still_parses_the_original_meet_invocation() {
        let Cli::Doctor {
            code,
            wait_sec,
            headed,
            platform,
            passcode,
            ..
        } = parse(&["doctor", "bqy-ybgi-pbb", "--wait", "45", "--headed"])
        else {
            panic!("expected a doctor command");
        };
        assert_eq!(code, "bqy-ybgi-pbb");
        assert_eq!(wait_sec, 45);
        assert!(headed);
        assert_eq!(platform, None);
        assert_eq!(passcode, None);
    }

    #[test]
    fn serve_is_still_the_default_command() {
        assert!(matches!(parse(&[]), Cli::Serve { .. }));
        assert!(matches!(parse(&["serve"]), Cli::Serve { .. }));
        assert!(matches!(parse(&["config.toml"]), Cli::Serve { .. }));
    }

    /// The usage text is what an operator reads at 22:00; it has to mention
    /// both platforms and how to supply a Teams passcode.
    #[test]
    fn usage_documents_teams() {
        assert!(USAGE.contains("teams"));
        assert!(USAGE.contains("--passcode"));
        assert!(USAGE.contains("--platform"));
    }
}
