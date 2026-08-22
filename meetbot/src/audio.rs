//! Audio plumbing: `AudioFrame`, `Utterance`, `VadConfig`, `Segmenter`,
//! `WavSink`, and the `run_segmenter` task. 16 kHz mono i16 everywhere.
//!
//! Contract: `SPEC.md` §6.1. Owner: the `audio` builder agent.
//!
//! Beyond the frozen §6.1 surface this module also owns the *ingest* side of
//! the capture path:
//!
//! * [`start_ingest_server`] — a tokio-tungstenite WebSocket **server** that
//!   `assets/capture.js` (running inside the Meet page) connects to at
//!   `ws://127.0.0.1:<port>/ingest`. Every binary message it receives is one
//!   PCM frame; every text message is a control/speaker event.
//! * [`Chunker`] — the long-form chunker. Where [`Segmenter`] cuts *utterances*
//!   on voice activity, `Chunker` cuts fixed ~20-30 s windows, preferring the
//!   quietest boundary inside the window and keeping ~1 s of overlap so a word
//!   straddling the cut survives in both chunks.
//! * [`dedupe_overlap`] / [`dedupe_chunk_texts`] — undo that overlap after
//!   transcription, dropping the duplicated leading words when consecutive
//!   chunks are stitched back together by timestamp.
//!
//! The extra surface is additive: nothing in §6.1 changed shape, so `whisper.rs`
//! and `session.rs` compile against the frozen signatures untouched.

use std::collections::VecDeque;
use std::io::Cursor;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use anyhow::{Context, anyhow};
// `futures` (already a dependency for chromiumoxide's Handler stream) re-exports
// futures-util; the WebSocketStream is a Stream + Sink and needs both traits.
use futures::{SinkExt, StreamExt};
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::handshake::server::{ErrorResponse, Request, Response};

pub const SAMPLE_RATE: u32 = 16_000;
pub const CHANNELS: u16 = 1;
pub const BITS_PER_SAMPLE: u16 = 16;

/// Full-scale reference for a signed 16-bit sample.
const FULL_SCALE: f32 = 32_768.0;

/// VAD analysis window. 20 ms at 16 kHz — small enough to place a boundary
/// tightly, large enough that a single glottal pulse does not toggle the gate.
const VAD_WINDOW_SAMPLES: usize = (SAMPLE_RATE as usize) / 50;

/// Overlap carried across a *hard* cut (max-length cut or chunk boundary), so a
/// word spanning the boundary is present at the tail of one chunk and the head
/// of the next. Undone after transcription by [`dedupe_overlap`].
pub const OVERLAP_SEC: f64 = 1.0;

// ---------------------------------------------------------------------------
// Frames and utterances
// ---------------------------------------------------------------------------

/// One capture buffer as produced by meet.rs.
#[derive(Debug, Clone)]
pub struct AudioFrame {
    pub pcm: Vec<i16>,
    /// Seconds elapsed since capture start (== meeting start_time origin).
    pub offset_sec: f64,
    /// Active speaker display name at capture time, if the page exposed one.
    pub speaker: Option<String>,
}

impl AudioFrame {
    pub fn duration_sec(&self) -> f64 {
        self.pcm.len() as f64 / SAMPLE_RATE as f64
    }

    pub fn is_silent(&self, threshold_rms: f32) -> bool {
        rms(&self.pcm) < threshold_rms
    }
}

/// A speech run cut out by the segmenter; the unit whisper transcribes.
#[derive(Debug, Clone)]
pub struct Utterance {
    pub pcm: Vec<i16>,
    pub start_time: f64,
    pub end_time: f64,
    pub speaker: Option<String>,
}

impl Utterance {
    pub fn duration_sec(&self) -> f64 {
        // Trust the sample count over the timestamps: the timestamps are
        // derived from it, and a caller-built Utterance may leave them at 0.
        if self.pcm.is_empty() {
            (self.end_time - self.start_time).max(0.0)
        } else {
            self.pcm.len() as f64 / SAMPLE_RATE as f64
        }
    }

    /// In-memory RIFF/WAVE bytes for the multipart upload.
    pub fn to_wav_bytes(&self) -> anyhow::Result<Vec<u8>> {
        encode_wav(&self.pcm)
    }
}

/// 16 kHz mono 16-bit RIFF/WAVE bytes for an arbitrary PCM buffer.
pub fn encode_wav(pcm: &[i16]) -> anyhow::Result<Vec<u8>> {
    let mut cursor = Cursor::new(Vec::<u8>::with_capacity(44 + pcm.len() * 2));
    {
        let mut writer = hound::WavWriter::new(&mut cursor, wav_spec())
            .context("initialising in-memory WAV writer")?;
        for &s in pcm {
            writer.write_sample(s).context("writing WAV sample")?;
        }
        writer.finalize().context("finalizing in-memory WAV")?;
    }
    Ok(cursor.into_inner())
}

fn wav_spec() -> hound::WavSpec {
    hound::WavSpec {
        channels: CHANNELS,
        sample_rate: SAMPLE_RATE,
        bits_per_sample: BITS_PER_SAMPLE,
        sample_format: hound::SampleFormat::Int,
    }
}

// ---------------------------------------------------------------------------
// VAD segmenter (SPEC §6.1)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct VadConfig {
    pub silence_rms: f32,      // 0.006 of full scale
    pub min_utterance_ms: u64, // 400   — shorter runs are discarded
    pub max_utterance_ms: u64, // 25_000 — hard cut, whisper chunk ceiling
    pub trailing_silence_ms: u64, // 700   — closes an utterance
    /// A speaker change also closes the current utterance.
    pub split_on_speaker_change: bool, // true
}

impl Default for VadConfig {
    fn default() -> Self {
        VadConfig {
            silence_rms: 0.006,
            min_utterance_ms: 400,
            max_utterance_ms: 25_000,
            trailing_silence_ms: 700,
            split_on_speaker_change: true,
        }
    }
}

/// Energy-gated utterance cutter. Not Sync; owned by one task.
///
/// The gate runs over 20 ms windows. Speech opens an utterance,
/// `trailing_silence_ms` of quiet closes it, and `max_utterance_ms` forces a
/// hard cut that carries [`OVERLAP_SEC`] of audio into the next utterance so no
/// word is lost at the seam.
pub struct Segmenter {
    cfg: VadConfig,
    /// Samples not yet aligned to a full analysis window.
    pending: Vec<i16>,
    /// Absolute capture time of `pending[0]`.
    pending_start: f64,
    /// True once `pending_start` has been seeded from a frame offset.
    clock_set: bool,
    /// Audio of the utterance currently being built.
    cur: Vec<i16>,
    cur_start: f64,
    cur_speaker: Option<String>,
    in_speech: bool,
    /// Consecutive silent samples at the tail of `cur`.
    silence_run: usize,
    /// Speaker reported by the frame currently being consumed.
    frame_speaker: Option<String>,
}

impl Segmenter {
    pub fn new(cfg: VadConfig) -> Segmenter {
        Segmenter {
            cfg,
            pending: Vec::new(),
            pending_start: 0.0,
            clock_set: false,
            cur: Vec::new(),
            cur_start: 0.0,
            cur_speaker: None,
            in_speech: false,
            silence_run: 0,
            frame_speaker: None,
        }
    }

