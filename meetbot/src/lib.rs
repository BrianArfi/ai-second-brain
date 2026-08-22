//! meetbot — a Rust drop-in replacement for the self-hosted Vexa Lite API.
//!
//! It sends a headless Chromium note-taker into Google Meet / Teams calls,
//! captures the remote audio, transcribes it against the local whisper-server,
//! and serves the result over the exact HTTP contract that
//! `meeting-recorder/vexa_bots.py` already speaks.
//!
//! The wire contract, the session state machine, and every module's public
//! type/function signature are frozen in `SPEC.md` at the repo root. Module
//! authors implement their own file against those signatures and nothing else.
//!
//! Module map (see `SPEC.md` §3):
//!
//! | module     | responsibility                                            |
//! |------------|-----------------------------------------------------------|
//! | [`state`]  | `Config`, `Platform`, `MeetingKey`, `AppState`, handles    |
//! | [`db`]     | SQLite persistence: meetings + segments                    |
//! | [`audio`]  | PCM types, VAD segmenter, WAV sink                         |
//! | [`whisper`]| OpenAI-compatible transcription client + worker            |
//! | [`meet`]   | CDP browser automation: join, admission, capture, leave    |
//! | [`teams`]  | the Microsoft Teams selector table + `coords` URL decoding |
//! | [`session`]| the lifecycle orchestrator; sole writer of terminal status |
//! | [`api`]    | axum HTTP surface                                          |
//!
//! The single most important invariant (`SPEC.md` §0.1): a bot that is never
//! admitted, or that sits through a silent meeting, finishes as
//! `status = "completed"` with zero segments — never `failed`, never `stopped`.
//! The Python client reads that combination as `skipped_not_admitted`, an
//! operational skip rather than an alertable failure.

pub mod api;
pub mod audio;
pub mod db;
pub mod meet;
pub mod pulse;
pub mod session;
pub mod state;
pub mod teams;
pub mod whisper;
