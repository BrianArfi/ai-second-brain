//! Proves the tab-audio capture path works, WITHOUT needing a real meeting.
//!
//! # Why this exists
//!
//! The 5 Aug 2026 outage could only be reproduced in a live call with real
//! people talking, because an empty room is legitimately silent and every
//! throughput counter looks healthy on silence. That made each iteration cost a
//! meeting off the owner's calendar, and it is why the bug survived two days and
//! two wrong fixes.
//!
//! Everything about the fix EXCEPT Google Meet's own DOM can be tested against
//! a page we control. This example launches Chrome with the production
//! `LAUNCH_FLAGS`, points it at a page playing a known 440 Hz tone, injects the
//! real `assets/capture.js`, and reads the tap's own `peak`. A working tab
//! capture reports peak near 1.0; the broken DOM path reports exactly 0.0,
//! because a WebAudio oscillator plays through no media element at all — which
//! makes this page a faithful stand-in for what Meet now does.
//!
//! What it proves: the flag applies, the injection carries the user gesture
//! getDisplayMedia demands, the returned stream is wired into the mixer, and
//! samples with real amplitude reach the frame path. What it cannot prove: that
//! Meet admits the bot and routes participants into the tab. That still needs a
//! live meeting.
//!
//! ```text
//! cargo run --release --example tabtap
//! ```
//!
//! Exits non-zero on failure, so it can gate a deploy.

use std::time::Duration;

use anyhow::{Context as _, Result, anyhow, bail};
use chromiumoxide::browser::{Browser, BrowserConfig};
use chromiumoxide::cdp::js_protocol::runtime::{AddBindingParams, EvaluateParams};
use futures::StreamExt;
use meetbot::meet::{LAUNCH_FLAGS, capture_script};
use meetbot::state::Platform;

/// A page whose only output is a continuous tone from its own AudioContext.
///
/// Deliberately NOT an `<audio>` or `<video>` element. Meet stopped putting
/// remote participants on media elements, so a test built on one would pass
/// against the very code path that is broken.
const TONE_PAGE: &str = r#"<!doctype html>
<meta charset="utf-8">
<title>meetbot tab tap probe</title>
<body>
<script>
  var ctx = new AudioContext();
  var osc = ctx.createOscillator();
  var gain = ctx.createGain();
  osc.frequency.value = 440;
  gain.gain.value = 0.5;
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start();
  window.__toneState = function () { return ctx.state; };
</script>
</body>"#;