    /// Feeds one frame; returns any utterances completed by it (usually 0 or 1).
    pub fn push(&mut self, frame: AudioFrame) -> Vec<Utterance> {
        let mut out = Vec::new();

        // Resync the clock whenever nothing is buffered: capture gaps (a dropped
        // WebSocket frame, a page stall) must not shift every later timestamp.
        if !self.clock_set || (self.pending.is_empty() && !self.in_speech) {
            self.pending_start = frame.offset_sec;
            self.clock_set = true;
        }

        if self.cfg.split_on_speaker_change && self.in_speech && frame.speaker != self.cur_speaker
            && let Some(u) = self.close_current(false) {
                out.push(u);
            }
        self.frame_speaker = frame.speaker.clone();

        self.pending.extend_from_slice(&frame.pcm);
        self.drain_windows(&mut out);
        out
    }

    /// Emits whatever is buffered (call during Finalizing).
    pub fn flush(&mut self) -> Option<Utterance> {
        // Fold any sub-window remainder into the open utterance so the tail of
        // the meeting is not silently dropped.
        if !self.pending.is_empty() {
            let tail = std::mem::take(&mut self.pending);
            if self.in_speech {
                self.cur.extend_from_slice(&tail);
            } else if rms(&tail) >= self.cfg.silence_rms {
                self.in_speech = true;
                self.cur_start = self.pending_start;
                self.cur_speaker = self.frame_speaker.clone();
                self.cur = tail;
                self.silence_run = 0;
            }
        }
        self.close_current(false)
    }

    fn drain_windows(&mut self, out: &mut Vec<Utterance>) {
        let mut consumed = 0usize;
        while self.pending.len() - consumed >= VAD_WINDOW_SAMPLES {
            let window_start = self.pending_start + samples_to_sec(consumed);
            let window = &self.pending[consumed..consumed + VAD_WINDOW_SAMPLES];
            let voiced = rms(window) >= self.cfg.silence_rms;

            if self.in_speech {
                self.cur.extend_from_slice(window);
                if voiced {
                    self.silence_run = 0;
                } else {
                    self.silence_run += VAD_WINDOW_SAMPLES;
                }
            } else if voiced {
                self.in_speech = true;
                self.cur_start = window_start;
                self.cur_speaker = self.frame_speaker.clone();
                self.cur.clear();
                self.cur.extend_from_slice(window);
                self.silence_run = 0;
            }
            consumed += VAD_WINDOW_SAMPLES;

            if self.in_speech {
                let close_for_silence =
                    self.silence_run >= ms_to_samples(self.cfg.trailing_silence_ms);
                let close_for_length = self.cur.len() >= ms_to_samples(self.cfg.max_utterance_ms);
                if close_for_silence || close_for_length {
                    // Advance the clock before closing: a hard cut derives the
                    // next utterance's start from the current end.
                    self.pending_start += samples_to_sec(consumed);
                    self.pending.drain(..consumed);
                    consumed = 0;
                    if let Some(u) = self.close_current(!close_for_silence) {
                        out.push(u);
                    }
                }
            }
        }

        if consumed > 0 {
            self.pending_start += samples_to_sec(consumed);
            self.pending.drain(..consumed);
        }
    }

    /// Closes the open utterance. `carry_overlap` keeps the last [`OVERLAP_SEC`]
    /// of audio as the head of the next one (used for hard length cuts only).
    fn close_current(&mut self, carry_overlap: bool) -> Option<Utterance> {
        if self.cur.is_empty() {
            self.in_speech = false;
            self.silence_run = 0;
            return None;
        }

        let mut pcm = std::mem::take(&mut self.cur);
        let silence_run = self.silence_run;
        let start = self.cur_start;
        let speaker = self.cur_speaker.clone();

        // Trim the trailing silence that closed this utterance, keeping a short
        // pad for whisper's context window. Without this the 700 ms of quiet is
        // counted as speech: a 100 ms blip would measure 800 ms and sail past
        // min_utterance_ms, and every clip would carry dead air to the ASR.
        // A hard length cut has no trailing silence to trim.
        if !carry_overlap && silence_run > 0 {
            let pad = ms_to_samples(100);
            let trim = silence_run.saturating_sub(pad);
            let kept = pcm.len().saturating_sub(trim).max(1);
            pcm.truncate(kept);
        }
        let end = start + samples_to_sec(pcm.len());

        if carry_overlap {
            let overlap = sec_to_samples(OVERLAP_SEC).min(pcm.len() / 2);
            self.cur = pcm[pcm.len() - overlap..].to_vec();
            self.cur_start = end - samples_to_sec(overlap);
            self.in_speech = true;
            self.silence_run = 0;
        } else {
            self.in_speech = false;
            self.silence_run = 0;
            self.cur_speaker = None;
        }

        if samples_to_sec(pcm.len()) * 1000.0 < self.cfg.min_utterance_ms as f64 {
            return None;
        }

        Some(Utterance {
            pcm,
            start_time: start,
            end_time: end,
            speaker,
        })
    }
}

// ---------------------------------------------------------------------------
// Long-form chunker
// ---------------------------------------------------------------------------

/// Chunking policy for [`Chunker`]. Defaults cut 20-30 s windows with 1 s of
/// overlap, which is where whisper-server's accuracy/latency tradeoff sits.
#[derive(Debug, Clone)]
pub struct ChunkConfig {
    /// Never cut before this much audio has accumulated.
    pub min_chunk_sec: f64, // 20.0
    /// Hard ceiling — cut here even if the audio never goes quiet.
    pub max_chunk_sec: f64, // 30.0
    /// Audio carried from the tail of one chunk into the head of the next.
    pub overlap_sec: f64, // 1.0
    /// RMS (fraction of full scale) below which a window counts as silence.
    pub silence_rms: f32, // 0.006
    /// Chunks shorter than this are dropped at flush time.
    pub min_flush_sec: f64, // 0.4
}

impl Default for ChunkConfig {
    fn default() -> Self {
        ChunkConfig {
            min_chunk_sec: 20.0,
            max_chunk_sec: 30.0,
            overlap_sec: 1.0,
            silence_rms: 0.006,
            min_flush_sec: 0.4,
        }
    }
}

/// Accumulates PCM and cuts ~20-30 s chunks, preferring the quietest 20 ms
/// window in the `[min_chunk_sec, max_chunk_sec]` band so the boundary lands
/// between words rather than inside one. Each cut keeps `overlap_sec` of audio
/// at the head of the next chunk; [`dedupe_overlap`] removes the resulting
/// duplicated words after transcription.
pub struct Chunker {
    cfg: ChunkConfig,
    buf: Vec<i16>,
    buf_start: f64,
    clock_set: bool,
    speaker: Option<String>,
}

impl Chunker {
    pub fn new(cfg: ChunkConfig) -> Chunker {
        Chunker {
            cfg,
            buf: Vec::new(),
            buf_start: 0.0,
            clock_set: false,
            speaker: None,
        }
    }

    /// Feeds one frame; returns every chunk it completed.
    pub fn push(&mut self, frame: AudioFrame) -> Vec<Utterance> {
        if !self.clock_set {
            self.buf_start = frame.offset_sec;
            self.clock_set = true;
        }
        if frame.speaker.is_some() {
            self.speaker = frame.speaker.clone();
        }
        self.buf.extend_from_slice(&frame.pcm);

        let mut out = Vec::new();
        while samples_to_sec(self.buf.len()) >= self.cfg.max_chunk_sec {
            let cut = self.pick_boundary();
            match self.cut_at(cut) {
                Some(u) => out.push(u),
                // cut_at only returns None for a zero-length cut, which
                // pick_boundary cannot produce; break rather than spin.
                None => break,
            }
        }
        out
    }

