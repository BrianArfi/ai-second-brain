//! Answers two questions about `chrome.tabCapture` in a bundled extension,
//! without needing a meeting or a human.
//!
//! 1. **Can it run unattended?** `chrome.tabCapture` has historically required
//!    the extension to have been "invoked for the current tab", which normally
//!    means a click on the toolbar action. If that gate is real, meetbot cannot
//!    use it: the whole point is that the owner is not there. Nothing in
//!    `tabcapture_ext/` is wired to a click, so if capture succeeds here it
//!    succeeds unattended.
//!
//! 2. **Does it dodge the fake device?** `--use-fake-device-for-media-stream`
//!    substituted a synthetic 440 Hz beep for `getDisplayMedia`'s audio on
//!    6 Aug 2026, which would have made every meeting look healthy while
//!    recording a test tone. `tabCapture` reaches the stream through
//!    `getUserMedia`, which is the API that flag most directly targets, so this
//!    is the thing to measure rather than assume.
//!
//! The page under test plays a 440 Hz tone at a gain this harness controls, and
//! the run asserts the measured peak MOVES with that gain. A peak that ignores
//! the source is not a measurement -- that is exactly how the getDisplayMedia
//! path passed while capturing nothing.
//!
//! ```text
//! cargo run --release --example extcapture
//! ```

use std::io::{Read, Write};
use std::net::TcpListener;
use std::time::Duration;

use anyhow::{Result, anyhow, bail};
use chromiumoxide::browser::{Browser, BrowserConfig};
use chromiumoxide::cdp::browser_protocol::input::{DispatchKeyEventParams, DispatchKeyEventType};
use futures::StreamExt;
use meetbot::meet::LAUNCH_FLAGS;

/// Serves the tone page on a loopback port and returns the base URL.
///
/// HTTP rather than `file://` on purpose: extensions have no file access
/// without a profile setting that cannot be set from the command line, and Meet
/// is https anyway.
fn serve_tone(gain: f64) -> Result<String> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    let body = format!(
        "<!doctype html><meta charset=\"utf-8\"><title>tone</title><body><script>\
         var ctx=new AudioContext();var osc=ctx.createOscillator();\
         var g=ctx.createGain();osc.frequency.value=440;g.gain.value={gain};\
         osc.connect(g);g.connect(ctx.destination);osc.start();\
         window.__toneState=function(){{return ctx.state;}};</script></body>"
    );
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut s) = stream else { continue };
            let mut buf = [0u8; 1024];
            let _ = s.read(&mut buf);
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\
                 Content-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            let _ = s.write_all(resp.as_bytes());
        }
    });
    Ok(format!("http://127.0.0.1:{port}/"))
}

