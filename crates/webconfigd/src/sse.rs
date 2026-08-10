//! SSE endpoint (spec §3: one SSE endpoint for log follow + dashboard push;
//! spec §4.6 follow mode via SSE).
//!
//! Each tick emits two events: a `vitals` push (spec §4.5) and a `journal`
//! follow chunk (spec §4.6). The journal follow is **cursor-based**: the first
//! tick seeds from the last ~50 lines and captures the journal cursor; every
//! later tick emits only the lines **after** that cursor and advances it — so a
//! connected browser sees new log lines as they arrive without re-receiving
//! history. Assumption (per spec §7, one line): this is a polled follow
//! (every `JOURNAL_TICK_SECS`), not a blocking `journalctl -f` stream, because
//! the daemon runs on a current-thread executor and a per-connection child
//! process would be costly; the cursor makes it a *true* follow (no dupes, no
//! gaps) within the poll interval. On the fake sysroot (no journal) only the
//! `vitals` tick is emitted.

use axum::extract::State;
use axum::response::sse::{Event, KeepAlive, Sse};
use futures_util::stream::{self, Stream, StreamExt};
use std::convert::Infallible;
use std::time::Duration;
use tokio::sync::OwnedSemaphorePermit;

use crate::auth::AuthRole;
use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// How often the journal follow advances (spec §4.6: "every few seconds").
const JOURNAL_TICK_SECS: u64 = 2;
/// Initial back-fill when a client first connects (spec §4.6 last-N paging).
const JOURNAL_SEED_LINES: &str = "50";

/// Per-connection follow state: the journal cursor from the last emit, so the
/// next tick asks only for lines after it.
type Cursor = Option<String>;

/// `GET /api/v1/events` — Server-Sent Events: periodic vitals push + (on the
/// real root) journal follow. The browser subscribes while the Logs/Monitoring
/// pages are open.
pub async fn events(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Sse<impl Stream<Item = Result<Event, Infallible>>>> {
    // Cap concurrent streams: each connection polls `journalctl` on its own
    // tick, which would otherwise let unbounded simultaneous connections
    // exhaust the current-thread runtime and RAM budget. The permit is held
    // in the unfold state below so it's released exactly when the client
    // disconnects and the stream is dropped — no separate bookkeeping.
    let permit = app
        .sse_connections
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            ApiError::NotReady("too many active event streams; try again shortly".into())
        })?;

    let sys = app.engine.sysroot.clone();
    let stream = stream::unfold(
        (None as Cursor, permit),
        move |(cursor, permit): (Cursor, OwnedSemaphorePermit)| {
            let sys = sys.clone();
            async move {
                tokio::time::sleep(Duration::from_secs(JOURNAL_TICK_SECS)).await;

                let mut events: Vec<Event> = Vec::new();

                // Vitals push (spec §4.5).
                let vitals_payload = if sys.is_real_root() {
                    serde_json::to_string(&webconfig_engine::monitoring::read_vitals(&sys))
                        .unwrap_or_default()
                } else {
                    serde_json::json!({ "tick": 0 }).to_string()
                };
                events.push(Event::default().event("vitals").data(vitals_payload));

                // Journal follow (spec §4.6). Real root only; advances the cursor.
                let mut next_cursor = cursor;
                if sys.is_real_root() {
                    let (lines, new_cursor) = journal_follow(&sys, next_cursor);
                    if !lines.is_empty() {
                        let payload = serde_json::to_string(&lines).unwrap_or_else(|_| "[]".into());
                        events.push(Event::default().event("journal").data(payload));
                    }
                    next_cursor = new_cursor;
                }

                // Flatten the per-tick events into the SSE stream.
                let item_stream = stream::iter(events.into_iter().map(Ok::<_, Infallible>));
                Some((item_stream, (next_cursor, permit)))
            }
        },
    )
    .flatten();

    Ok(Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))))
}

/// Read journal lines for the follow tick. When `cursor` is `None`, seed from
/// the last `JOURNAL_SEED_LINES` lines and capture the cursor. Otherwise emit
/// only lines after the cursor and advance it. Returns `(lines, new_cursor)`.
fn journal_follow(_sys: &webconfig_engine::Sysroot, cursor: Cursor) -> (Vec<String>, Cursor) {
    let mut args: Vec<String> = vec!["--no-pager".into(), "--output=json".into()];
    match &cursor {
        Some(c) => {
            args.push("--after-cursor".into());
            args.push(c.clone());
        }
        None => {
            args.push("-n".into());
            args.push(JOURNAL_SEED_LINES.into());
        }
    }
    // --show-cursor appends a `-- cursor: <…>` line we parse out to advance.
    args.push("--show-cursor".into());
    let refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let Ok(out) = webconfig_engine::applier::run_cmd("journalctl", &refs) else {
        return (Vec::new(), cursor);
    };
    parse_journal_follow(&out)
}

/// Split `journalctl -o json --show-cursor` output into the JSON event lines
/// and the trailing cursor line. JSON lines are returned verbatim (the frontend
/// parses each); the cursor is extracted from the `-- cursor:` sentinel.
fn parse_journal_follow(out: &str) -> (Vec<String>, Cursor) {
    let mut lines = Vec::new();
    let mut new_cursor: Cursor = None;
    for line in out.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("-- cursor:") {
            new_cursor = Some(rest.trim().to_string());
        } else if !line.is_empty() {
            lines.push(line.to_string());
        }
    }
    (lines, new_cursor)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_json_lines_and_cursor() {
        let out = "\
{\"MESSAGE\":\"hello\",\"__CURSOR\":\"s=abc\"}
{\"MESSAGE\":\"world\",\"__CURSOR\":\"s=def\"}
-- cursor: s=def
";
        let (lines, cur) = parse_journal_follow(out);
        assert_eq!(lines.len(), 2);
        assert!(lines[0].contains("hello"));
        assert_eq!(cur.as_deref(), Some("s=def"));
    }

    #[test]
    fn empty_journal_yields_no_lines_and_no_cursor() {
        let (lines, cur) = parse_journal_follow("");
        assert!(lines.is_empty());
        assert!(cur.is_none());
    }

    #[test]
    fn cursor_only_output_advances_cursor_without_lines() {
        // --after-cursor with no new entries: just the cursor sentinel.
        let out = "-- cursor: s=def\n";
        let (lines, cur) = parse_journal_follow(out);
        assert!(lines.is_empty());
        assert_eq!(cur.as_deref(), Some("s=def"));
    }
}
