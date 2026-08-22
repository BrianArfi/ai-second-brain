//! SQLite persistence: `MeetingStatus`, `MeetingRecord`, `NewMeeting`,
//! `Segment`, `NewSegment`, `Db`.
//!
//! Contract: `SPEC.md` §5. Owner: the `db` builder agent.
//!
//! # Concurrency choice
//!
//! `Db` wraps `Arc<Mutex<rusqlite::Connection>>` (a **std** mutex, not a tokio
//! one) and every method is **synchronous**, exactly as `SPEC.md` §3 mandates.
//! rusqlite is blocking, the database is a local file on the same host, and no
//! statement here touches more than a few hundred rows, so every call is
//! sub-millisecond and is safe to make directly from an async task without
//! `spawn_blocking`. The guard is never held across an `.await` because no
//! method in this module is `async`.
//!
//! # Timestamps
//!
//! Stored as RFC3339 UTC strings with second precision and a trailing `Z`
//! (`2026-07-19T10:42:07Z`) — the exact form `vexa_bots.py` feeds to
//! `datetime.fromisoformat`. That format sorts lexicographically in the same
//! order it sorts chronologically, so `ORDER BY created_at DESC` is correct.
//! Rows created inside the same second are disambiguated by `rowid DESC`.

use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use anyhow::{Context, anyhow};
use chrono::{DateTime, SecondsFormat, Utc};
use rusqlite::{Connection, OptionalExtension, Row, params};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::state::{MeetingKey, Platform};

/// Every column of `meetings`, in a fixed order, so a single row mapper serves
/// every query in this module.
const MEETING_COLUMNS: &str = "id, platform, native_meeting_id, constructed_meeting_url, \
     title, bot_name, language, passcode, recording_enabled, transcribe_enabled, \
     status, error, audio_path, start_time, end_time, created_at, updated_at";

/// The SQL predicate that selects rows in a non-terminal status. Kept in one
/// place so `active_meeting`, `count_active` and `sweep_stale` can never drift
/// apart from [`MeetingStatus::is_terminal`].
const NON_TERMINAL_SQL: &str = "status NOT IN ('completed', 'failed', 'stopped')";

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS meetings (
    id                      TEXT PRIMARY KEY,
    platform                TEXT NOT NULL,
    native_meeting_id       TEXT NOT NULL,
    constructed_meeting_url TEXT,
    title                   TEXT,
    bot_name                TEXT NOT NULL,
    language                TEXT,
    passcode                TEXT,
    recording_enabled       INTEGER NOT NULL DEFAULT 0,
    transcribe_enabled      INTEGER NOT NULL DEFAULT 1,
    status                  TEXT NOT NULL,
    error                   TEXT,
    audio_path              TEXT,
    start_time              TEXT,
    end_time                TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_key
    ON meetings(platform, native_meeting_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_meetings_status
    ON meetings(status);

CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_time REAL NOT NULL,
    end_time   REAL NOT NULL,
    speaker    TEXT,
    text       TEXT NOT NULL,
    language   TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_segments_meeting
    ON segments(meeting_id, start_time);
"#;

/// Wire values are exactly these snake_case strings.
/// TERMINAL: Completed | Failed | Stopped  (see `SPEC.md` §0.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MeetingStatus {
    /// Row created, session task not started yet.
    Requested,
    /// Browser launching / navigating.
    Joining,
    /// Sitting in the waiting room.
    AwaitingAdmission,
    /// In call, capturing audio.
    Active,
    /// Draining audio + awaiting in-flight whisper calls.
    Finalizing,
    /// TERMINAL. Also the correct status for "never admitted" and "silent
    /// meeting" — see `SPEC.md` §0.1.
    Completed,
    /// TERMINAL. Bot crashed, browser died, join threw.
    Failed,
    /// TERMINAL. Stopped or removed *while audio was already flowing*.
    Stopped,
}

impl MeetingStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            MeetingStatus::Requested => "requested",
            MeetingStatus::Joining => "joining",
            MeetingStatus::AwaitingAdmission => "awaiting_admission",
            MeetingStatus::Active => "active",
            MeetingStatus::Finalizing => "finalizing",
            MeetingStatus::Completed => "completed",
            MeetingStatus::Failed => "failed",
            MeetingStatus::Stopped => "stopped",
        }
    }

    pub fn parse(s: &str) -> Option<MeetingStatus> {
        match s {
            "requested" => Some(MeetingStatus::Requested),
            "joining" => Some(MeetingStatus::Joining),
            "awaiting_admission" => Some(MeetingStatus::AwaitingAdmission),
            "active" => Some(MeetingStatus::Active),
            "finalizing" => Some(MeetingStatus::Finalizing),
            "completed" => Some(MeetingStatus::Completed),
            "failed" => Some(MeetingStatus::Failed),
            "stopped" => Some(MeetingStatus::Stopped),
            _ => None,
        }
    }

    /// Completed | Failed | Stopped — exactly the client's tuple
    /// (`vexa_bots.py` lines 326 / 538).
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            MeetingStatus::Completed | MeetingStatus::Failed | MeetingStatus::Stopped
        )
    }
}