    /// Emits the residual buffer. Call once the frame channel has closed.
    pub fn flush(&mut self) -> Option<Utterance> {
        if samples_to_sec(self.buf.len()) < self.cfg.min_flush_sec {
            self.buf.clear();
            return None;
        }
        let len = self.buf.len();
        let out = self.cut_at(len);
        self.buf.clear();
        out
    }

    /// Quietest 20 ms window inside `[min_chunk_sec, max_chunk_sec]`, expressed
    /// as a sample index. Falls back to the hard ceiling when the band is empty.
    fn pick_boundary(&self) -> usize {
        let lo = sec_to_samples(self.cfg.min_chunk_sec);
        let hi = sec_to_samples(self.cfg.max_chunk_sec).min(self.buf.len());
        if lo + VAD_WINDOW_SAMPLES > hi {
            return hi;
        }

        let mut best = hi;
        let mut best_rms = f32::MAX;
        let mut idx = lo;
        while idx + VAD_WINDOW_SAMPLES <= hi {
            let energy = rms(&self.buf[idx..idx + VAD_WINDOW_SAMPLES]);
            if energy < best_rms {
                best_rms = energy;
                // Cut in the middle of the quiet window, not at its edge.
                best = idx + VAD_WINDOW_SAMPLES / 2;
            }
            // A window already under the silence floor is good enough; taking
            // the earliest such boundary keeps chunks near the target length.
            if energy < self.cfg.silence_rms {
                break;
            }
            idx += VAD_WINDOW_SAMPLES;
        }
        best.clamp(lo, hi)
    }

    fn cut_at(&mut self, idx: usize) -> Option<Utterance> {
        let idx = idx.min(self.buf.len());
        if idx == 0 {
            return None;
        }
        let pcm: Vec<i16> = self.buf[..idx].to_vec();
        let start = self.buf_start;
        let end = start + samples_to_sec(idx);

        let overlap = sec_to_samples(self.cfg.overlap_sec).min(idx / 2);
        let keep_from = idx - overlap;
        self.buf.drain(..keep_from);
        self.buf_start = end - samples_to_sec(overlap);

        Some(Utterance {
            pcm,
            start_time: start,
            end_time: end,
            speaker: self.speaker.clone(),
        })
    }
}

// ---------------------------------------------------------------------------
// Overlap dedupe
// ---------------------------------------------------------------------------

/// Longest word overlap [`dedupe_overlap`] will look for. `OVERLAP_SEC` of
/// speech is ~3 words; 12 leaves headroom for fast speech without matching an
/// unrelated repeated phrase.
const MAX_OVERLAP_WORDS: usize = 12;

/// Drops the leading words of `next` that merely repeat the tail of `prev`.
///
/// Chunks are cut with [`OVERLAP_SEC`] of shared audio, so whisper transcribes
/// the seam twice. This finds the longest suffix of `prev` that is also a prefix
/// of `next` (comparing case- and punctuation-insensitively) and returns `next`
/// with that prefix removed, preserving the original casing and punctuation of
/// whatever survives.
pub fn dedupe_overlap(prev: &str, next: &str) -> String {
    let prev_words = normalized_words(prev);
    let next_raw: Vec<&str> = next.split_whitespace().collect();
    let next_words = normalized_words(next);

    // `normalized_words` drops purely-punctuation tokens, so the raw and
    // normalized views can disagree in length; word-index arithmetic on the raw
    // slice would then cut in the wrong place. Bail out instead.
    if prev_words.is_empty() || next_words.is_empty() || next_words.len() != next_raw.len() {
        return next.trim().to_string();
    }

    let max_k = MAX_OVERLAP_WORDS
        .min(prev_words.len())
        .min(next_words.len());

    for k in (1..=max_k).rev() {
        let prev_tail = &prev_words[prev_words.len() - k..];
        let next_head = &next_words[..k];
        if prev_tail == next_head {
            return next_raw[k..].join(" ").trim().to_string();
        }
    }
    next.trim().to_string()
}

/// Stitches transcribed chunks in place: sorts by start time, then strips the
/// duplicated leading words of every chunk that overlaps its predecessor in
/// time. Chunks left with no text are removed.
///
/// Tuples are `(start_time, end_time, text)` in seconds elapsed from meeting
/// start — the same units `db::NewSegment` uses, kept as a tuple here so
/// `audio.rs` stays dependency-free (SPEC §3).
pub fn dedupe_chunk_texts(chunks: &mut Vec<(f64, f64, String)>) {
    chunks.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));

    let mut prev_end = f64::NEG_INFINITY;
    let mut prev_text = String::new();
    let mut idx = 0;
    while idx < chunks.len() {
        if chunks[idx].0 < prev_end - 1e-6 && !prev_text.is_empty() {
            chunks[idx].2 = dedupe_overlap(&prev_text, &chunks[idx].2);
        }
        if chunks[idx].2.trim().is_empty() {
            chunks.remove(idx);
            continue;
        }
        prev_end = chunks[idx].1;
        prev_text = chunks[idx].2.clone();
        idx += 1;
    }
}

fn normalized_words(s: &str) -> Vec<String> {
    s.split_whitespace()
        .map(|w| {
            w.chars()
                .filter(|c| c.is_alphanumeric())
                .flat_map(|c| c.to_lowercase())
                .collect::<String>()
        })
        .filter(|w| !w.is_empty())
        .collect()
}

// ---------------------------------------------------------------------------
// WAV sink
// ---------------------------------------------------------------------------

/// Streaming WAV sink for `recording_enabled`.
pub struct WavSink {
    writer: hound::WavWriter<std::io::BufWriter<std::fs::File>>,
    samples: u64,
}

impl WavSink {
    pub fn create(path: &Path) -> anyhow::Result<WavSink> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating audio dir {}", parent.display()))?;
        }
        let writer = hound::WavWriter::create(path, wav_spec())
            .with_context(|| format!("creating WAV file {}", path.display()))?;
        Ok(WavSink { writer, samples: 0 })
    }

    pub fn write(&mut self, pcm: &[i16]) -> anyhow::Result<()> {
        for &s in pcm {
            self.writer.write_sample(s).context("writing WAV sample")?;
        }
        self.samples += pcm.len() as u64;
        Ok(())
    }

    /// Finalizes the RIFF header; returns total samples written.
    pub fn finalize(self) -> anyhow::Result<u64> {
        let n = self.samples;
        self.writer.finalize().context("finalizing WAV file")?;
        Ok(n)
    }
}

// ---------------------------------------------------------------------------
// PCM helpers
// ---------------------------------------------------------------------------

pub fn rms(pcm: &[i16]) -> f32 {
    if pcm.is_empty() {
        return 0.0;
    }
    // Accumulate in f64: 30 s of full-scale audio overflows an f32 sum.
    let sum: f64 = pcm
        .iter()
        .map(|&s| {
            let v = s as f64;
            v * v
        })
        .sum();
    ((sum / pcm.len() as f64).sqrt() as f32) / FULL_SCALE
}

/// Little-endian i16 bytes (CDP payload) -> samples.
pub fn pcm_from_le_bytes(bytes: &[u8]) -> Vec<i16> {
    bytes
        .chunks_exact(2)
        .map(|c| i16::from_le_bytes([c[0], c[1]]))
        .collect()
}

