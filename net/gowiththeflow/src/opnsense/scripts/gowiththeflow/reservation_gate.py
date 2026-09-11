"""Enforcement side of the "only known/reserved devices may reach the
WAN" feature -- an allowlist, not a blocklist, so its failure posture is
the opposite of blocklist.py's: where a failed block-sync leaves a host
merely unblocked (annoying, not dangerous), a failed *allowlist* sync
must never be allowed to silently look like "there are now zero
reserved devices," or a transient read failure would cut every device
on the network off the WAN at once. Every function here is written
around that one asymmetry.

The pf table (`gowiththeflow_allowed`) and the pass/pass/pass/block rule
set referencing it are declared by `etc/inc/plugins.inc.d/gowiththeflow.inc`,
gated on the plugin's own `enableReservationGate` setting -- when that's
off, nothing is even registered into the live ruleset (not "registered
but harmless"). This module's job is only ever to keep the pf table's
backing file and the kernel's static ARP entries in sync with whatever
OPNsense's Dnsmasq plugin currently says is reserved -- read via
dnsmasq_reservations.php (Python has no other way to reach a PHP-model-
owned config, same reasoning as dnsbl_apply.php for Unbound).

ARP pinning (arp -S) is a SEPARATE, independently-toggled hardening
layer on top of the pf allowlist -- it stops a device from simply
claiming a reserved IP via static config, but is not required for the
pf-level enforcement to work at all. `arp_pins` (db.py) is this
module's own record of which pins it has applied, so the periodic
reconcile's diff never needs to parse `arp -an` output to guess which
entries are "ours" (unlike local_host_identity's own read-only `arp -an`
parsing of the WHOLE table).
"""

from __future__ import annotations

import ipaddress
import json
import sqlite3
import subprocess

import blocklist

try:
    import syslog
except ImportError:  # syslog is POSIX-only -- this module's own tests run on Windows
    syslog = None

PF_ALLOWED_TABLE = "gowiththeflow_allowed"
PF_PROTECTED_TABLE = "gowiththeflow_protected_dests"
ALLOWED_TABLE_FILE = "/var/db/gowiththeflow/allowed_hosts.tbl"
PROTECTED_TABLE_FILE = "/var/db/gowiththeflow/protected_dests.tbl"
PHP_BIN = "/usr/local/bin/php"
DNSMASQ_RESERVATIONS_SCRIPT = "/usr/local/opnsense/scripts/gowiththeflow/dnsmasq_reservations.php"
ARP_BIN = "/usr/sbin/arp"


def _log_error(message: str) -> None:
    if syslog is not None:
        syslog.syslog(syslog.LOG_ERR, message)