impl std::fmt::Display for MeetingStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MeetingRecord {
    pub id: Uuid,
    pub platform: Platform,
    pub native_meeting_id: String,
    pub constructed_meeting_url: Option<String>,
    pub title: Option<String>,
    pub bot_name: String,
    pub language: Option<String>,
    pub passcode: Option<String>,
    pub recording_enabled: bool,
    pub transcribe_enabled: bool,
    pub status: MeetingStatus,
    pub error: Option<String>,
    pub audio_path: Option<String>,
    pub start_time: Option<DateTime<Utc>>,
    pub end_time: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl MeetingRecord {
    pub fn key(&self) -> MeetingKey {
        MeetingKey::new(self.platform, self.native_meeting_id.clone())
    }
}

#[derive(Debug, Clone)]
pub struct NewMeeting {
    pub key: MeetingKey,
    pub title: Option<String>,
    pub bot_name: String,
    pub language: Option<String>,
    pub passcode: Option<String>,
    pub recording_enabled: bool,
    pub transcribe_enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Segment {
    pub id: i64,
    pub meeting_id: Uuid,
    /// Seconds elapsed from the meeting start, **not** a timestamp.
    pub start_time: f64,
    pub end_time: f64,
    pub speaker: Option<String>,
    pub text: String,
    pub language: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// What `whisper.rs` produces and `db.rs` persists.
#[derive(Debug, Clone, PartialEq)]
pub struct NewSegment {
    pub start_time: f64,
    pub end_time: f64,
    pub speaker: Option<String>,
    pub text: String,
    pub language: Option<String>,
}

/// Handle to the meetbot SQLite database.
///
/// `Clone + Send + Sync`; clones share one connection behind a mutex. All
/// methods are synchronous and safe to call directly from async code.
#[derive(Debug, Clone)]
pub struct Db {
    conn: Arc<Mutex<Connection>>,
}

impl Db {
    /// Opens (creating parent directories), sets WAL + `busy_timeout`, and runs
    /// [`Db::migrate`].
    pub fn open(path: &Path) -> anyhow::Result<Db> {
        if let Some(parent) = path.parent()
            && !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)
                    .with_context(|| format!("creating db directory {}", parent.display()))?;
            }
        let conn = Connection::open(path)
            .with_context(|| format!("opening sqlite database {}", path.display()))?;
        Db::from_connection(conn)
    }

    pub fn open_in_memory() -> anyhow::Result<Db> {
        let conn = Connection::open_in_memory().context("opening in-memory sqlite database")?;
        Db::from_connection(conn)
    }

    fn from_connection(conn: Connection) -> anyhow::Result<Db> {
        conn.busy_timeout(Duration::from_secs(5))
            .context("setting sqlite busy_timeout")?;
        // `PRAGMA journal_mode` returns a row, so it must be queried rather
        // than executed. In-memory databases cannot do WAL and answer
        // "memory"; that is fine and not an error.
        let _: String = conn
            .query_row("PRAGMA journal_mode=WAL", [], |row| row.get(0))
            .context("enabling WAL journal mode")?;
        conn.pragma_update(None, "synchronous", "NORMAL")
            .context("setting synchronous=NORMAL")?;
        conn.pragma_update(None, "foreign_keys", true)
            .context("enabling foreign_keys")?;

        let db = Db {
            conn: Arc::new(Mutex::new(conn)),
        };
        db.migrate()?;
        Ok(db)
    }

    fn conn(&self) -> anyhow::Result<MutexGuard<'_, Connection>> {
        self.conn
            .lock()
            .map_err(|_| anyhow!("meetbot database mutex was poisoned by a panicking task"))
    }

    /// Idempotent `CREATE TABLE IF NOT EXISTS` schema migration.
    pub fn migrate(&self) -> anyhow::Result<()> {
        let conn = self.conn()?;
        conn.execute_batch(SCHEMA)
            .context("running meetbot schema migration")?;
        Ok(())
    }

