//! End-to-end check of the PulseAudio capture path, without a meeting.
//!
//! Launches Chrome with the production `LAUNCH_FLAGS` on a page playing a tone
//! this harness controls, records the configured Pulse monitor through the real
//! [`meetbot::pulse::PulseRecorder`], and asserts the measured peak MOVES with
//! the page's gain.
//!
//! The control run is the point. A capture that reports the same peak whether
//! the page is loud or silent is not capturing the page -- that is exactly how
//! the `getDisplayMedia` attempt passed while recording Chrome's synthetic test
//! tone, and a probe without a silent leg would have shipped it.
//!
//! ```text
//! cargo run --release --example pulsetap                 # RDPSink.monitor
//! MEETBOT_PULSE_SOURCE=other.monitor cargo run --release --example pulsetap
//! ```

use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Result, anyhow, bail};
use chromiumoxide::browser::{Browser, BrowserConfig};
use futures::StreamExt;
use meetbot::meet::LAUNCH_FLAGS;
use meetbot::pulse::{PulseRecorder, ensure_null_sink};

fn serve_tone(gain: f64) -> Result<String> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    let body = format!(
        "<!doctype html><meta charset=\"utf-8\"><title>tone</title><body><script>\
         var ctx=new AudioContext();var osc=ctx.createOscillator();\
         var g=ctx.createGain();osc.frequency.value=440;g.gain.value={gain};\
         osc.connect(g);g.connect(ctx.destination);osc.start();</script></body>"
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

async fn probe(sink: &str, source: &str, gain: f64) -> Result<(f64, f64, u64)> {
    let url = serve_tone(gain)?;
    let chrome = std::env::var("MEETBOT_CHROME").unwrap_or_else(|_| {
        "~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome".to_string()
    });

    // Exactly what `MeetSession::launch` does: headed builder plus our own
    // `--headless=new`, so chromiumoxide never gets the chance to append the
    // bare `--mute-audio` that would silence the sink.
    let mut builder = BrowserConfig::builder()
        .chrome_executable(&chrome)
        .no_sandbox()
        .with_head()
        .arg(("headless", "new"))
        // Chrome must play into OUR sink, not the shared RDPSink. Recording
        // RDPSink.monitor is what wedged WSLg's audio server on 6 Aug 2026.
        .env("PULSE_SINK", sink)
        .viewport(None);
    for (key, value) in LAUNCH_FLAGS {
        builder = match value {
            Some(v) => builder.arg((*key, *v)),
            None => builder.arg(*key),
        };
    }
    let config = builder.build().map_err(|e| anyhow!("bad browser config: {e}"))?;

    let (mut browser, mut handler) = Browser::launch(config).await?;
    let drive = tokio::spawn(async move { while handler.next().await.is_some() {} });
    let page = browser.new_page(url.as_str()).await?;
    page.wait_for_navigation().await.ok();

    let (tx, mut rx) = tokio::sync::mpsc::channel(256);
    let drain = tokio::spawn(async move {
        let mut n = 0u64;
        while rx.recv().await.is_some() {
            n += 1;
        }
        n
    });

    let rec = PulseRecorder::start(source, tx, Arc::new(Mutex::new(None)))?;

    // Let Chrome's output stream come up, then discard that first window: the
    // stream starting is not the meeting starting.
    tokio::time::sleep(Duration::from_secs(3)).await;
    rec.stats.take_peak();
    tokio::time::sleep(Duration::from_secs(3)).await;
    let (peak, loud_ratio) = rec.stats.take_peak();
    let frames = rec.stats.frames.load(std::sync::atomic::Ordering::Relaxed);

    drop(rec);
    let _ = browser.close().await;
    drive.abort();
    drain.abort();
    Ok((peak as f64, loud_ratio, frames))
}

#[tokio::main]
async fn main() -> Result<()> {
    let sink = std::env::var("MEETBOT_PULSE_SINK").unwrap_or_else(|_| "meetbot".to_string());
    let source = format!("{sink}.monitor");
    match ensure_null_sink(&sink) {
        Ok(true) => println!("created null sink `{sink}`"),
        Ok(false) => println!("null sink `{sink}` already present"),
        Err(e) => bail!("{e:#}"),
    }
    println!("source: {source}\n");

    println!("--- leg 1: page SILENT (control) ---");
    let (quiet_peak, quiet_ratio, quiet_frames) = probe(&sink, &source, 0.0).await?;
    println!("peak={quiet_peak:.4} loud_ratio={quiet_ratio:.2} frames={quiet_frames}");
    if quiet_frames == 0 {
        bail!("FAIL: no frames at all; the recorder is not reading `{source}`");
    }
    if quiet_peak > 0.01 {
        bail!(
            "FAIL: the monitor carried audio while the page was silent (peak={quiet_peak:.4}). \
             Either something else on this host is playing into that sink, or the capture is \
             synthetic. Either way a loud leg would prove nothing."
        );
    }
    println!("ok: silent page, silent monitor\n");

    println!("--- leg 2: page playing 440 Hz at gain 0.5 ---");
    let (peak, ratio, frames) = probe(&sink, &source, 0.5).await?;
    println!("peak={peak:.4} loud_ratio={ratio:.2} frames={frames}");
    if peak <= 0.01 {
        bail!(
            "FAIL: Chrome played a tone and the monitor heard nothing (peak={peak:.4}). \
             Check that no --mute-audio reached the command line, that Chrome is using \
             PulseAudio (run it with --enable-logging=stderr --v=1 and grep for \
             audio_manager_name), and that `{source}` is the monitor of the sink it plays into."
        );
    }
    println!(
        "\nPASS: silent {quiet_peak:.4} -> loud {peak:.4}. The capture tracks what Chrome plays."
    );
    Ok(())
}