def fetch_reservations() -> list[tuple[str, str]] | None:
    """Runs dnsmasq_reservations.php and returns [(mac, ip), ...], or
    None on ANY failure (non-zero exit, timeout, unparseable JSON).
    None is a deliberate, distinct sentinel from "[]" (zero reservations
    genuinely exist) -- callers must never treat a failed read the same
    as an empty allowlist, or a transient PHP/config-lock hiccup would
    spuriously cut every device off the WAN. timeout=15, matching this
    project's post-pfctl-freeze-incident convention for every subprocess
    call that could otherwise hang the daemon solid."""
    try:
        result = subprocess.run(
            [PHP_BIN, "-f", DNSMASQ_RESERVATIONS_SCRIPT],
            capture_output=True, text=True, check=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        _log_error("gowiththeflow: dnsmasq_reservations.php failed: %r" % (e,))
        return None
    try:
        rows = json.loads(result.stdout)
        return [(str(row["mac"]), str(row["ip"])) for row in rows]
    except (ValueError, KeyError, TypeError) as e:
        _log_error("gowiththeflow: dnsmasq_reservations.php returned unparseable output: %r" % (e,))
        return None


def build_protected_dests(local_subnets: list[str]) -> list[str]:
    """LAN-to-LAN protection only -- firewall-self protection is handled
    independently, at the pf-rule level, by core's own `(self)` literal
    (confirmed live against OPNsense core's own dnsmasq_firewall() hook,
    which uses the identical alias), so this never needs to guard against
    local_subnets being blank/wrong the way an earlier design draft did."""
    return sorted(set(local_subnets))


def choose_pin_target(
    reservations: list[tuple[str, str]],
    identity_rows: list[sqlite3.Row],
) -> dict[str, str]:
    """One ip -> one mac, for every ip with at least one reservation
    candidate MAC currently observed there. Dnsmasq's own multi-MAC
    syntax (`hwaddr=aa:..,bb:..`) means "either MAC gets this IP" (e.g.
    a laptop's wifi and ethernet, expected to never be simultaneously
    active) -- since a static ARP entry can only ever pin ONE mac to a
    given ip, this can't literally honor "any of them" the way a bare
    pf-level IP check can:
    - exactly one candidate mac for this ip -> pin it.
    - multiple candidates, exactly one currently observed at this ip in
      local_host_identity -> pin that one.
    - multiple candidates, more than one currently observed at this ip
      -> pin whichever has the most recent updated_at (mirrors
      block_host.py's own "most recent wins" idiom).
    - multiple candidates, NONE currently observed at this ip -> skip
      pinning this ip entirely this cycle. The ip stays allowed on the
      pf table regardless (that check is ip-only, independent of this
      function) -- a real, accepted gap: until one of the reservation's
      candidate MACs is actually observed once via DHCP/ARP, that ip's
      ARP entry isn't pinned, so a rogue device could squat it in that
      narrow window. Retried every cycle, same "miss it, move on"
      acceptance this project already takes elsewhere (e.g. db.py's
      best-effort dpi_protocol UPDATE)."""
    candidates_by_ip: dict[str, list[str]] = {}
    for mac, ip in reservations:
        candidates_by_ip.setdefault(ip, []).append(mac)

    identity_by_mac = {row["mac"]: row for row in identity_rows}

    target: dict[str, str] = {}
    for ip, macs in candidates_by_ip.items():
        observed = [
            identity_by_mac[mac] for mac in macs
            if mac in identity_by_mac and identity_by_mac[mac]["ip"] == ip
        ]
        if not observed:
            continue
        best = max(observed, key=lambda row: row["updated_at"])
        target[ip] = best["mac"]
    return target


def diff_arp_pins(conn: sqlite3.Connection, target: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Reads arp_pins (what THIS module applied last time, not `arp -an`)
    and compares against `target`. An ip whose target mac differs from
    its currently-pinned mac appears in BOTH returned dicts -- arp -S
    doesn't merge two pins for the same ip, the old one must be
    explicitly cleared before the new one is set."""
    current = {row["ip"]: row["mac"] for row in conn.execute("SELECT ip, mac FROM arp_pins")}
    to_add = {ip: mac for ip, mac in target.items() if current.get(ip) != mac}
    to_remove = {ip: mac for ip, mac in current.items() if ip not in target or target[ip] != mac}
    return to_add, to_remove


def apply_arp_pins(
    conn: sqlite3.Connection, to_add: dict[str, str], to_remove: dict[str, str], now: int,
) -> list[str]:
    """Applies the diff via `arp -S`/`arp -d`, timeout=15, check=False --
    a missing/duplicate entry is an expected, non-fatal outcome (same
    posture as pfctl -k's "0 states killed"). Returns warning strings for
    any genuinely unexpected non-zero exit rather than raising, matching
    blocklist.sync_pf()'s own "return, don't raise" convention. A device
    that just lost its reservation gets its existing states killed
    immediately (blocklist.kill_states()), mirroring block_host.py's own
    "this address should stop working right now" behavior rather than
    leaving already-open connections to limp along until they expire."""
    warnings = []
    for ip, mac in to_remove.items():
        result = subprocess.run(
            [ARP_BIN, "-d", ip], capture_output=True, text=True, check=False, timeout=15,
        )
        # Only clear our own tracking row if the entry is actually gone
        # (exit 1, "not found", counts as gone too) -- a genuine failure
        # here (any other exit code) must NOT be recorded as done, or
        # the next reconcile pass would never retry it, believing
        # arp_pins already reflects reality when it doesn't. Confirmed
        # live this distinction matters: a boot-time arp -S can fail
        # silently before the interface is fully up (see the -S note
        # below) -- the same self-healing-retry principle applies to
        # removal.
        if result.returncode in (0, 1):
            conn.execute("DELETE FROM arp_pins WHERE ip = ?", (ip,))
            conn.commit()
        else:
            warnings.append(f"arp -d {ip} failed: {(result.stderr or result.stdout).strip()[:200]}")
        blocklist.kill_states(ip)
    for ip, mac in to_add.items():
        result = subprocess.run(
            [ARP_BIN, "-S", ip, mac], capture_output=True, text=True, check=False, timeout=15,
        )
        # Same reasoning as removal above, the other direction: only
        # record a pin as applied if arp -S actually succeeded. Found
        # live on a real reboot: the daemon's own startup-time reconcile
        # can run before le0 is fully initialized, so arp -S can fail at
        # that exact moment even though the identical command succeeds
        # a few seconds later once the interface is ready -- if this
        # wrote the DB row regardless, the periodic 60s tick would never
        # retry it, since diff_arp_pins() would already believe it was
        # correctly pinned.
        if result.returncode == 0:
            conn.execute(
                "INSERT INTO arp_pins (ip, mac, applied_at) VALUES (?, ?, ?) "
                "ON CONFLICT(ip) DO UPDATE SET mac=excluded.mac, applied_at=excluded.applied_at",
                (ip, mac, now),
            )
            conn.commit()
        else:
            warnings.append(f"arp -S {ip} {mac} failed: {(result.stderr or result.stdout).strip()[:200]}")
    return warnings


def purge_all(conn: sqlite3.Connection) -> None:
    """Removes every currently-tracked ARP pin -- used both when the
    arp-pinning toggle is turned off (while the main gate may stay on)
    and by the package's pre-deinstall cleanup, so uninstalling never
    leaves a stale static ARP entry behind."""
    for row in conn.execute("SELECT ip FROM arp_pins").fetchall():
        subprocess.run([ARP_BIN, "-d", row["ip"]], capture_output=True, text=True, check=False, timeout=15)
    conn.execute("DELETE FROM arp_pins")
    conn.commit()


def reset_pin_tracking(conn: sqlite3.Connection) -> None:
    """Forgets every ARP pin this module believes it has applied,
    WITHOUT touching the actual (kernel) ARP table at all -- call this
    exactly once, at daemon startup, before the first reconcile().

    Found live on a real reboot: arp_pins is a SQLite table, so it
    survives a reboot; the kernel's own ARP cache does not -- it's
    always empty on a fresh boot. Without this, diff_arp_pins() sees a
    surviving arp_pins row for a device that's still correctly reserved
    and concludes "already applied, nothing to do," permanently skipping
    the actual arp -S needed to restore it, since nothing about a
    reservation staying unchanged across a reboot would ever look like a
    diff. This just clears the bookkeeping so the very next reconcile()
    call treats every currently-needed pin as new and applies it fresh
    -- correct and cheap, since arp -S against an ip that happens to
    already be correctly pinned is a harmless no-op either way."""
    conn.execute("DELETE FROM arp_pins")
    conn.commit()


def reconcile(conn: sqlite3.Connection, now: int, settings: dict) -> dict:
    """Top-level orchestrator -- called by both gowiththeflowd.py's
    periodic tick and reservation_sync.py's "Sync now" CLI."""
    if not settings.get("enable_reservation_gate"):
        blocklist.sync_pf_table(PF_ALLOWED_TABLE, ALLOWED_TABLE_FILE, [])
        purge_all(conn)
        return {"status": "ok", "enabled": False, "allowed_count": 0, "pinned_count": 0}

    reservations = fetch_reservations()
    if reservations is None:
        # Deliberately touches NO pf/arp state -- see fetch_reservations()'s
        # own docstring for why a failed read must never look identical
        # to "zero reservations."
        return {"status": "error", "error": "could not read Dnsmasq reservations"}

    allowed_ips = sorted({ip for _mac, ip in reservations}, key=ipaddress.ip_address)
    blocklist.sync_pf_table(PF_ALLOWED_TABLE, ALLOWED_TABLE_FILE, allowed_ips)
    blocklist.sync_pf_table(
        PF_PROTECTED_TABLE, PROTECTED_TABLE_FILE, build_protected_dests(settings.get("local_subnets", [])),
    )

    if not settings.get("enable_arp_pinning"):
        purge_all(conn)
        return {"status": "ok", "enabled": True, "allowed_count": len(allowed_ips), "pinned_count": 0}

    identity_rows = conn.execute(
        "SELECT ip, mac, updated_at FROM local_host_identity WHERE ip IS NOT NULL"
    ).fetchall()
    target = choose_pin_target(reservations, identity_rows)
    to_add, to_remove = diff_arp_pins(conn, target)
    warnings = apply_arp_pins(conn, to_add, to_remove, now)

    return {
        "status": "ok", "enabled": True,
        "allowed_count": len(allowed_ips),
        "pinned_count": len(target),
        "pins_added": len(to_add), "pins_removed": len(to_remove),
        "warnings": warnings,
    }