    // ---------------------------------------------------------------- meetings

    pub fn create_meeting(&self, req: &NewMeeting) -> anyhow::Result<MeetingRecord> {
        let now = Utc::now();
        let record = MeetingRecord {
            id: Uuid::new_v4(),
            platform: req.key.platform,
            native_meeting_id: req.key.native_id.clone(),
            constructed_meeting_url: Some(req.key.url()),
            title: req.title.clone(),
            bot_name: req.bot_name.clone(),
            language: req.language.clone(),
            passcode: req.passcode.clone(),
            recording_enabled: req.recording_enabled,
            transcribe_enabled: req.transcribe_enabled,
            status: MeetingStatus::Requested,
            error: None,
            audio_path: None,
            start_time: None,
            end_time: None,
            created_at: now,
            updated_at: now,
        };

        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO meetings (
                 id, platform, native_meeting_id, constructed_meeting_url, title,
                 bot_name, language, passcode, recording_enabled, transcribe_enabled,
                 status, error, audio_path, start_time, end_time, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)",
            params![
                record.id.to_string(),
                record.platform.as_str(),
                record.native_meeting_id,
                record.constructed_meeting_url,
                record.title,
                record.bot_name,
                record.language,
                record.passcode,
                record.recording_enabled as i64,
                record.transcribe_enabled as i64,
                record.status.as_str(),
                Option::<String>::None,
                Option::<String>::None,
                Option::<String>::None,
                Option::<String>::None,
                fmt_ts(record.created_at),
                fmt_ts(record.updated_at),
            ],
        )
        .context("inserting meeting row")?;

