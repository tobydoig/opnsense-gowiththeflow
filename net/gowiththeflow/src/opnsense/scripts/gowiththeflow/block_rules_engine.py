"""Reconciles `block_rules` against actual enforcement -- pf (via
blocklist.py, reused verbatim) for rule_type='host', Unbound DNSBL (via a
small PHP CLI script, since Unbound's config is PHP-model-owned) for
rule_type='domain'. `block_rules` is the one source of truth; this module
only ever derives enforcement state FROM it, the same "DB is truth,
pf/Unbound state is derived" discipline blocklist.py already established
for blocked_hosts/the pf table.

Decision logic (resolve_rule_state) is pure and separated from the actual
pf/PHP effects, mirroring blocklist.py's own split -- fully unit-testable
without a VM.
"""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime

import block_schedule
import blocklist

try:
    import syslog
except ImportError:  # syslog is POSIX-only -- this module's own tests run on Windows
    syslog = None

DB_PATH = "/var/db/gowiththeflow/flows.db"
TABLE_FILE_PATH = "/var/db/gowiththeflow/blocked_hosts.tbl"
PHP_BIN = "/usr/local/bin/php"
DNSBL_APPLY_SCRIPT = "/usr/local/opnsense/scripts/gowiththeflow/dnsbl_apply.php"


def _log_error(message: str) -> None:
    if syslog is not None:
        syslog.syslog(syslog.LOG_ERR, message)


# --- CRUD -------------------------------------------------------------
# Pure DB operations, no validation -- callers (block_host.py's existing
# cmd_block, and block_rules.py's own CLI) validate the IP/guards first,
# matching blocklist.add_block()'s own established split.