/// f32 [-1,1] samples (WebAudio) -> i16, clamped.
pub fn pcm_from_f32(samples: &[f32]) -> Vec<i16> {
    samples
        .iter()
        .map(|&s| {
            let clamped = s.clamp(-1.0, 1.0);
            // 32767 rather than 32768 so +1.0 does not wrap to -32768.
            (clamped * 32_767.0).round() as i16
        })
        .collect()
}

/// Little-endian f32 bytes (the `capture.js` wire format) -> samples.
pub fn f32_from_le_bytes(bytes: &[u8]) -> Vec<f32> {
    bytes
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

/// Linear resample to SAMPLE_RATE. No-op when `from == SAMPLE_RATE`.
pub fn resample_to_16k(pcm: &[i16], from: u32) -> Vec<i16> {
    if from == SAMPLE_RATE || from == 0 || pcm.is_empty() {
        return pcm.to_vec();
    }
    let ratio = SAMPLE_RATE as f64 / from as f64;
    let out_len = ((pcm.len() as f64) * ratio).round().max(1.0) as usize;
    let last = pcm.len() - 1;
    let mut out = Vec::with_capacity(out_len);
    for i in 0..out_len {
        let src = i as f64 / ratio;
        let i0 = (src.floor() as usize).min(last);
        let i1 = (i0 + 1).min(last);
        let frac = src - i0 as f64;
        let a = pcm[i0] as f64;
        let b = pcm[i1] as f64;
        out.push((a + (b - a) * frac).round().clamp(-32768.0, 32767.0) as i16);
    }
    out
}

fn samples_to_sec(n: usize) -> f64 {
    n as f64 / SAMPLE_RATE as f64
}

fn sec_to_samples(sec: f64) -> usize {
    (sec.max(0.0) * SAMPLE_RATE as f64).round() as usize
}

fn ms_to_samples(ms: u64) -> usize {
    (ms as usize * SAMPLE_RATE as usize) / 1000
}

// ---------------------------------------------------------------------------
// Segmenter / chunker tasks
// ---------------------------------------------------------------------------

/// Long-running task: frames in, utterances out, optional WAV on the side.
/// Returns when `frames` closes, after flushing the segmenter.
pub async fn run_segmenter(
    mut frames: mpsc::Receiver<AudioFrame>,
    cfg: VadConfig,
    utterances: mpsc::Sender<Utterance>,
    wav_path: Option<PathBuf>,
) -> anyhow::Result<Option<PathBuf>> {
    let mut segmenter = Segmenter::new(cfg);
    let mut sink = match &wav_path {
        Some(p) => Some(WavSink::create(p)?),
        None => None,
    };

    while let Some(frame) = frames.recv().await {
        if let Some(s) = sink.as_mut()
            && let Err(e) = s.write(&frame.pcm) {
                // A broken recording must never take the session down (§0.1).
                tracing::warn!(error = %e, "WAV sink write failed; dropping recording");
                sink = None;
            }
        for u in segmenter.push(frame) {
            if utterances.send(u).await.is_err() {
                tracing::warn!("utterance channel closed; segmenter stopping early");
                return finish_sink(sink, wav_path);
            }
        }
    }

    if let Some(u) = segmenter.flush() {
        let _ = utterances.send(u).await;
    }
    drop(utterances);
    finish_sink(sink, wav_path)
}

/// Same shape as [`run_segmenter`] but cuts fixed ~20-30 s overlapping chunks
/// instead of VAD utterances. Use when whisper accuracy matters more than
/// latency; the duplicated words at each seam are removed afterwards with
/// [`dedupe_chunk_texts`].
pub async fn run_chunker(
    mut frames: mpsc::Receiver<AudioFrame>,
    cfg: ChunkConfig,
    utterances: mpsc::Sender<Utterance>,
    wav_path: Option<PathBuf>,
) -> anyhow::Result<Option<PathBuf>> {
    let mut chunker = Chunker::new(cfg);
    let mut sink = match &wav_path {
        Some(p) => Some(WavSink::create(p)?),
        None => None,
    };

    while let Some(frame) = frames.recv().await {
        if let Some(s) = sink.as_mut()
            && let Err(e) = s.write(&frame.pcm) {
                tracing::warn!(error = %e, "WAV sink write failed; dropping recording");
                sink = None;
            }
        for u in chunker.push(frame) {
            if utterances.send(u).await.is_err() {
                tracing::warn!("utterance channel closed; chunker stopping early");
                return finish_sink(sink, wav_path);
            }
        }
    }

    if let Some(u) = chunker.flush() {
        let _ = utterances.send(u).await;
    }
    drop(utterances);
    finish_sink(sink, wav_path)
}

fn finish_sink(sink: Option<WavSink>, wav_path: Option<PathBuf>) -> anyhow::Result<Option<PathBuf>> {
    match sink {
        Some(s) => {
            let samples = s.finalize()?;
            tracing::info!(samples, "WAV recording closed");
            Ok(wav_path)
        }
        None => Ok(None),
    }
}

// ---------------------------------------------------------------------------
// WebSocket ingest server
// ---------------------------------------------------------------------------

/// Path `assets/capture.js` connects to.
pub const INGEST_PATH: &str = "/ingest";

/// How many *consecutive* `accept()` errors the ingest loop tolerates before it
/// gives up. Transient errors (ECONNABORTED on a peer that vanished mid
/// handshake) clear the counter on the next success; a persistent one
/// (EMFILE/ENFILE, a broken listener fd) never will.
pub const MAX_ACCEPT_FAILURES: u32 = 10;

/// First backoff step after a failed `accept()`.
const ACCEPT_BACKOFF_BASE_MS: u64 = 20;

/// Ceiling for the exponential backoff between failed `accept()` calls.
const ACCEPT_BACKOFF_MAX_MS: u64 = 2_000;

/// Backoff before retrying `accept()` after `consecutive` consecutive failures
/// (1-based). `None` means the loop has exhausted [`MAX_ACCEPT_FAILURES`] and
/// must stop rather than spin.
///
/// Doubling from [`ACCEPT_BACKOFF_BASE_MS`], clamped to
/// [`ACCEPT_BACKOFF_MAX_MS`]. Pure so the policy is unit-testable without a
/// socket.
pub fn accept_backoff(consecutive: u32) -> Option<std::time::Duration> {
    if consecutive == 0 || consecutive > MAX_ACCEPT_FAILURES {
        return None;
    }
    let shift = (consecutive - 1).min(20);
    let ms = ACCEPT_BACKOFF_BASE_MS
        .saturating_mul(1u64 << shift)
        .min(ACCEPT_BACKOFF_MAX_MS);
    Some(std::time::Duration::from_millis(ms))
}

/// Handles for the per-connection tasks spawned by the accept loop, shared
/// between that loop (which pushes) and [`IngestServer::drop`] (which aborts).
///
/// A plain `std::sync::Mutex` is correct here because the lock is only ever
/// taken for a push/drain and never held across an `.await`.
type ConnTasks = Arc<Mutex<Vec<JoinHandle<()>>>>;

/// A running ingest listener. Dropping it aborts the accept loop **and every
/// live connection task**, which closes the frames channel and drives the
/// segmenter to flush — the normal end-of-capture path.
///
/// Aborting the connection tasks is a resource-leak fix, not a correctness one:
/// end-of-capture is signalled out of band by `session.rs`'s `capture_done`
/// oneshot, precisely so finalization never has to wait on Chrome tearing its
/// socket down. Before this, only the accept task was aborted, so a connection
/// task held its cloned `frames` sender until the socket closed or the process
/// exited.
pub struct IngestServer {
    /// Actual bound address; port 0 in the request yields the OS-assigned port.
    pub local_addr: SocketAddr,
    task: JoinHandle<()>,
    conns: ConnTasks,
}

impl IngestServer {
    pub fn port(&self) -> u16 {
        self.local_addr.port()
    }

    /// `ws://127.0.0.1:<port>/ingest` — the URL to hand to `capture.js`.
    pub fn ingest_url(&self) -> String {
        format!("ws://{}{}", self.local_addr, INGEST_PATH)
    }

    /// Stops accepting and drops all connections.
    ///
    /// Takes `self` by value without destructuring, so [`Drop`] still runs and
    /// does the actual aborting; `abort` on an already-finished task is a no-op.
    pub fn shutdown(self) {}

    /// Number of connection handles currently tracked. Test-facing.
    #[cfg(test)]
    fn tracked_conns(&self) -> usize {
        self.conns.lock().map(|c| c.len()).unwrap_or(0)
    }
}

impl Drop for IngestServer {
    fn drop(&mut self) {
        self.task.abort();
        // A poisoned lock still yields the Vec, and aborting is the safe action
        // either way, so recover rather than skip the cleanup.
        let mut conns = match self.conns.lock() {
            Ok(c) => c,
            Err(poisoned) => poisoned.into_inner(),
        };
        for handle in conns.drain(..) {
            handle.abort();
        }
    }
}

/// Binds a WebSocket server and forwards every PCM frame it receives onto
/// `frames`.
///
/// Wire format spoken by `assets/capture.js`:
///
/// * **binary** — `[f64 offset_sec LE][f32 samples LE ...]`, mono, already at
///   [`SAMPLE_RATE`]. `offset_sec` is seconds since capture start.
/// * **text** — JSON. `{"type":"hello","sampleRate":16000}` announces the
///   context rate (anything other than 16 kHz is resampled here);
///   `{"type":"speaker","name":"YourManager","speaking":true,"t":12.4}` updates the
///   active speaker attached to subsequent frames; `{"type":"bye"}` ends the
///   stream.
///
/// Bind on port 0 to let the OS pick, then read [`IngestServer::port`].
pub async fn start_ingest_server(
    bind: SocketAddr,
    frames: mpsc::Sender<AudioFrame>,
) -> anyhow::Result<IngestServer> {
    let listener = TcpListener::bind(bind)
        .await
        .with_context(|| format!("binding audio ingest listener on {bind}"))?;
    let local_addr = listener
        .local_addr()
        .context("reading ingest listener address")?;
    tracing::info!(%local_addr, "audio ingest websocket listening");

    let conns: ConnTasks = Arc::new(Mutex::new(Vec::new()));
    let accept_conns = conns.clone();

    let task = tokio::spawn(async move {
        let mut consecutive_failures: u32 = 0;
        loop {
            let (stream, peer) = match listener.accept().await {
                Ok(v) => {
                    consecutive_failures = 0;
                    v
                }
                Err(e) => {
                    consecutive_failures += 1;
                    match accept_backoff(consecutive_failures) {
                        Some(delay) => {
                            // Do NOT `continue` straight into `accept()` again:
                            // a persistent error (EMFILE/ENFILE, the listener fd
                            // going bad) returns instantly and would pin a core
                            // in a tight loop for the rest of the call.
                            tracing::warn!(
                                error = %e,
                                attempt = consecutive_failures,
                                backoff_ms = delay.as_millis() as u64,
                                "ingest accept failed; backing off"
                            );
                            tokio::time::sleep(delay).await;
                            continue;
                        }
                        None => {
                            tracing::error!(
                                error = %e,
                                attempts = consecutive_failures,
                                "ingest accept failed repeatedly; stopping accept loop"
                            );
                            // Returning drops `frames`, which closes the frames
                            // channel and drives the segmenter to flush — the
                            // normal end-of-capture path, so the session still
                            // finalizes with whatever it captured.
                            return;
                        }
                    }
                }
            };
            let frames = frames.clone();
            let handle = tokio::spawn(async move {
                match serve_ingest_conn(stream, frames).await {
                    Ok(()) => tracing::info!(%peer, "ingest connection closed"),
                    Err(e) => tracing::warn!(%peer, error = %e, "ingest connection ended with error"),
                }
            });
            // Track it so `IngestServer::drop` can abort it. Reap finished
            // handles on the way in: capture.js reconnects on error, so a long
            // call would otherwise grow this Vec without bound.
            if let Ok(mut tracked) = accept_conns.lock() {
                tracked.retain(|h: &JoinHandle<()>| !h.is_finished());
                tracked.push(handle);
            }
        }
    });

    Ok(IngestServer {
        local_addr,
        task,
        conns,
    })
}

// clippy::result_large_err: the Err type here is tungstenite's `ErrorResponse`
// (an `http::Response<Option<String>>`), fixed by the `accept_hdr_async`
// callback signature. It cannot be boxed without failing to satisfy the trait,
// and it is constructed at most once per rejected handshake.
#[allow(clippy::result_large_err)]
async fn serve_ingest_conn(
    stream: tokio::net::TcpStream,
    frames: mpsc::Sender<AudioFrame>,
) -> anyhow::Result<()> {
    let _ = stream.set_nodelay(true);

    let ws = tokio_tungstenite::accept_hdr_async(
        stream,
        |req: &Request, resp: Response| -> Result<Response, ErrorResponse> {
            if req.uri().path() == INGEST_PATH {
                Ok(resp)
            } else {
                let body = Some(format!("unknown ingest path {}", req.uri().path()));
                // `builder()` lives on Response<()>, i.e. the `Response` alias;
                // `ErrorResponse` is Response<Option<String>> and has no builder.
                Err(Response::builder()
                    .status(404)
                    .body(body)
                    .expect("static 404 response builds"))
            }
        },
    )
    .await
    .context("websocket handshake")?;

    let (mut sink, mut source) = ws.split();
    let mut state = IngestState::default();

    while let Some(msg) = source.next().await {
        let msg = msg.context("reading ingest websocket message")?;
        match msg {
            Message::Binary(payload) => {
                if let Some(frame) = state.decode_frame(&payload)
                    && frames.send(frame).await.is_err() {
                        // Consumer gone: the session is finalizing.
                        break;
                    }
            }
            Message::Text(text) => {
                if state.handle_control(text.as_str()) {
                    break;
                }
            }
            Message::Ping(p) => {
                let _ = sink.send(Message::Pong(p)).await;
            }
            Message::Close(_) => break,
            _ => {}
        }
    }

    tracing::debug!(frames = state.frames_seen, "ingest stream finished");
    let _ = sink.close().await;
    Ok(())
}

/// Per-connection decoding state for the ingest protocol.
#[derive(Debug)]
struct IngestState {
    /// Sample rate announced by the page; anything else is resampled to 16 kHz.
    source_rate: u32,
    speaker: Option<String>,
    frames_seen: u64,
}

impl Default for IngestState {
    fn default() -> Self {
        IngestState {
            source_rate: SAMPLE_RATE,
            speaker: None,
            frames_seen: 0,
        }
    }
}

impl IngestState {
    fn decode_frame(&mut self, payload: &[u8]) -> Option<AudioFrame> {
        if payload.len() < 8 {
            tracing::warn!(len = payload.len(), "ingest frame too short; dropped");
            return None;
        }
        let mut off = [0u8; 8];
        off.copy_from_slice(&payload[..8]);
        let offset_sec = f64::from_le_bytes(off);

        let samples = f32_from_le_bytes(&payload[8..]);
        if samples.is_empty() {
            return None;
        }
        let pcm = pcm_from_f32(&samples);
        let pcm = resample_to_16k(&pcm, self.source_rate);

        self.frames_seen += 1;
        Some(AudioFrame {
            pcm,
            offset_sec,
            speaker: self.speaker.clone(),
        })
    }

    /// Returns true when the peer signalled end-of-stream.
    fn handle_control(&mut self, text: &str) -> bool {
        let v: serde_json::Value = match serde_json::from_str(text) {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!(error = %e, "unparseable ingest control message");
                return false;
            }
        };
        match v.get("type").and_then(|t| t.as_str()) {
            Some("hello") => {
                if let Some(rate) = v.get("sampleRate").and_then(|r| r.as_u64())
                    && rate > 0 {
                        self.source_rate = rate as u32;
                        tracing::info!(rate, "capture.js announced sample rate");
                    }
                false
            }
            Some("speaker") => {
                let speaking = v.get("speaking").and_then(|s| s.as_bool()).unwrap_or(true);
                let name = v
                    .get("name")
                    .and_then(|n| n.as_str())
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty());
                if speaking {
                    self.speaker = name;
                } else if self.speaker == name {
                    self.speaker = None;
                }
                false
            }
            Some("bye") => true,
            _ => false,
        }
    }
}