/// One run of the probe. `disable_tab` neuters the getDisplayMedia call so the
/// tap can only fall back to the media-element path, which is how the run
/// proves it discriminates rather than just passing.
async fn probe(disable_tab: bool) -> Result<(String, String, f64)> {
    let chrome = std::env::var("MEETBOT_CHROME").unwrap_or_else(|_| {
        "~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome".to_string()
    });
    let page_path = std::env::temp_dir().join("meetbot-tabtap-probe.html");
    std::fs::write(&page_path, TONE_PAGE).context("could not write the probe page")?;

    let mut builder = BrowserConfig::builder()
        .chrome_executable(&chrome)
        .no_sandbox()
        // Match production exactly: --headless=new. The legacy headless
        // renderer has no WebAudio at all, so a probe on it would be testing a
        // different browser than the one that records meetings.
        .new_headless_mode()
        .viewport(None);
    if std::env::var("MEETBOT_TABTAP_HEADFUL").is_ok() {
        builder = builder.with_head();
    }
    let skip_fake = std::env::var("MEETBOT_TABTAP_NO_FAKE").is_ok();
    for (key, value) in LAUNCH_FLAGS {
        if skip_fake && *key == "use-fake-device-for-media-stream" {
            continue;
        }
        builder = match value {
            Some(v) => builder.arg((*key, *v)),
            None => builder.arg(*key),
        };
    }
    let extra_flags = std::env::var("MEETBOT_TABTAP_EXTRA").unwrap_or_default();
    {
        let extra = extra_flags.as_str();
        for f in extra.split(',').filter(|f| !f.is_empty()) {
            match f.split_once('=') {
                Some((k, v)) => builder = builder.arg((k, v)),
                None => builder = builder.arg(f),
            }
        }
    }
    let config = builder.build().map_err(|e| anyhow!("bad browser config: {e}"))?;

    let (mut browser, mut handler) = Browser::launch(config)
        .await
        .context("could not launch Chrome")?;
    let drive = tokio::spawn(async move { while handler.next().await.is_some() {} });

    let page = browser
        .new_page(format!("file://{}", page_path.display()))
        .await
        .context("could not open the probe page")?;
    page.wait_for_navigation().await.ok();

    // capture.js refuses to start without the binding it ships frames through.
    page.enable_runtime()
        .await
        .map_err(|e| anyhow!("could not enable Runtime: {e}"))?;
    page.execute(AddBindingParams::new("meetbotAudio"))
        .await
        .map_err(|e| anyhow!("could not create the binding: {e}"))?;

    // The same gesture-carrying evaluate `start_capture` uses. Without
    // `user_gesture(true)` getDisplayMedia rejects with NotAllowedError and the
    // tap falls silently back to the DOM path.
    let mut script = capture_script(Platform::GoogleMeet);
    if disable_tab {
        // Rename the API out from under the tap. `startTabCapture` then reports
        // `unsupported` and only the DOM scan is left, which is exactly the
        // configuration that was live on 5 Aug 2026.
        script = script.replace("getDisplayMedia", "getDisplayMediaDISABLED");
    }
    let params = EvaluateParams::builder()
        .expression(script)
        .user_gesture(true)
        .await_promise(true)
        .return_by_value(true)
        .build()
        .map_err(|e| anyhow!("could not build evaluate params: {e}"))?;
    let injected = page
        .evaluate(params)
        .await
        .map_err(|e| anyhow!("injection failed: {e}"))?;
    println!("injected: {:?}", injected.value());

    // Give getDisplayMedia time to settle and the mixer time to see real
    // samples. The tap's peak window is destructive-read, so the last read is
    // the one that matters.
    // Measure STEADY STATE, never the first window after attach.
    //
    // Wiring a MediaStreamSource into a running graph produces a one-block
    // transient — a click, at an amplitude that has nothing to do with the
    // signal. An earlier version of this probe broke out of the loop on the
    // first peak above the floor and reported an identical 1.0900 whether the
    // page's tone was at gain 0.5, 0.25 or 0.1. That is a probe measuring its
    // own connect click and calling the fix verified, which is the same species
    // of false green as the throughput counters that hid the outage. Read three
    // consecutive windows once the mode has settled and keep the last.
    let mut last = serde_json::Value::Null;
    let mut settled_reads = 0;
    for _ in 0..16 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        if std::env::var("MEETBOT_TABTAP_DUMP").is_ok() {
            if let Ok(v) = page
                .evaluate(
                    "(function(){var s=window.__meetbotCapture&&window.__meetbotCapture.tabSource;                     if(!s)return{none:true};return s.getAudioTracks().map(function(t){                     return {label:t.label,id:t.id,settings:t.getSettings()};});})()",
                )
                .await
            {
                println!("tracks: {:?}", v.value());
            }
        }
        let stats = page
            .evaluate("window.__meetbotCaptureStats()")
            .await
            .map_err(|e| anyhow!("could not read stats: {e}"))?;
        last = stats.value().cloned().unwrap_or(serde_json::Value::Null);
        let mode = last.get("mode").and_then(|v| v.as_str()).unwrap_or("?");
        let tab_state = last.get("tabState").and_then(|v| v.as_str()).unwrap_or("?");
        let peak = last.get("peak").and_then(|v| v.as_f64()).unwrap_or(0.0);
        println!("mode={mode} tab_state={tab_state} peak={peak:.4}");
        // `disable_tab` never leaves 'dom', so gate on the attempt settling
        // rather than on the mode, or the negative leg would spin the full 16.
        if tab_state != "pending" {
            settled_reads += 1;
            if settled_reads >= 3 {
                break;
            }
        }
    }

    let mode = last.get("mode").and_then(|v| v.as_str()).unwrap_or("?").to_string();
    let tab_state = last
        .get("tabState")
        .and_then(|v| v.as_str())
        .unwrap_or("?")
        .to_string();
    let peak = last.get("peak").and_then(|v| v.as_f64()).unwrap_or(0.0);

    let _ = browser.close().await;
    drive.abort();
    let _ = std::fs::remove_file(&page_path);

    Ok((mode, tab_state, peak))
}

#[tokio::main]
async fn main() -> Result<()> {
    // Negative leg FIRST. A probe that only ever runs the passing case cannot
    // tell "the fix works" from "the probe is incapable of failing", and a
    // green light that cannot go red is precisely what let the 5 Aug outage
    // hide behind healthy-looking counters for two days.
    println!("--- leg 1: getDisplayMedia disabled (the 5 Aug configuration) ---");
    let (mode, tab_state, peak) = probe(true).await?;
    println!("=> mode={mode} tab_state={tab_state} peak={peak:.4}");
    if mode == "tab" {
        bail!("BROKEN PROBE: tab capture engaged even with getDisplayMedia removed");
    }
    if peak > 0.01 {
        bail!(
            "BROKEN PROBE: the DOM tap heard the tone (peak={peak}). The probe page must be \
             inaudible to the media-element path or a passing leg 2 proves nothing."
        );
    }
    println!("ok: the DOM path is deaf to this page, as it was to Meet on 5 Aug\n");

    println!("--- leg 2: getDisplayMedia enabled (the fix) ---");
    let (mode, tab_state, peak) = probe(false).await?;
    println!("=> mode={mode} tab_state={tab_state} peak={peak:.4}");
    if tab_state == "fake-device" {
        bail!(
            "FAIL: Chrome returned its SYNTHETIC capture device, not the tab.              --use-fake-device-for-media-stream fakes getDisplayMedia's audio too, and              dropping that flag leaves getDisplayMedia with no audio backend at all              (NotReadableError, measured in every headless/headful and audio-service              combination on 6 Aug 2026). Tab capture cannot work on this host as flagged;              capture.js correctly refused the beep rather than recording it."
        );
    }
    if mode != "tab" {
        bail!("FAIL: tap never switched to tab capture (mode={mode}, tab_state={tab_state})");
    }
    if peak <= 0.01 {
        bail!(
            "FAIL: tab capture is attached but silent (peak={peak}); the tone never reached \
             the mixer"
        );
    }
    println!("\nPASS: the DOM path hears nothing, tab capture hears the tone (peak={peak:.4})");
    Ok(())
}