        Ok(record)
    }

    /// Most recent row for the key, terminal or not. `None` => the API 404s
    /// (the client's zombie guard).
    pub fn latest_meeting(&self, key: &MeetingKey) -> anyhow::Result<Option<MeetingRecord>> {
        let sql = format!(
            "SELECT {MEETING_COLUMNS} FROM meetings
             WHERE platform = ?1 AND native_meeting_id = ?2
             ORDER BY created_at DESC, rowid DESC LIMIT 1"
        );
        let conn = self.conn()?;
        conn.query_row(&sql, params![key.platform.as_str(), key.native_id], |row| {
            row_to_meeting(row)
        })
        .optional()
        .context("selecting latest meeting for key")
    }

    pub fn get_meeting(&self, id: Uuid) -> anyhow::Result<Option<MeetingRecord>> {
        let sql = format!("SELECT {MEETING_COLUMNS} FROM meetings WHERE id = ?1");
        let conn = self.conn()?;
        conn.query_row(&sql, params![id.to_string()], row_to_meeting)
            .optional()
            .context("selecting meeting by id")
    }

    /// Most recent NON-terminal row for the key. Drives the 409 on `POST /bots`.
    pub fn active_meeting(&self, key: &MeetingKey) -> anyhow::Result<Option<MeetingRecord>> {
        let sql = format!(
            "SELECT {MEETING_COLUMNS} FROM meetings
             WHERE platform = ?1 AND native_meeting_id = ?2 AND {NON_TERMINAL_SQL}
             ORDER BY created_at DESC, rowid DESC LIMIT 1"
        );
        let conn = self.conn()?;
        conn.query_row(&sql, params![key.platform.as_str(), key.native_id], |row| {
            row_to_meeting(row)
        })
        .optional()
        .context("selecting active meeting for key")
    }

    /// Rows in a non-terminal status. Drives the 403 concurrency check.
    pub fn count_active(&self) -> anyhow::Result<usize> {
        let sql = format!("SELECT COUNT(*) FROM meetings WHERE {NON_TERMINAL_SQL}");
        let conn = self.conn()?;
        let n: i64 = conn
            .query_row(&sql, [], |row| row.get(0))
            .context("counting active meetings")?;
        Ok(n.max(0) as usize)
    }

    pub fn list_meetings(&self, limit: usize) -> anyhow::Result<Vec<MeetingRecord>> {
        let sql = format!(
            "SELECT {MEETING_COLUMNS} FROM meetings
             ORDER BY created_at DESC, rowid DESC LIMIT ?1"
        );
        let conn = self.conn()?;
        let mut stmt = conn.prepare(&sql).context("preparing list_meetings")?;
        let rows = stmt
            .query_map(params![limit as i64], row_to_meeting)
            .context("querying list_meetings")?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.context("decoding meeting row")?);
        }
        Ok(out)
    }

    /// Also bumps `updated_at`. `error` is only written when `Some` — passing
    /// `None` leaves any previously recorded error intact.
    pub fn set_status(
        &self,
        id: Uuid,
        status: MeetingStatus,
        error: Option<&str>,
    ) -> anyhow::Result<()> {
        let conn = self.conn()?;
        let changed = match error {
            Some(msg) => conn
                .execute(
                    "UPDATE meetings SET status = ?1, error = ?2, updated_at = ?3 WHERE id = ?4",
                    params![status.as_str(), msg, fmt_ts(Utc::now()), id.to_string()],
                )
                .context("updating meeting status with error")?,
            None => conn
                .execute(
                    "UPDATE meetings SET status = ?1, updated_at = ?2 WHERE id = ?3",
                    params![status.as_str(), fmt_ts(Utc::now()), id.to_string()],
                )
                .context("updating meeting status")?,
        };
        ensure_touched(changed, id, "set_status")
    }

    pub fn set_start_time(&self, id: Uuid, ts: DateTime<Utc>) -> anyhow::Result<()> {
        self.update_column("start_time", &fmt_ts(ts), id, "set_start_time")
    }

    pub fn set_end_time(&self, id: Uuid, ts: DateTime<Utc>) -> anyhow::Result<()> {
        self.update_column("end_time", &fmt_ts(ts), id, "set_end_time")
    }

    pub fn set_title(&self, id: Uuid, title: &str) -> anyhow::Result<()> {
        self.update_column("title", title, id, "set_title")
    }

    pub fn set_audio_path(&self, id: Uuid, path: &str) -> anyhow::Result<()> {
        self.update_column("audio_path", path, id, "set_audio_path")
    }

    /// Single-column update helper. `column` is only ever a literal from this
    /// module, never user input, so interpolating it is safe.
    fn update_column(&self, column: &str, value: &str, id: Uuid, what: &str) -> anyhow::Result<()> {
        let sql = format!("UPDATE meetings SET {column} = ?1, updated_at = ?2 WHERE id = ?3");
        let conn = self.conn()?;
        let changed = conn
            .execute(
                sql.as_str(),
                params![value, fmt_ts(Utc::now()), id.to_string()],
            )
            .with_context(|| format!("updating meetings.{column}"))?;
        ensure_touched(changed, id, what)
    }

    // ---------------------------------------------------------------- segments

    /// Blank/whitespace-only `text` is dropped here, not at the API layer
    /// (`SPEC.md` §1.5: the client counts non-blank segments to decide
    /// `skipped_not_admitted`). Returns the number actually inserted.
    pub fn insert_segments(&self, meeting_id: Uuid, segs: &[NewSegment]) -> anyhow::Result<usize> {
        if segs.is_empty() {
            return Ok(0);
        }
        let now = fmt_ts(Utc::now());
        let mid = meeting_id.to_string();

        let mut conn = self.conn()?;
        let tx = conn.transaction().context("opening segment transaction")?;
        let mut inserted = 0usize;
        {
            let mut stmt = tx
                .prepare(
                    "INSERT INTO segments
                         (meeting_id, start_time, end_time, speaker, text, language, created_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                )
                .context("preparing segment insert")?;
            for seg in segs {
                let text = seg.text.trim();
                if text.is_empty() {
                    continue;
                }
                stmt.execute(params![
                    mid,
                    seg.start_time,
                    seg.end_time,
                    seg.speaker,
                    text,
                    seg.language,
                    now,
                ])
                .context("inserting segment")?;
                inserted += 1;
            }
        }
        tx.commit().context("committing segment transaction")?;
        Ok(inserted)
    }

    /// Inserts one segment and returns its rowid. Blank text is rejected with
    /// an error rather than silently dropped, because the single-insert path is
    /// only ever reached from `whisper::run_transcriber`, which has already
    /// filtered noise transcripts.
    pub fn insert_segment(&self, meeting_id: Uuid, seg: &NewSegment) -> anyhow::Result<i64> {
        let text = seg.text.trim();
        if text.is_empty() {
            return Err(anyhow!(
                "refusing to insert a blank-text segment for meeting {meeting_id}"
            ));
        }
        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO segments
                 (meeting_id, start_time, end_time, speaker, text, language, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                meeting_id.to_string(),
                seg.start_time,
                seg.end_time,
                seg.speaker,
                text,
                seg.language,
                fmt_ts(Utc::now()),
            ],
        )
        .context("inserting segment")?;
        Ok(conn.last_insert_rowid())
    }

    /// Ordered by `start_time` ASC. Never returns blank-text rows.
    pub fn get_segments(&self, meeting_id: Uuid) -> anyhow::Result<Vec<Segment>> {
        let conn = self.conn()?;
        let mut stmt = conn
            .prepare(
                "SELECT id, meeting_id, start_time, end_time, speaker, text, language, created_at
                 FROM segments
                 WHERE meeting_id = ?1 AND TRIM(text) <> ''
                 ORDER BY start_time ASC, id ASC",
            )
            .context("preparing get_segments")?;
        let rows = stmt
            .query_map(params![meeting_id.to_string()], row_to_segment)
            .context("querying segments")?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.context("decoding segment row")?);
        }
        Ok(out)
    }

    pub fn count_segments(&self, meeting_id: Uuid) -> anyhow::Result<usize> {
        let conn = self.conn()?;
        let n: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM segments WHERE meeting_id = ?1 AND TRIM(text) <> ''",
                params![meeting_id.to_string()],
                |row| row.get(0),
            )
            .context("counting segments")?;
        Ok(n.max(0) as usize)
    }

    /// One round trip for `GET /transcripts/{platform}/{native_id}`: the most
    /// recent meeting row for the key plus its ordered, non-blank segments.
    ///
    /// `Ok(None)` is the 404 case — no row has ever existed for this key. Once
    /// `POST /bots` has returned 201 this must never be `None` again, which is
    /// why nothing in this module ever deletes a meeting row.
    ///
    /// `api.rs` turns the pair into its own `TranscriptResponse`; the response
    /// struct itself lives there (`SPEC.md` §9) and is deliberately not
    /// redefined here.
    pub fn transcript(
        &self,
        key: &MeetingKey,
    ) -> anyhow::Result<Option<(MeetingRecord, Vec<Segment>)>> {
        let Some(meeting) = self.latest_meeting(key)? else {
            return Ok(None);
        };
        let segments = self.get_segments(meeting.id)?;
        Ok(Some((meeting, segments)))
    }

    // ------------------------------------------------------------- maintenance

    /// Startup crash recovery: every non-terminal row created more than
    /// `older_than` ago becomes `Failed` with `error`. Returns rows touched.
    ///
    /// Pass `chrono::Duration::zero()` to sweep *every* live row, which is what
    /// acceptance test 9 in `SPEC.md` §10 requires after a hard restart — a row
    /// left non-terminal across a restart is polled by the client forever.
    pub fn sweep_stale(&self, older_than: chrono::Duration, error: &str) -> anyhow::Result<usize> {
        let now = Utc::now();
        let cutoff = now
            .checked_sub_signed(older_than)
            .ok_or_else(|| anyhow!("sweep_stale: cutoff timestamp overflowed"))?;
        let sql = format!(
            "UPDATE meetings
                SET status = 'failed', error = ?1, end_time = COALESCE(end_time, ?2), updated_at = ?2
              WHERE {NON_TERMINAL_SQL} AND created_at <= ?3"
        );
        let conn = self.conn()?;
        let changed = conn
            .execute(sql.as_str(), params![error, fmt_ts(now), fmt_ts(cutoff)])
            .context("sweeping stale meetings")?;
        Ok(changed)
    }
}