/// The page-side capture script for **this** transport, compiled in so `meet.rs`
/// can inject it without a runtime file dependency.
///
/// `assets/capture.js` is the other transport (the CDP binding of SPEC §7, owned
/// by `meet.rs`); `assets/capture_ws.js` talks to [`start_ingest_server`]
/// instead. Inject one or the other, never both.
pub const CAPTURE_WS_JS: &str = include_str!("../assets/capture_ws.js");

/// `capture_ws.js` with its `__MEETBOT_INGEST_URL__` placeholder bound to a real
/// ingest URL. Hand the result to `Runtime.evaluate` /
/// `Page.addScriptToEvaluateOnNewDocument`.
pub fn capture_ws_script(ingest_url: &str) -> String {
    CAPTURE_WS_JS.replace("__MEETBOT_INGEST_URL__", ingest_url)
}

/// Rolling view of capture health for callers that want to answer "have we
/// heard anything lately?". Purely observational.
#[derive(Debug, Default)]
pub struct LevelMonitor {
    window: VecDeque<f32>,
    capacity: usize,
}

impl LevelMonitor {
    pub fn new(capacity: usize) -> LevelMonitor {
        let capacity = capacity.max(1);
        LevelMonitor {
            window: VecDeque::with_capacity(capacity),
            capacity,
        }
    }