def list_rules(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM block_rules ORDER BY created_at DESC").fetchall()


def get_rule(conn: sqlite3.Connection, rule_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM block_rules WHERE id = ?", (rule_id,)).fetchone()


def create_host_rule(
    conn: sqlite3.Connection, ip: str, hostname: str | None, mac: str | None,
    created_by: str | None, reason: str | None, now: int,
) -> int:
    """Upserts this device's host-type rule -- only one is ever allowed
    per IP (the partial unique index enforces it). Called both by
    block_rules.py's own `create` command and by block_host.py's existing
    cmd_block, so the pre-existing Live/Top-Talkers quick-block icon and
    this unified table stay in lockstep automatically, with no separate
    migration step."""
    conn.execute(
        """
        INSERT INTO block_rules
            (rule_type, local_ip, mac, hostname, domains, schedule_json, enabled,
             created_at, created_by, reason, updated_at)
        VALUES ('host', ?, ?, ?, NULL, NULL, 1, ?, ?, ?, ?)
        ON CONFLICT(local_ip) WHERE rule_type = 'host' DO UPDATE SET
            mac = excluded.mac, hostname = excluded.hostname,
            created_by = excluded.created_by, reason = excluded.reason,
            updated_at = excluded.updated_at
        """,
        (ip, mac, hostname, now, created_by, reason, now),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM block_rules WHERE rule_type = 'host' AND local_ip = ?", (ip,)).fetchone()
    return row["id"]


def create_domain_rule(
    conn: sqlite3.Connection, ip: str, hostname: str | None, mac: str | None,
    domains: str, schedule_json: str | None, created_by: str | None, reason: str | None, now: int,
) -> int:
    """Always inserts a new row -- unlike host rules, a device can have
    several independent domain rules at once (different domains,
    different schedules)."""
    cur = conn.execute(
        """
        INSERT INTO block_rules
            (rule_type, local_ip, mac, hostname, domains, schedule_json, enabled,
             created_at, created_by, reason, updated_at)
        VALUES ('domain', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (ip, mac, hostname, domains, schedule_json, now, created_by, reason, now),
    )
    conn.commit()
    rule_id = cur.lastrowid
    conn.execute(
        "UPDATE block_rules SET unbound_description = ? WHERE id = ?",
        (f"gowiththeflow:rule:{rule_id}", rule_id),
    )
    conn.commit()
    return rule_id


def update_rule(
    conn: sqlite3.Connection, rule_id: int, domains: str | None, schedule_json: str | None, now: int,
) -> bool:
    """Edits a rule's domains/schedule (host rules never have either) and
    re-applies it immediately so a change takes effect right away rather
    than waiting for the next reconcile tick."""
    if get_rule(conn, rule_id) is None:
        return False
    conn.execute(
        "UPDATE block_rules SET domains = ?, schedule_json = ?, updated_at = ? WHERE id = ?",
        (domains, schedule_json, now, rule_id),
    )
    conn.commit()
    apply_rule(conn, rule_id, now)
    return True


def set_enabled(conn: sqlite3.Connection, rule_id: int, enabled: bool, now: int) -> bool:
    row = get_rule(conn, rule_id)
    if row is None:
        return False
    conn.execute(
        "UPDATE block_rules SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, now, rule_id),
    )
    conn.commit()
    if enabled:
        apply_rule(conn, rule_id, now)
    else:
        # Unwind enforcement immediately -- a disabled rule must not stay
        # silently blocked until the next reconcile tick notices it's off.
        if row["rule_type"] == "host":
            _apply_host_rule(conn, row, False, now)
        else:
            _apply_domain_rule(row, False)
    return True


def delete_rule(conn: sqlite3.Connection, rule_id: int, now: int) -> bool:
    row = get_rule(conn, rule_id)
    if row is None:
        return False
    if row["rule_type"] == "host":
        _apply_host_rule(conn, row, False, now)
    else:
        _remove_domain_rule(row)
    conn.execute("DELETE FROM block_rules WHERE id = ?", (rule_id,))
    conn.commit()
    return True


def set_override(conn: sqlite3.Connection, rule_id: int, state: str, now: int) -> dict:
    """Sets a temporary manual override on a SCHEDULED rule, lasting until
    the current schedule segment ends (block_schedule.current_segment_end())
    -- a real, decided behavior: unblocking mid-window holds until that
    window ends, then the schedule resumes normally next time. Not
    meaningful for an "always" (schedule-less) rule -- those are toggled
    by creating/deleting the rule itself, exactly like block-a-host
    already worked before this feature existed."""
    if state not in ("blocked", "unblocked"):
        return {"status": "error", "error": f"invalid override state: {state!r}"}
    row = get_rule(conn, rule_id)
    if row is None:
        return {"status": "error", "error": "no such rule"}
    if row["schedule_json"] is None:
        return {"status": "error", "error": "this rule has no schedule -- block/unblock it directly instead"}

    schedule = block_schedule.parse_schedule(row["schedule_json"])
    segment_end = block_schedule.current_segment_end(schedule, datetime.fromtimestamp(now))
    override_until = int(segment_end.timestamp()) if segment_end is not None else None

    conn.execute(
        "UPDATE block_rules SET manual_override_state = ?, override_until = ?, updated_at = ? WHERE id = ?",
        (state, override_until, now, rule_id),
    )
    conn.commit()
    apply_rule(conn, rule_id, now)
    return {"status": "ok", "override_until": override_until}


# --- reconcile engine ---------------------------------------------------

@dataclass(frozen=True)
class RuleDecision:
    rule_id: int
    rule_type: str
    should_be_blocked: bool
    clear_override: bool  # an expired override_until should be cleared this pass


def resolve_rule_state(row: sqlite3.Row, now: int) -> RuleDecision:
    """Pure: decides whether one block_rules row should currently be
    enforced as blocked. Touches nothing -- no DB write, no pf, no PHP."""
    override_until = row["override_until"]
    clear_override = override_until is not None and now >= override_until
    override_active = override_until is not None and not clear_override

    if row["schedule_json"] is None:
        # "Always" -- exactly today's pre-schedule-feature behavior.
        # Unblocking an always-rule means disabling/deleting it (handled
        # by block_rules.py, outside reconcile), not an override, so an
        # enabled always-rule is simply always meant to be blocked.
        should_be_blocked = True
    else:
        schedule = block_schedule.parse_schedule(row["schedule_json"])
        now_local = datetime.fromtimestamp(now)
        scheduled = block_schedule.is_blocked_now(schedule, now_local)
        should_be_blocked = (row["manual_override_state"] == "blocked") if override_active else scheduled

    return RuleDecision(row["id"], row["rule_type"], should_be_blocked, clear_override)


def _is_host_blocked(conn: sqlite3.Connection, ip: str) -> bool:
    return conn.execute("SELECT 1 FROM blocked_hosts WHERE local_ip = ?", (ip,)).fetchone() is not None


def _apply_host_rule(conn: sqlite3.Connection, row: sqlite3.Row, should_be_blocked: bool, now: int) -> None:
    ip = row["local_ip"]
    currently_blocked = _is_host_blocked(conn, ip)
    if should_be_blocked and not currently_blocked:
        blocklist.add_block(conn, ip, row["hostname"], row["mac"], row["created_by"], row["reason"], now)
        blocklist.sync_pf(conn, TABLE_FILE_PATH)
        blocklist.kill_states(ip)
    elif not should_be_blocked and currently_blocked:
        blocklist.remove_block(conn, ip)
        blocklist.sync_pf(conn, TABLE_FILE_PATH)


def _run_dnsbl_apply(action: str, row: sqlite3.Row) -> None:
    result = subprocess.run(
        [
            PHP_BIN, "-f", DNSBL_APPLY_SCRIPT, "--",
            "--action", action,
            "--description", row["unbound_description"],
            "--domains", row["domains"] or "",
            "--source-ip", row["local_ip"],
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        _log_error(
            "gowiththeflow: dnsbl_apply.php %s failed for rule %s: %s"
            % (action, row["id"], (result.stderr or result.stdout).strip()[:500])
        )


def _apply_domain_rule(row: sqlite3.Row, should_be_blocked: bool) -> None:
    """A schedule-driven on/off toggle -- the Unbound row itself persists
    (just its `enabled` flag flips) so re-enabling it moments later isn't
    a full add/remove cycle. See _remove_domain_rule() for the distinct
    "this rule is being deleted, actually remove the row" case."""
    _run_dnsbl_apply("enable" if should_be_blocked else "disable", row)


def _remove_domain_rule(row: sqlite3.Row) -> None:
    """Used only when the block_rules row itself is being deleted -- unlike
    a routine schedule-driven disable, this actually removes this
    plugin's row from Unbound's dnsbl.blocklist rather than leaving a
    disabled one behind forever."""
    _run_dnsbl_apply("remove", row)


def apply_rule(conn: sqlite3.Connection, rule_id: int, now: int) -> RuleDecision | None:
    """Reconciles exactly one rule -- used both by reconcile_all()'s loop
    and by block_rules.py's CLI so a manual create/edit/override takes
    effect immediately rather than waiting for the next periodic tick.
    Returns None if the rule no longer exists or isn't enabled."""
    row = conn.execute("SELECT * FROM block_rules WHERE id = ? AND enabled = 1", (rule_id,)).fetchone()
    if row is None:
        return None

    decision = resolve_rule_state(row, now)

    if decision.clear_override:
        conn.execute(
            "UPDATE block_rules SET manual_override_state = NULL, override_until = NULL WHERE id = ?",
            (decision.rule_id,),
        )
        conn.commit()

    if decision.rule_type == "host":
        _apply_host_rule(conn, row, decision.should_be_blocked, now)
    else:
        _apply_domain_rule(row, decision.should_be_blocked)

    conn.execute(
        "UPDATE block_rules SET last_effective_state = ?, last_evaluated_at = ? WHERE id = ?",
        ("blocked" if decision.should_be_blocked else "unblocked", now, decision.rule_id),
    )
    conn.commit()
    return decision


def reconcile_all(conn: sqlite3.Connection, now: int) -> list[RuleDecision]:
    """Reconciles every enabled rule. A single rule's own bad data (e.g. a
    malformed schedule_json) is caught and logged rather than aborting the
    whole pass -- or worse, the whole daemon, since nothing upstream of
    this call catches exceptions either (a real, hard-learned lesson from
    the DNS sniffer's own silent-thread-death bug this same project just
    fixed: one bad record must never be able to take down everything else
    sharing its loop)."""
    ids = [row["id"] for row in conn.execute("SELECT id FROM block_rules WHERE enabled = 1").fetchall()]
    decisions = []
    for rule_id in ids:
        try:
            decision = apply_rule(conn, rule_id, now)
        except Exception as e:
            _log_error("gowiththeflow: reconcile failed for block_rules.id=%s: %r" % (rule_id, e))
            continue
        if decision is not None:
            decisions.append(decision)
    return decisions