// ------------------------------------------------------------------- helpers

/// RFC3339 UTC, second precision, trailing `Z` — the only timestamp format
/// this database stores (`SPEC.md` §1.5).
fn fmt_ts(ts: DateTime<Utc>) -> String {
    ts.to_rfc3339_opts(SecondsFormat::Secs, true)
}

fn parse_ts(raw: &str) -> anyhow::Result<DateTime<Utc>> {
    Ok(DateTime::parse_from_rfc3339(raw)
        .with_context(|| format!("parsing stored timestamp {raw:?}"))?
        .with_timezone(&Utc))
}

fn ensure_touched(changed: usize, id: Uuid, what: &str) -> anyhow::Result<()> {
    if changed == 0 {
        Err(anyhow!("{what}: no meeting row with id {id}"))
    } else {
        Ok(())
    }
}

/// Turns a decode failure into a rusqlite error so it can travel out of a row
/// mapper closure.
fn decode_err(index: usize, msg: String) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        index,
        rusqlite::types::Type::Text,
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidData, msg)),
    )
}

fn row_to_meeting(row: &Row<'_>) -> rusqlite::Result<MeetingRecord> {
    let id_raw: String = row.get(0)?;
    let id = Uuid::parse_str(&id_raw)
        .map_err(|e| decode_err(0, format!("meetings.id {id_raw:?} is not a uuid: {e}")))?;

    let platform_raw: String = row.get(1)?;
    let platform = Platform::parse(&platform_raw)
        .ok_or_else(|| decode_err(1, format!("meetings.platform {platform_raw:?} is unknown")))?;

    let status_raw: String = row.get(10)?;
    let status = MeetingStatus::parse(&status_raw)
        .ok_or_else(|| decode_err(10, format!("meetings.status {status_raw:?} is unknown")))?;

    let start_time = opt_ts(row, 13)?;
    let end_time = opt_ts(row, 14)?;
    let created_raw: String = row.get(15)?;
    let updated_raw: String = row.get(16)?;

    Ok(MeetingRecord {
        id,
        platform,
        native_meeting_id: row.get(2)?,
        constructed_meeting_url: row.get(3)?,
        title: row.get(4)?,
        bot_name: row.get(5)?,
        language: row.get(6)?,
        passcode: row.get(7)?,
        recording_enabled: row.get::<_, i64>(8)? != 0,
        transcribe_enabled: row.get::<_, i64>(9)? != 0,
        status,
        error: row.get(11)?,
        audio_path: row.get(12)?,
        start_time,
        end_time,
        created_at: parse_ts(&created_raw)
            .map_err(|e| decode_err(15, format!("meetings.created_at: {e}")))?,
        updated_at: parse_ts(&updated_raw)
            .map_err(|e| decode_err(16, format!("meetings.updated_at: {e}")))?,
    })
}