    pub fn observe(&mut self, frame: &AudioFrame) {
        if self.window.len() == self.capacity {
            self.window.pop_front();
        }
        self.window.push_back(rms(&frame.pcm));
    }

    /// True when every observed frame was below `threshold`.
    pub fn is_silent(&self, threshold: f32) -> bool {
        !self.window.is_empty() && self.window.iter().all(|&r| r < threshold)
    }

    pub fn peak(&self) -> f32 {
        self.window.iter().copied().fold(0.0f32, f32::max)
    }
}

/// Convenience for callers that only have raw parts.
pub fn frame_from_f32(samples: &[f32], offset_sec: f64, speaker: Option<String>) -> AudioFrame {
    AudioFrame {
        pcm: pcm_from_f32(samples),
        offset_sec,
        speaker,
    }
}

/// Reads a mono WAV back into 16 kHz PCM (tests, replay tooling).
pub fn read_wav_16k(path: &Path) -> anyhow::Result<Vec<i16>> {
    let mut reader =
        hound::WavReader::open(path).with_context(|| format!("opening WAV {}", path.display()))?;
    let spec = reader.spec();
    if spec.channels != 1 {
        return Err(anyhow!("expected mono WAV, got {} channels", spec.channels));
    }
    let samples: Result<Vec<i16>, _> = reader.samples::<i16>().collect();
    let samples = samples.context("decoding WAV samples")?;
    Ok(resample_to_16k(&samples, spec.sample_rate))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(n: usize, amp: f32) -> Vec<i16> {
        (0..n)
            .map(|i| {
                let t = i as f32 / SAMPLE_RATE as f32;
                ((t * 220.0 * std::f32::consts::TAU).sin() * amp * 32_767.0) as i16
            })
            .collect()
    }

    fn silence(n: usize) -> Vec<i16> {
        vec![0; n]
    }

    fn frame(pcm: Vec<i16>, offset: f64) -> AudioFrame {
        AudioFrame {
            pcm,
            offset_sec: offset,
            speaker: None,
        }
    }

    #[test]
    fn rms_is_normalized_to_full_scale() {
        assert_eq!(rms(&[]), 0.0);
        assert_eq!(rms(&silence(160)), 0.0);
        let loud = rms(&vec![32_767i16; 160]);
        assert!((loud - 1.0).abs() < 0.001, "got {loud}");
    }

    #[test]
    fn pcm_roundtrips_through_f32_and_le_bytes() {
        let src = vec![0.0f32, 0.5, -0.5, 1.0, -1.0];
        let pcm = pcm_from_f32(&src);
        assert_eq!(pcm[0], 0);
        assert_eq!(pcm[3], 32_767);
        assert_eq!(pcm[4], -32_767);

        let bytes: Vec<u8> = pcm.iter().flat_map(|s| s.to_le_bytes()).collect();
        assert_eq!(pcm_from_le_bytes(&bytes), pcm);

        let f32_bytes: Vec<u8> = src.iter().flat_map(|s| s.to_le_bytes()).collect();
        assert_eq!(f32_from_le_bytes(&f32_bytes), src);
    }

    #[test]
    fn resample_halves_and_doubles_length() {
        let src = tone(16_000, 0.5);
        assert_eq!(resample_to_16k(&src, SAMPLE_RATE).len(), src.len());
        let up = resample_to_16k(&src, 8_000);
        assert_eq!(up.len(), 32_000);
        let down = resample_to_16k(&src, 48_000);
        assert!((down.len() as i64 - 5_333).abs() <= 2, "got {}", down.len());
    }

    #[test]
    fn wav_bytes_have_riff_header_and_payload() {
        let u = Utterance {
            pcm: tone(16_000, 0.4),
            start_time: 0.0,
            end_time: 1.0,
            speaker: None,
        };
        let wav = u.to_wav_bytes().expect("encode");
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
        assert_eq!(wav.len(), 44 + 32_000);
    }

    #[test]
    fn segmenter_cuts_on_trailing_silence_and_drops_short_blips() {
        let mut seg = Segmenter::new(VadConfig::default());
        let mut out = Vec::new();
        // 1 s of speech, then 1 s of silence -> exactly one utterance.
        out.extend(seg.push(frame(tone(16_000, 0.4), 0.0)));
        out.extend(seg.push(frame(silence(16_000), 1.0)));
        assert_eq!(out.len(), 1, "expected one utterance, got {}", out.len());
        assert!(out[0].duration_sec() >= 1.0);
        assert!(out[0].start_time < 0.1);

        // A 100 ms blip is below min_utterance_ms and must be discarded.
        let mut seg = Segmenter::new(VadConfig::default());
        let mut out = Vec::new();
        out.extend(seg.push(frame(tone(1_600, 0.4), 0.0)));
        out.extend(seg.push(frame(silence(16_000), 0.1)));
        assert!(out.is_empty(), "short blip should be dropped");
    }

    #[test]
    fn segmenter_hard_cuts_at_max_with_overlap() {
        let cfg = VadConfig {
            max_utterance_ms: 2_000,
            trailing_silence_ms: 5_000,
            ..VadConfig::default()
        };
        let mut seg = Segmenter::new(cfg);
        let mut out = Vec::new();
        // 5 s of continuous speech -> repeated hard cuts at 2 s.
        for i in 0..5 {
            out.extend(seg.push(frame(tone(16_000, 0.4), i as f64)));
        }
        assert!(out.len() >= 2, "expected hard cuts, got {}", out.len());
        // Consecutive utterances overlap by ~OVERLAP_SEC.
        let overlap = out[0].end_time - out[1].start_time;
        assert!(
            (overlap - OVERLAP_SEC).abs() < 0.05,
            "expected ~1s overlap, got {overlap}"
        );
    }

    #[test]
    fn segmenter_splits_on_speaker_change() {
        let mut seg = Segmenter::new(VadConfig::default());
        let mut out = Vec::new();
        out.extend(seg.push(AudioFrame {
            pcm: tone(16_000, 0.4),
            offset_sec: 0.0,
            speaker: Some("YourManager".into()),
        }));
        out.extend(seg.push(AudioFrame {
            pcm: tone(16_000, 0.4),
            offset_sec: 1.0,
            speaker: Some("the operator".into()),
        }));
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].speaker.as_deref(), Some("YourManager"));
        let tail = seg.flush().expect("second speaker flushes");
        assert_eq!(tail.speaker.as_deref(), Some("the operator"));
    }

    #[test]
    fn segmenter_flush_emits_open_utterance() {
        let mut seg = Segmenter::new(VadConfig::default());
        assert!(seg.push(frame(tone(16_000, 0.4), 0.0)).is_empty());
        let u = seg.flush().expect("open utterance must flush");
        assert!(u.duration_sec() >= 0.9);
        assert!(seg.flush().is_none(), "flush must be idempotent");
    }

    #[test]
    fn silent_meeting_produces_no_utterances() {
        let mut seg = Segmenter::new(VadConfig::default());
        for i in 0..30 {
            assert!(seg.push(frame(silence(16_000), i as f64)).is_empty());
        }
        assert!(seg.flush().is_none());
    }

    #[test]
    fn chunker_cuts_between_20_and_30_seconds_with_overlap() {
        let mut ch = Chunker::new(ChunkConfig::default());
        let mut out = Vec::new();
        for i in 0..70 {
            out.extend(ch.push(frame(tone(16_000, 0.4), i as f64)));
        }
        assert!(!out.is_empty(), "expected chunks from 70 s of audio");
        for c in &out {
            let d = c.duration_sec();
            assert!((20.0..=30.0).contains(&d), "chunk out of range: {d}s");
        }
        for pair in out.windows(2) {
            let overlap = pair[0].end_time - pair[1].start_time;
            assert!(
                (overlap - OVERLAP_SEC).abs() < 0.05,
                "expected 1s overlap, got {overlap}"
            );
        }
    }

    #[test]
    fn chunker_prefers_a_silent_boundary() {
        let mut ch = Chunker::new(ChunkConfig::default());
        let mut out = Vec::new();
        // 22 s speech, 1 s silence, then more speech: the cut should land in
        // the quiet stretch (~22-23 s), not at the 30 s ceiling.
        out.extend(ch.push(frame(tone(22 * 16_000, 0.4), 0.0)));
        out.extend(ch.push(frame(silence(16_000), 22.0)));
        out.extend(ch.push(frame(tone(10 * 16_000, 0.4), 23.0)));
        assert!(!out.is_empty());
        let end = out[0].end_time;
        assert!(
            (21.9..=23.2).contains(&end),
            "cut should land in the silence, got {end}"
        );
    }

    #[test]
    fn dedupe_overlap_strips_repeated_leading_words() {
        assert_eq!(
            dedupe_overlap("we should ship the seller portal", "the seller portal next week"),
            "next week"
        );
        // Case and punctuation insensitive.
        assert_eq!(
            dedupe_overlap("...done with PIM sync.", "PIM sync, and then OMS"),
            "and then OMS"
        );
        // No overlap: next is returned untouched.
        assert_eq!(
            dedupe_overlap("totally unrelated", "brand new sentence"),
            "brand new sentence"
        );
        // Empty inputs are safe.
        assert_eq!(dedupe_overlap("", "hello there"), "hello there");
        assert_eq!(dedupe_overlap("hello there", ""), "");
    }

    #[test]
    fn dedupe_chunk_texts_stitches_by_timestamp() {
        let mut chunks = vec![
            (25.0, 50.0, "the seller portal next week and PIM".to_string()),
            (0.0, 26.0, "we should ship the seller portal".to_string()),
            (49.0, 70.0, "PIM sync is done".to_string()),
        ];
        dedupe_chunk_texts(&mut chunks);
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks[0].2, "we should ship the seller portal");
        assert_eq!(chunks[1].2, "next week and PIM");
        assert_eq!(chunks[2].2, "sync is done");
    }

    #[test]
    fn dedupe_chunk_texts_drops_fully_duplicated_chunks() {
        let mut chunks = vec![
            (0.0, 10.0, "hello world".to_string()),
            (9.0, 20.0, "hello world".to_string()),
        ];
        dedupe_chunk_texts(&mut chunks);
        assert_eq!(chunks.len(), 1);
    }

    #[test]
    fn dedupe_leaves_non_overlapping_chunks_alone() {
        // Same words, but the chunks do not overlap in time: a genuine repeat.
        let mut chunks = vec![
            (0.0, 10.0, "hello world".to_string()),
            (12.0, 20.0, "hello world".to_string()),
        ];
        dedupe_chunk_texts(&mut chunks);
        assert_eq!(chunks.len(), 2);
    }

    #[test]
    fn ingest_state_decodes_binary_frames() {
        let mut st = IngestState::default();
        let mut payload = 12.5f64.to_le_bytes().to_vec();
        for s in [0.0f32, 0.5, -0.5] {
            payload.extend_from_slice(&s.to_le_bytes());
        }
        let frame = st.decode_frame(&payload).expect("frame decodes");
        assert_eq!(frame.offset_sec, 12.5);
        assert_eq!(frame.pcm.len(), 3);
        assert!(st.decode_frame(&[0u8; 4]).is_none(), "short frame dropped");
    }

    #[test]
    fn ingest_state_tracks_speaker_and_rate() {
        let mut st = IngestState::default();
        assert!(!st.handle_control(r#"{"type":"hello","sampleRate":48000}"#));
        assert_eq!(st.source_rate, 48_000);

        assert!(!st.handle_control(r#"{"type":"speaker","name":"YourManager","speaking":true,"t":1.0}"#));
        assert_eq!(st.speaker.as_deref(), Some("YourManager"));

        assert!(!st.handle_control(r#"{"type":"speaker","name":"YourManager","speaking":false,"t":2.0}"#));
        assert_eq!(st.speaker, None);

        assert!(st.handle_control(r#"{"type":"bye"}"#));
        assert!(
            !st.handle_control("not json"),
            "garbage must not kill the stream"
        );
    }

    #[test]
    fn wav_sink_roundtrips_to_disk() {
        let dir = std::env::temp_dir().join(format!("meetbot-wav-{}", std::process::id()));
        let path = dir.join("test.wav");
        let mut sink = WavSink::create(&path).expect("create sink");
        let pcm = tone(8_000, 0.3);
        sink.write(&pcm).expect("write");
        sink.write(&pcm).expect("write");
        let total = sink.finalize().expect("finalize");
        assert_eq!(total, 16_000);

        let back = read_wav_16k(&path).expect("read back");
        assert_eq!(back.len(), 16_000);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn run_segmenter_flushes_and_writes_wav() {
        let dir = std::env::temp_dir().join(format!("meetbot-seg-{}", std::process::id()));
        let wav = dir.join("meeting.wav");

        let (ftx, frx) = mpsc::channel::<AudioFrame>(16);
        let (utx, mut urx) = mpsc::channel::<Utterance>(16);

        let task = tokio::spawn(run_segmenter(
            frx,
            VadConfig::default(),
            utx,
            Some(wav.clone()),
        ));

        ftx.send(frame(tone(16_000, 0.4), 0.0)).await.unwrap();
        ftx.send(frame(silence(16_000), 1.0)).await.unwrap();
        drop(ftx);

        let mut got = Vec::new();
        while let Some(u) = urx.recv().await {
            got.push(u);
        }
        let out = task.await.expect("join").expect("segmenter ok");

        assert_eq!(got.len(), 1);
        assert_eq!(out.as_deref(), Some(wav.as_path()));
        assert!(wav.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Regression: `IngestServer::drop` used to abort only the accept task, so a
    // per-connection task outlived the server and kept its cloned `frames`
    // sender alive until the client socket closed or the process exited.
    //
    // The observable consequence is exactly that: with the client socket held
    // OPEN on purpose, dropping the server must still close the frames channel
    // (`recv()` -> `None`). Before the fix this timed out, because the leaked
    // connection task still held a sender.
    #[tokio::test]
    async fn dropping_ingest_server_aborts_live_connection_tasks() {
        let (tx, mut rx) = mpsc::channel::<AudioFrame>(64);
        // `start_ingest_server` takes `tx` by value, so the accept task and the
        // connection tasks own the only senders in existence.
        let server = start_ingest_server("127.0.0.1:0".parse().unwrap(), tx)
            .await
            .expect("bind ingest server");
        let url = server.ingest_url();

        let (mut ws, _resp) = tokio_tungstenite::connect_async(&url)
            .await
            .expect("client connects");
        ws.send(Message::Text(
            r#"{"type":"hello","sampleRate":16000}"#.into(),
        ))
        .await
        .unwrap();

        // Round-trip one frame so the connection task is provably live and has
        // its own clone of the sender before we drop the server.
        let mut payload = 1.0f64.to_le_bytes().to_vec();
        for _ in 0..320 {
            payload.extend_from_slice(&0.1f32.to_le_bytes());
        }
        ws.send(Message::Binary(payload.into())).await.unwrap();
        let frame = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv())
            .await
            .expect("frame arrives in time")
            .expect("frame present");
        assert_eq!(frame.pcm.len(), 320);
        assert_eq!(
            server.tracked_conns(),
            1,
            "the connection task must be tracked for drop to reach it"
        );

        // Note what we do NOT do: no `bye`, no close, no dropping `ws`. The
        // socket stays open, which is the leak scenario.
        drop(server);

        let closed = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv())
            .await
            .expect("frames channel must close once the server is dropped");
        assert!(
            closed.is_none(),
            "channel should be closed, got another frame instead"
        );

        drop(ws);
    }

    #[tokio::test]
    async fn ingest_server_accepts_capture_js_protocol() {
        let (tx, mut rx) = mpsc::channel::<AudioFrame>(64);
        let server = start_ingest_server("127.0.0.1:0".parse().unwrap(), tx)
            .await
            .expect("bind ingest server");
        let url = server.ingest_url();
        assert!(url.ends_with("/ingest"));

        let (mut ws, _resp) = tokio_tungstenite::connect_async(&url)
            .await
            .expect("client connects");

        ws.send(Message::Text(
            r#"{"type":"hello","sampleRate":16000}"#.into(),
        ))
        .await
        .unwrap();
        ws.send(Message::Text(
            r#"{"type":"speaker","name":"YourManager","speaking":true,"t":0.0}"#.into(),
        ))
        .await
        .unwrap();

        let mut payload = 3.25f64.to_le_bytes().to_vec();
        for i in 0..320 {
            let v = ((i as f32) / 320.0).sin() * 0.4;
            payload.extend_from_slice(&v.to_le_bytes());
        }
        ws.send(Message::Binary(payload.into())).await.unwrap();

        let frame = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv())
            .await
            .expect("frame arrives in time")
            .expect("frame present");
        assert_eq!(frame.offset_sec, 3.25);
        assert_eq!(frame.pcm.len(), 320);
        assert_eq!(frame.speaker.as_deref(), Some("YourManager"));

        ws.send(Message::Text(r#"{"type":"bye"}"#.into()))
            .await
            .unwrap();
        server.shutdown();
    }

    #[tokio::test]
    async fn ingest_server_rejects_unknown_path() {
        let (tx, _rx) = mpsc::channel::<AudioFrame>(4);
        let server = start_ingest_server("127.0.0.1:0".parse().unwrap(), tx)
            .await
            .expect("bind");
        let bad = format!("ws://{}/nope", server.local_addr);
        assert!(
            tokio_tungstenite::connect_async(&bad).await.is_err(),
            "unknown path must be rejected"
        );
        server.shutdown();
    }

    // Regression: the accept loop used to `continue` on any accept error with no
    // delay, so a persistent EMFILE/ENFILE pinned a core for the rest of the
    // call. The retry policy must back off and be bounded.
    #[test]
    fn accept_backoff_grows_and_is_bounded() {
        let first = accept_backoff(1).expect("first retry allowed");
        let second = accept_backoff(2).expect("second retry allowed");
        assert!(second > first, "backoff must grow: {first:?} -> {second:?}");

        // Never zero (a zero delay is the hot spin this guards against) and
        // never unbounded.
        let mut total = std::time::Duration::ZERO;
        for n in 1..=MAX_ACCEPT_FAILURES {
            let d = accept_backoff(n).expect("within budget");
            assert!(d > std::time::Duration::ZERO);
            assert!(d <= std::time::Duration::from_millis(ACCEPT_BACKOFF_MAX_MS));
            total += d;
        }
        assert!(
            total <= std::time::Duration::from_secs(30),
            "give-up must arrive promptly, took {total:?}"
        );
    }

    #[test]
    fn accept_backoff_gives_up_after_max_failures() {
        assert!(accept_backoff(MAX_ACCEPT_FAILURES).is_some());
        assert!(
            accept_backoff(MAX_ACCEPT_FAILURES + 1).is_none(),
            "loop must stop instead of spinning forever"
        );
        assert!(accept_backoff(u32::MAX).is_none());
        assert!(accept_backoff(0).is_none());
    }

    #[test]
    fn capture_script_binds_the_ingest_url() {
        let js = capture_ws_script("ws://127.0.0.1:8765/ingest");
        assert!(js.contains("ws://127.0.0.1:8765/ingest"));
        // The script rebuilds the placeholder at runtime by concatenation so it
        // can detect an un-substituted injection; that split form must survive,
        // but no literal placeholder may remain.
        assert!(!js.contains("__MEETBOT_INGEST_URL__"));
        assert!(js.contains("__MEETBOT_INGEST\" + \"_URL__"));
    }
}