async fn probe(gain: f64) -> Result<serde_json::Value> {
    let base = serve_tone(gain)?;

    // The extension needs the target URL baked in, because it finds the tab
    // itself rather than being pointed at one by a human.
    let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("examples/tabcapture_ext");
    let dir = std::env::temp_dir().join(format!("meetbot-tabcapture-ext-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir)?;
    for name in ["manifest.json", "sw.js", "offscreen.html", "offscreen.js"] {
        let text = std::fs::read_to_string(src.join(name))?.replace("__TARGET__", &base);
        std::fs::write(dir.join(name), text)?;
    }

    let chrome = std::env::var("MEETBOT_CHROME").unwrap_or_else(|_| {
        "~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome".to_string()
    });
    let skip_fake = std::env::var("MEETBOT_EXT_NO_FAKE").is_ok();

    let mut builder = BrowserConfig::builder()
        .chrome_executable(&chrome)
        .no_sandbox()
        .new_headless_mode()
        .extension(dir.to_string_lossy().to_string())
        .viewport(None);
    for (key, value) in LAUNCH_FLAGS {
        if skip_fake && *key == "use-fake-device-for-media-stream" {
            continue;
        }
        builder = match value {
            Some(v) => builder.arg((*key, *v)),
            None => builder.arg(*key),
        };
    }
    let config = builder.build().map_err(|e| anyhow!("bad browser config: {e}"))?;

    let (mut browser, mut handler) = Browser::launch(config).await?;
    let drive = tokio::spawn(async move { while handler.next().await.is_some() {} });

    let page = browser.new_page(base.as_str()).await?;
    page.wait_for_navigation().await.ok();

    let mut result = serde_json::Value::Null;
    for i in 0..40 {
        tokio::time::sleep(Duration::from_millis(500)).await;

        // At ~2s, fire the extension's keyboard command. A command is one of
        // the four invocations Chrome accepts for activeTab, and the only one
        // deliverable without a person at the keyboard -- so whether this
        // clears the gate decides if an extension is usable for an unattended
        // bot at all.
        if i == 4 {
            let key = DispatchKeyEventParams::builder()
                .r#type(DispatchKeyEventType::RawKeyDown)
                .modifiers(2 | 8) // Ctrl + Shift
                .windows_virtual_key_code(85) // U
                .native_virtual_key_code(85)
                .key("U")
                .code("KeyU")
                .build()
                .map_err(|e| anyhow!("bad key event: {e}"))?;
            let _ = page.execute(key).await;
            let up = DispatchKeyEventParams::builder()
                .r#type(DispatchKeyEventType::KeyUp)
                .modifiers(2 | 8)
                .windows_virtual_key_code(85)
                .native_virtual_key_code(85)
                .key("U")
                .code("KeyU")
                .build()
                .map_err(|e| anyhow!("bad key event: {e}"))?;
            let _ = page.execute(up).await;
            println!("(dispatched Ctrl+Shift+U)");
        }

        if let Ok(v) = page.evaluate("window.__tabCaptureProbe || null").await {
            if let Some(val) = v.value() {
                if !val.is_null() {
                    result = val.clone();
                    if result.get("ok").and_then(|b| b.as_bool()) == Some(true) {
                        break;
                    }
                }
            }
        }
    }

    let _ = browser.close().await;
    drive.abort();
    let _ = std::fs::remove_dir_all(&dir);

    if result.is_null() {
        bail!("the extension never reported; it may not have loaded at all");
    }
    Ok(result)
}

#[tokio::main]
async fn main() -> Result<()> {
    let fake = if std::env::var("MEETBOT_EXT_NO_FAKE").is_ok() {
        "WITHOUT --use-fake-device-for-media-stream"
    } else {
        "with production flags"
    };
    println!("--- chrome.tabCapture, unattended, {fake} ---\n");

    let loud = probe(0.5).await?;
    println!("gain 0.5 -> {loud}");
    if loud.get("ok").and_then(|v| v.as_bool()) != Some(true) {
        let err = loud.get("error").and_then(|v| v.as_str()).unwrap_or("?");
        bail!(
            "FAIL: tabCapture did not produce a stream unattended: {err}\n\
             If this mentions the extension not being invoked, tabCapture needs a user \
             gesture and is unusable for an unattended bot."
        );
    }

    // The control. If the peak is identical with the page silent, the capture is
    // synthetic and the loud run proves nothing.
    let quiet = probe(0.0).await?;
    println!("gain 0.0 -> {quiet}\n");

    let loud_peak = loud.get("peak").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let quiet_peak = quiet.get("peak").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let label = loud.get("label").and_then(|v| v.as_str()).unwrap_or("?");

    println!("label      : {label}");
    println!("peak loud  : {loud_peak:.4}");
    println!("peak silent: {quiet_peak:.4}");

    if loud_peak <= 0.01 {
        bail!("FAIL: capture ran unattended but heard nothing (peak={loud_peak:.4})");
    }
    if quiet_peak > 0.01 {
        bail!(
            "FAIL: SYNTHETIC AUDIO. The page was silent and the capture still reported \
             peak={quiet_peak:.4} (label {label:?}). This is the getDisplayMedia failure \
             again: it would look healthy on every meeting while recording a test tone."
        );
    }
    println!(
        "\nPASS: unattended tabCapture returns REAL tab audio \
         (loud {loud_peak:.4}, silent {quiet_peak:.4})"
    );
    Ok(())
}