fn opt_ts(row: &Row<'_>, index: usize) -> rusqlite::Result<Option<DateTime<Utc>>> {
    match row.get::<_, Option<String>>(index)? {
        None => Ok(None),
        Some(raw) => parse_ts(&raw)
            .map(Some)
            .map_err(|e| decode_err(index, e.to_string())),
    }
}

fn row_to_segment(row: &Row<'_>) -> rusqlite::Result<Segment> {
    let meeting_raw: String = row.get(1)?;
    let meeting_id = Uuid::parse_str(&meeting_raw).map_err(|e| {
        decode_err(
            1,
            format!("segments.meeting_id {meeting_raw:?} is not a uuid: {e}"),
        )
    })?;
    let created_raw: String = row.get(7)?;

    Ok(Segment {
        id: row.get(0)?,
        meeting_id,
        start_time: row.get(2)?,
        end_time: row.get(3)?,
        speaker: row.get(4)?,
        text: row.get(5)?,
        language: row.get(6)?,
        created_at: parse_ts(&created_raw)
            .map_err(|e| decode_err(7, format!("segments.created_at: {e}")))?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key() -> MeetingKey {
        MeetingKey::new(Platform::GoogleMeet, "bqy-ybgi-pbb")
    }

    fn new_meeting() -> NewMeeting {
        NewMeeting {
            key: key(),
            title: None,
            bot_name: "Notetaker".to_string(),
            language: Some("en".to_string()),
            passcode: None,
            recording_enabled: false,
            transcribe_enabled: true,
        }
    }

    fn seg(start: f64, text: &str) -> NewSegment {
        NewSegment {
            start_time: start,
            end_time: start + 1.0,
            speaker: Some("YourManager Marchesi".to_string()),
            text: text.to_string(),
            language: Some("en".to_string()),
        }
    }

    #[test]
    fn terminal_set_matches_the_python_client_tuple() {
        for s in [
            MeetingStatus::Completed,
            MeetingStatus::Failed,
            MeetingStatus::Stopped,
        ] {
            assert!(s.is_terminal(), "{s} must be terminal");
        }
        for s in [
            MeetingStatus::Requested,
            MeetingStatus::Joining,
            MeetingStatus::AwaitingAdmission,
            MeetingStatus::Active,
            MeetingStatus::Finalizing,
        ] {
            assert!(!s.is_terminal(), "{s} must not be terminal");
        }
    }

    #[test]
    fn status_string_roundtrip() {
        for s in [
            MeetingStatus::Requested,
            MeetingStatus::Joining,
            MeetingStatus::AwaitingAdmission,
            MeetingStatus::Active,
            MeetingStatus::Finalizing,
            MeetingStatus::Completed,
            MeetingStatus::Failed,
            MeetingStatus::Stopped,
        ] {
            assert_eq!(MeetingStatus::parse(s.as_str()), Some(s));
        }
        assert_eq!(MeetingStatus::parse("bogus"), None);
    }

    #[test]
    fn create_then_read_back() {
        let db = Db::open_in_memory().unwrap();
        let rec = db.create_meeting(&new_meeting()).unwrap();

        assert_eq!(rec.status, MeetingStatus::Requested);
        assert!(rec.start_time.is_none());
        assert_eq!(
            rec.constructed_meeting_url.as_deref(),
            Some("https://meet.google.com/bqy-ybgi-pbb")
        );

        let fetched = db.get_meeting(rec.id).unwrap().unwrap();
        assert_eq!(fetched.id, rec.id);
        assert_eq!(fetched.native_meeting_id, "bqy-ybgi-pbb");
        assert!(fetched.transcribe_enabled);
        assert!(!fetched.recording_enabled);
    }

    #[test]
    fn unknown_key_has_no_latest_meeting() {
        let db = Db::open_in_memory().unwrap();
        let missing = MeetingKey::new(Platform::GoogleMeet, "aaa-bbbb-ccc");
        assert!(db.latest_meeting(&missing).unwrap().is_none());
    }

    #[test]
    fn active_and_count_track_terminality() {
        let db = Db::open_in_memory().unwrap();
        let rec = db.create_meeting(&new_meeting()).unwrap();

        assert!(db.active_meeting(&key()).unwrap().is_some());
        assert_eq!(db.count_active().unwrap(), 1);

        db.set_status(rec.id, MeetingStatus::Completed, None)
            .unwrap();

        assert!(db.active_meeting(&key()).unwrap().is_none());
        assert_eq!(db.count_active().unwrap(), 0);
        // The row is permanent: the zombie guard must never see a 404 again.
        assert!(db.latest_meeting(&key()).unwrap().is_some());
    }

    #[test]
    fn latest_meeting_returns_the_newest_row() {
        let db = Db::open_in_memory().unwrap();
        let first = db.create_meeting(&new_meeting()).unwrap();
        db.set_status(first.id, MeetingStatus::Completed, None)
            .unwrap();
        let second = db.create_meeting(&new_meeting()).unwrap();

        assert_eq!(db.latest_meeting(&key()).unwrap().unwrap().id, second.id);
    }

    #[test]
    fn blank_segments_are_dropped_on_insert_and_on_read() {
        let db = Db::open_in_memory().unwrap();
        let rec = db.create_meeting(&new_meeting()).unwrap();

        let inserted = db
            .insert_segments(
                rec.id,
                &[
                    seg(2.0, "second"),
                    seg(1.0, "first"),
                    seg(3.0, "   "),
                    seg(4.0, ""),
                ],
            )
            .unwrap();
        assert_eq!(inserted, 2);

        let segments = db.get_segments(rec.id).unwrap();
        assert_eq!(segments.len(), 2);
        assert_eq!(segments[0].text, "first");
        assert_eq!(segments[1].text, "second");
        assert_eq!(segments[0].start_time, 1.0);
        assert_eq!(db.count_segments(rec.id).unwrap(), 2);

        assert!(db.insert_segment(rec.id, &seg(9.0, "  ")).is_err());
        assert!(db.insert_segment(rec.id, &seg(9.0, "real")).unwrap() > 0);
    }

    #[test]
    fn transcript_assembles_record_plus_ordered_segments() {
        let db = Db::open_in_memory().unwrap();
        let rec = db.create_meeting(&new_meeting()).unwrap();
        db.set_start_time(rec.id, Utc::now()).unwrap();
        db.insert_segments(rec.id, &[seg(17.4, "b"), seg(12.48, "a")])
            .unwrap();
        db.set_end_time(rec.id, Utc::now()).unwrap();
        db.set_status(rec.id, MeetingStatus::Completed, None)
            .unwrap();

        let (meeting, segments) = db.transcript(&key()).unwrap().unwrap();
        assert_eq!(meeting.status, MeetingStatus::Completed);
        assert!(meeting.start_time.is_some());
        assert!(meeting.end_time.is_some());
        assert_eq!(segments.len(), 2);
        assert!(segments[0].start_time < segments[1].start_time);

        let missing = MeetingKey::new(Platform::GoogleMeet, "zzz-zzzz-zzz");
        assert!(db.transcript(&missing).unwrap().is_none());
    }

    #[test]
    fn never_admitted_meeting_is_completed_with_no_segments() {
        // SPEC.md §0.1: the load-bearing semantic.
        let db = Db::open_in_memory().unwrap();
        let rec = db.create_meeting(&new_meeting()).unwrap();
        db.set_status(rec.id, MeetingStatus::AwaitingAdmission, None)
            .unwrap();
        db.set_end_time(rec.id, Utc::now()).unwrap();
        db.set_status(rec.id, MeetingStatus::Completed, None)
            .unwrap();

        let (meeting, segments) = db.transcript(&key()).unwrap().unwrap();
        assert_eq!(meeting.status, MeetingStatus::Completed);
        assert!(meeting.start_time.is_none());
        assert!(segments.is_empty());
    }

    #[test]
    fn set_status_preserves_an_existing_error_when_none_is_passed() {
        let db = Db::open_in_memory().unwrap();
        let rec = db.create_meeting(&new_meeting()).unwrap();
        db.set_status(rec.id, MeetingStatus::Failed, Some("page detached"))
            .unwrap();
        db.set_status(rec.id, MeetingStatus::Failed, None).unwrap();

        let fetched = db.get_meeting(rec.id).unwrap().unwrap();
        assert_eq!(fetched.error.as_deref(), Some("page detached"));
    }

    #[test]
    fn setters_reject_an_unknown_meeting_id() {
        let db = Db::open_in_memory().unwrap();
        let ghost = Uuid::new_v4();
        assert!(db.set_status(ghost, MeetingStatus::Completed, None).is_err());
        assert!(db.set_start_time(ghost, Utc::now()).is_err());
        assert!(db.set_title(ghost, "x").is_err());
        assert!(db.set_audio_path(ghost, "x.wav").is_err());
    }

    #[test]
    fn sweep_stale_fails_live_rows_and_leaves_terminal_ones_alone() {
        let db = Db::open_in_memory().unwrap();
        let live = db.create_meeting(&new_meeting()).unwrap();
        let done = db
            .create_meeting(&NewMeeting {
                key: MeetingKey::new(Platform::Teams, "1234567890"),
                ..new_meeting()
            })
            .unwrap();
        db.set_status(done.id, MeetingStatus::Completed, None)
            .unwrap();

        // A six-hour cutoff must not touch a row created a moment ago.
        assert_eq!(
            db.sweep_stale(chrono::Duration::hours(6), "server restarted")
                .unwrap(),
            0
        );

        let touched = db
            .sweep_stale(
                chrono::Duration::zero(),
                "server restarted while session was live",
            )
            .unwrap();
        assert_eq!(touched, 1);

        let swept = db.get_meeting(live.id).unwrap().unwrap();
        assert_eq!(swept.status, MeetingStatus::Failed);
        assert_eq!(
            swept.error.as_deref(),
            Some("server restarted while session was live")
        );
        assert!(swept.end_time.is_some());
        assert_eq!(
            db.get_meeting(done.id).unwrap().unwrap().status,
            MeetingStatus::Completed
        );
        assert_eq!(db.count_active().unwrap(), 0);
    }

    #[test]
    fn list_meetings_is_newest_first_and_honours_the_limit() {
        let db = Db::open_in_memory().unwrap();
        db.create_meeting(&new_meeting()).unwrap();
        db.create_meeting(&new_meeting()).unwrap();
        let third = db.create_meeting(&new_meeting()).unwrap();

        let all = db.list_meetings(10).unwrap();
        assert_eq!(all.len(), 3);
        assert_eq!(all[0].id, third.id);
        assert_eq!(db.list_meetings(2).unwrap().len(), 2);
    }

    #[test]
    fn timestamps_roundtrip_as_utc_with_a_trailing_z() {
        let now = Utc::now();
        let text = fmt_ts(now);
        assert!(text.ends_with('Z'), "{text} must end with Z");
        assert!(!text.contains('+'), "{text} must not carry an offset");
        assert_eq!(parse_ts(&text).unwrap().timestamp(), now.timestamp());
    }

    #[test]
    fn db_handle_is_shareable_across_threads() {
        let db = Db::open_in_memory().unwrap();
        let rec = db.create_meeting(&new_meeting()).unwrap();
        let handles: Vec<_> = (0..4)
            .map(|i| {
                let db = db.clone();
                let id = rec.id;
                std::thread::spawn(move || {
                    db.insert_segment(id, &seg(i as f64, "concurrent")).unwrap()
                })
            })
            .collect();
        for h in handles {
            h.join().unwrap();
        }
        assert_eq!(db.count_segments(rec.id).unwrap(), 4);
    }
}
