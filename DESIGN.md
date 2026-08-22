# GoWithTheFlow — OPNsense per-host connection & bandwidth tracking plugin

## Status (updated as work progresses)

- **Phase A — complete.** All 7 daemon modules (`pf_state_poller.py`,
  `db.py`, `rollup.py`, `dns_sniffer.py`, `sni_sniffer.py`,
  `localhost_identity.py`, `correlator.py` + supporting `hostcache.py`/
  `ptr_resolver.py`) built and verified against fixtures. 69 tests passing.
  `gowiththeflowd.py` (the daemon entrypoint wiring everything together)
  also written.
- **Phase B0 — complete.** Isolated OPNsense 26.7 test VM running in
  VirtualBox: LAN `10.0.0.1/24` (host-only adapter, host at `10.0.0.9`),
  WAN on a VirtualBox NAT adapter (real outbound internet via VirtualBox's
  own NAT engine, `10.0.3.0/24` — isolated from the real home LAN/WAN).
  SSH key auth set up (`~/.ssh/gowiththeflow_opnsense`) for direct
  deployment/testing from the dev machine.
- **Phase B1 — complete.** Full daemon run against real capture, real
  `pfctl` output, real Dnsmasq leases, real DNS traffic (dozens of live
  hostnames captured), and an isolated real TLS ClientHello test (faked
  SNI, exact match extracted). **Five real bugs found and fixed** — see
  "Real-world corrections" below; none of these were guessable from docs
  alone, which is exactly why Phase B exists.
- **Phase B2 — complete.** `LiveController`/`Api/LiveController.php`/
  `live.volt` built, deployed to the real VM's MVC tree, and verified
  end-to-end via an authenticated curl session: page renders correctly
  (menu entry, grid, columns), and the search API returns correctly
  joined/formatted hostnames and a live-computed duration. Base templates
  came from reading the real installed `os-dnsmasq` plugin's own files on
  the VM (`LeasesController.php`/`leases.volt`/`ApiControllerBase.php`)
  rather than guessed conventions. One gotcha found: OPNsense caches the
  built menu at `/var/lib/php/tmp/opnsense_menu_cache.xml` — new
  `Menu.xml` entries don't appear until that's cleared.
- **Not yet started**: Phase B3+ (History/TopTalkers/Settings
  controllers+views), Phase C (packaging), Phase D (production rollout).
- **Distribution repos**: local git repo initialized and committed at
  `D:\code\opnsense-gowiththeflow`; the two GitHub repos described below
  (`opnsense-gowiththeflow` private, `gowiththeflow-pkg-repo` public) have
  **not yet been created** — still local-only. TODO before Phase C.

## Context

The user wants an OPNsense plugin that tracks, per local host, which remote
hosts it talks to and how many bytes in/out, both live (open sessions) and
historically — and in every view, shows a real hostname (not just an IP),
resolved passively via DNS + TLS SNI observation rather than full IDS-grade
DPI. This is a brand-new, standalone project — unrelated to the
`mlmBackoffice` repo it was originally planned alongside.

Decisions made with the user:
- **Hostname strategy**: lightweight custom sniffer (passive DNS answers +
  TLS ClientHello SNI), not Suricata/full DPI. PTR reverse-DNS as last resort.
- **Byte/session data source**: poll pf's state table (`pfctl -vvs state`),
  not custom flow accounting.
- **Daemon language**: Python 3, using `scapy` (confirmed available as
  `py313-scapy` on the real OPNsense 26.7 box, pulling in `py313-pypcap`
  for native libpcap capture — zero extra dependency-hunting needed).
- **Storage**: local SQLite, WAL mode, raw records (~10 days) rolled up into
  hourly/daily aggregates for longer-term graphing.
- **Location**: this project, packaged as a standard OPNsense plugin port.

Target environment (confirmed by user): OPNsense 26.7.2_2-amd64 / FreeBSD
15.1-RELEASE-p2 (the test VM reports `stable/26.7-n283674-12334a596709`).
DHCP/local-DNS is **Dnsmasq DNS & DHCP** (`os-dnsmasq`), not Kea or ISC
dhcpd. Unbound runs as the primary resolver on port 53 and forwards
local-domain queries to Dnsmasq listening on port 5353. This changes the
local-host-naming source (see below) but not the DNS sniffer's capture
port — clients still query Unbound on port 53, and it relays Dnsmasq's
answer back to the client on that same port, so `dns_sniffer.py` only
needs to watch port 53 on LAN interfaces; the internal 53↔5353
Unbound→Dnsmasq forwarding is backend chatter it doesn't need to inspect.

Two design passes (data-collection/storage, and plugin/UI/packaging) were
run in parallel; this plan merges them into one buildable structure. One
reconciliation: the storage design assumed the Python daemon might ship as a
separate FreeBSD package dependency. For a plugin this size, bundling the
daemon's scripts inside the plugin's own port (as `os-telegraf`, `os-netdata`
and similar plugins do) is simpler to build/install/version — so everything
lives in one port, `net/gowiththeflow`.

## Real-world corrections found in Phase B1

Five real bugs, all invisible to fixture-based unit tests, caught by
running against the actual OPNsense box:

1. **`pf_state_poller.py` — real `pfctl -vvs state` format.** Real output
   has a leading `all` token before the protocol (not anticipated at all),
   uses IPv6 `addr[port]` bracket notation vs IPv4's `addr:port`, and TCP
   states have an extra window-scale detail line between the header and
   the `age ...` stats line (`expires in` is also `HH:MM:SS`, not a bare
   seconds count). Header parsing now anchors on the arrow token (`<-`/
   `->`) instead of assuming a fixed field count, and block parsing scans
   all of a state's detail lines for the stats line instead of assuming
   it's always the very next line.
2. **`pf_state_poller.py` — portless IPv6 states crashed it.** A transient
   link-local IPv6 state (ICMPv6 neighbor discovery, no port on either
   side) crashed `ipaddress.ip_address()` because a naive "split on the
   last colon" corrupted `fe80::1` into `ip='fe80:', port='1'` (IPv6
   addresses already contain colons). Now validates the whole token as a
   bare address (optionally with an IPv6 `%scope`) before falling back to
   an `ip:port` split, and states with no port on either side are simply
   dropped (not this module's connection model).
3. **`localhost_identity.py` — wrong JSON shape.** The real
   `configctl dnsmasq list leases` backend
   (`/usr/local/opnsense/scripts/dnsmasq/get_dnsmasq_leases.py`) returns
   `{"records": [...]}`, not `{"leases": [...]}` as originally assumed —
   and critically has **no `is_reserved` field at all** (that only exists
   in the richer PHP web API controller, not what `configctl` itself
   returns). The dhcp_lease/static_mapping source distinction this module
   originally planned to surface isn't obtainable from this source, so
   every lease-derived record is now just labeled `dhcp_lease`.
4. **`dns_sniffer.py`/`sni_sniffer.py` — scapy datalink detection.**
   scapy's `sniff()` failed to auto-guess the capture datalink type
   (silently falling back to an undissected `Raw` packet, with a console
   warning but no exception) depending on which scapy submodules happened
   to be imported first elsewhere in the process — non-deterministic and
   import-order-dependent. Both now always explicitly re-parse captured
   bytes as `Ether(bytes(pkt))` rather than trusting `sniff()`'s own
   dissection to have succeeded.
5. **`sni_sniffer.py` — Ethernet padding corrupted ClientHello
   reassembly.** A bare ACK segment (no real payload) gets Ethernet-padded
   to the minimum frame size, and scapy splits that padding into its own
   `Padding` layer — but `bytes(tcp.payload)` picks it up anyway, since
   `.payload` just means "whatever the next dissected layer is". Those
   zero bytes got fed into the `ClientHelloReassembler` ahead of the real
   ClientHello, permanently corrupting every subsequent byte offset for
   that flow. Fixed by computing the true TCP payload length from IP's own
   total length field (`_tcp_payload_bytes()`) instead of trusting scapy's
   layer-boundary guess.

One schema addition discovered necessary while implementing (not a bug,
but a real gap in the original design's prose): pf's byte/packet counters
are cumulative-since-creation and never reset, so the "checkpoint a
long-lived session every hour" behavior described below can't just zero
`live_sessions.bytes_in/out` — it needs separate `baseline_*` columns and
a `last_checkpoint_at` timestamp to track how much of the cumulative total
is already reflected in `connections_raw`. See the schema below.

## Package layout

Single port, `net/gowiththeflow` (package `os-gowiththeflow`), rooted at
this repo:

```
net/gowiththeflow/
├── Makefile                    # FreeBSD port Makefile (ports-style, standard for OPNsense plugins) [not yet written]
├── pkg-plist                   # [not yet written]
├── pkg-descr                   # [not yet written]
├── tests/                      # Phase A fixture-based unit tests (pytest) + tests/manual/ VM diagnostic scripts
└── src/
    ├── opnsense/
    │   ├── scripts/gowiththeflow/          # the Python daemon (own module tree, no MVC dependency) -- DONE
    │   │   ├── gowiththeflowd.py           # entrypoint: wires up threads/asyncio tasks below
    │   │   ├── dns_sniffer.py              # pcap on udp/tcp 53, parses answers -> (ip, hostname, ttl, ts)
    │   │   ├── sni_sniffer.py              # pcap on tcp 443 (+configurable ports), extracts ClientHello SNI
    │   │   ├── pf_state_poller.py          # periodic `pfctl -vvs state` diffing -> session open/update/close
    │   │   ├── correlator.py               # joins dns/sni/pf-state streams into connection records
    │   │   ├── hostcache.py                # durable IP->hostname cache with source/TTL/ambiguity handling
    │   │   ├── localhost_identity.py       # refreshes local IP/MAC->hostname from DHCP leases + statics
    │   │   ├── ptr_resolver.py             # rate-limited reverse-DNS fallback, negative caching
    │   │   ├── db.py                       # SQLite connection mgmt (WAL, batched writes)
    │   │   └── rollup.py                   # hourly/daily rollup + raw-retention pruning job
    │   ├── mvc/app/                        # Phase B2+ -- Live done, rest pending
    │   │   ├── controllers/OPNsense/GowiththeFlow/
    │   │   │   ├── LiveController.php          # DONE -- UI page controller (extends IndexController, picks live.volt)
    │   │   │   ├── HistoryController.php       # [not yet written]
    │   │   │   ├── TopTalkersController.php    # [not yet written]
    │   │   │   ├── SettingsController.php      # [not yet written]
    │   │   │   └── Api/
    │   │   │       ├── LiveController.php      # DONE -- reads live_sessions via native SQLite3, searchRecordsetBase()
    │   │   │       ├── ServiceController.php   # [not yet written] extends ApiMutableServiceControllerBase
    │   │   │       ├── SettingsController.php  # [not yet written]
    │   │   │       ├── HistoryController.php   # [not yet written]
    │   │   │       └── TopTalkersController.php # [not yet written]
    │   │   ├── models/OPNsense/GowiththeFlow/
    │   │   │   ├── GowiththeFlow.xml           # [not yet written] config.xml-backed model: enable, iface, retention
    │   │   │   ├── GowiththeFlow.php           # [not yet written]
    │   │   │   ├── ACL/ACL.xml                 # DONE -- ui/gowiththeflow/*, api/gowiththeflow/*
    │   │   │   └── Menu/Menu.xml               # DONE -- Reporting > Flow Monitor > Live (History/TopTalkers/Settings to add per-stage)
    │   │   └── views/OPNsense/GowiththeFlow/
    │   │       ├── live.volt                   # DONE -- Bootgrid with byte/duration formatters
    │   │       ├── history.volt                # [not yet written]
    │   │       ├── toptalkers.volt              # [not yet written]
    │   │       └── settings.volt                # [not yet written]
    │   └── service/conf/actions.d/          # [not yet written -- Phase C]
    │       └── actions_gowiththeflow.conf       # configd actions wrapping rc.d script
    └── etc/rc.d/gowiththeflow                   # [not yet written -- Phase C] rc(8) script starting/stopping gowiththeflowd.py
```

SQLite database lives at `/var/db/gowiththeflow/flows.db` on the firewall
(currently tested at `/tmp/test_flows.db` on the VM). The daemon is the
sole writer; PHP controllers open it read-only (`sqlite:...?mode=ro`,
`PRAGMA busy_timeout`) — safe under WAL.

## Data collection & hostname resolution

- **dns_sniffer.py**: BPF filter `udp port 53 or tcp port 53` on configured
  LAN interfaces; parses NOERROR A/AAAA answers into `hostcache` with
  source=`dns`, TTL clamped to [60s, 24h]. Confirmed on the real VM:
  captured dozens of genuine hostnames (Google, Microsoft, YouTube, etc.)
  correctly.
- **sni_sniffer.py**: BPF filter `tcp port 443` (+ configurable extra
  ports); buffers early client→server bytes per new flow (correctly
  excluding Ethernet padding — see corrections above), detects TLS
  handshake/ClientHello, extracts the SNI extension. Feeds a short-lived
  (~60s) in-memory `flow_hostname_hints` map keyed by
  `(local_ip, local_port, remote_ip, remote_port)` — note this is a
  4-tuple, not the 3-tuple originally sketched, deliberately including
  `local_port` so two *concurrent* connections from the same client to the
  same shared-IP CDN edge don't overwrite each other's hint. Confirmed on
  the real VM with a faked SNI value (`sni-test.example`), exact match.
- **pf_state_poller.py**: polls `pfctl -vvs state` every 5–30s (configurable,
  default 5s), diffs snapshots by 4-tuple key to detect new/updated/closed
  sessions and read pf's own cumulative byte/packet counters (not
  self-summed deltas, to avoid drift). Local vs. remote is decided by
  matching configured LAN subnets, not pf's in/out label — this also
  correctly excludes admin-plane traffic to OPNsense's own LAN IP (both
  endpoints match the local subnet) and OPNsense's own outbound traffic
  (neither endpoint matches), confirmed against real captured states.
- **correlator.py**: for each new/updated session, resolves the "best"
  hostname in priority order: user-configured static override (see
  Settings, below — explicit user intent always wins) → live SNI hint →
  non-expired/non-ambiguous `ip_hostname_cache` entry → (for historical
  rows) the hostname already snapshotted at write time → raw IP. Local-host
  name is resolved the other direction — at query time from current
  DHCP-lease/static data — since a device's IP↔MAC binding can change
  within a retained window.
- **localhost_identity.py**: refreshes local IP/MAC→hostname every 5 min
  from OPNsense's Dnsmasq service, via `configctl dnsmasq list leases`
  (real shape: `{"records": [...]}`, fields `address`/`hwaddr`/`hostname`/
  `expire`/`client_id`/`iaid`/`if` — see corrections above; no reservation
  status available at this layer, so source is always `dhcp_lease` for
  lease-derived records). Falls back to `arp -an` only for devices with no
  lease/hostname at all — confirmed against the real VM's ARP table.
  **This table is the single source of truth for local-host naming** — the
  daemon is the only thing that talks to Dnsmasq; the PHP layer never
  re-implements DHCP-lease parsing (see API section below).
- **ptr_resolver.py**: last-resort, rate-limited reverse DNS with negative
  caching, only consulted when DNS/SNI have nothing.

Known, documented limitations: ECH-enabled TLS hides SNI (falls back to
DNS/PTR, reintroducing shared-IP ambiguity); QUIC/HTTP3 handshakes are
encrypted by default and are not decrypted (hostname relies on DNS
correlation only); clients using DoH/DoT to a resolver other than OPNsense's
own bypass the DNS sniffer entirely. These should be called out in the
plugin's settings/help text, not silently swallowed.

## SQLite schema

```sql
PRAGMA journal_mode=WAL;
PRAGMA auto_vacuum=INCREMENTAL;

CREATE TABLE live_sessions (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, local_port INTEGER NOT NULL,
  remote_ip TEXT NOT NULL, remote_port INTEGER NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL DEFAULT 0, bytes_out INTEGER NOT NULL DEFAULT 0,
  pkts_in INTEGER NOT NULL DEFAULT 0, pkts_out INTEGER NOT NULL DEFAULT 0,
  -- pf's counters are cumulative-since-creation and never reset, so an
  -- hourly checkpoint (rollup.py) can't zero bytes_in/out -- it records how
  -- much of the cumulative total is already reflected in connections_raw.
  -- (Added during implementation -- not in the original design prose.)
  last_checkpoint_at INTEGER NOT NULL DEFAULT 0,
  baseline_bytes_in INTEGER NOT NULL DEFAULT 0, baseline_bytes_out INTEGER NOT NULL DEFAULT 0,
  baseline_pkts_in INTEGER NOT NULL DEFAULT 0, baseline_pkts_out INTEGER NOT NULL DEFAULT 0,
  UNIQUE(proto, local_ip, local_port, remote_ip, remote_port)
);
CREATE INDEX idx_live_local ON live_sessions(local_ip);

CREATE TABLE connections_raw (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, local_mac TEXT,
  remote_ip TEXT NOT NULL, remote_port INTEGER NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  started_at INTEGER NOT NULL, ended_at INTEGER NOT NULL, duration_s INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL
);
CREATE INDEX idx_raw_local_end ON connections_raw(local_ip, ended_at);
CREATE INDEX idx_raw_remote_end ON connections_raw(remote_ip, ended_at);
CREATE INDEX idx_raw_end ON connections_raw(ended_at);

CREATE TABLE rollup_hourly (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, remote_ip TEXT NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, remote_ip)
);
CREATE INDEX idx_ru_h_local ON rollup_hourly(bucket_start, local_ip);
CREATE INDEX idx_ru_h_remote ON rollup_hourly(bucket_start, remote_ip);

CREATE TABLE rollup_daily (               -- same shape as rollup_hourly
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, remote_ip TEXT NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, remote_ip)
);
CREATE INDEX idx_ru_d_local ON rollup_daily(bucket_start, local_ip);
CREATE INDEX idx_ru_d_remote ON rollup_daily(bucket_start, remote_ip);

CREATE TABLE ip_hostname_cache (
  ip TEXT PRIMARY KEY, hostname TEXT NOT NULL, source TEXT NOT NULL, -- dns | sni | ptr
  ambiguous INTEGER NOT NULL DEFAULT 0, ttl_expires_at INTEGER NOT NULL,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, hit_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE local_host_identity (
  mac TEXT PRIMARY KEY, ip TEXT, hostname TEXT, source TEXT, -- dhcp_lease | arp
  updated_at INTEGER NOT NULL
);
CREATE INDEX idx_lhi_ip ON local_host_identity(ip);

CREATE TABLE rollup_state (bucket_kind TEXT PRIMARY KEY, last_bucket_start INTEGER NOT NULL);
```

## Rollup / retention job

Hourly (configd cron): checkpoint any `live_sessions` row whose
`last_checkpoint_at` predates the most recently completed hour boundary,
writing a synthetic `connections_raw` row for bytes-since-baseline (see
schema note above) and advancing the baseline — so long-lived sessions
(big downloads, VPN tunnels) aren't invisible to rollups until they finally
close. Then aggregate unrolled `connections_raw` buckets into
`rollup_hourly` (hostname = most-recent non-null value in the group).
Nightly: aggregate `rollup_hourly` → `rollup_daily`, prune `rollup_hourly`
past ~45 days (daily rows kept much longer — they're tiny; never pruned
before daily rollup has actually run), and prune `connections_raw` past
`RawRetentionDays` (default 10) *only* for buckets already reflected in
`rollup_hourly` (never before hourly rollup has run at all). Weekly
`PRAGMA incremental_vacuum` instead of full `VACUUM` to avoid a long
exclusive lock on a live appliance. All implemented and unit-tested
(Stage A3) against synthetic data, including the watermark/gap-safety
edge cases.

## Service management & settings

*(Not yet implemented — Phase B2+/C. Design below unchanged from original
plan.)*

- `src/etc/rc.d/gowiththeflow`: standard rc(8) script starting/stopping
  `gowiththeflowd.py`; also applies the `rctl` CPU/memory cap (below) to
  the daemon's process/login class at start.
- `actions_gowiththeflow.conf`: configd actions (`start|stop|restart|status`)
  wrapping the rc.d script.
- `Api/ServiceController.php` extends `ApiMutableServiceControllerBase`,
  giving `/api/gowiththeflow/service/{start,stop,restart,status}` and
  wiring the Settings page's enable toggle to a model-linked restart.
- Settings model (`GowiththeFlow.xml`), scoped to "Essential + hostname
  tuning" per user decision:
  - **Essential**: `Enabled` (default false — installing the package does
    nothing until explicitly turned on), `CaptureInterfaces` (multi-select
    interface list), `LocalSubnets` (CIDR list, pre-populated from
    OPNsense's own interface config but editable so VPN tunnel subnets —
    e.g. WireGuard/OpenVPN client ranges — can optionally count as
    "local"), `RawRetentionDays` (default 10), `RollupHourlyRetentionDays`
    (default 45), `RollupDailyRetentionDays` (default 730), `CpuLimitPct`
    and `MemLimitMB` (rctl cap, sensible defaults e.g. 10% / 256MB for a
    lightweight sniffer), read-only `DbPath` display.
  - **Hostname tuning**: `EnableDnsSniffing`, `EnableSniSniffing`,
    `EnablePtrFallback` (independent bools — e.g. to stop extra
    reverse-DNS traffic leaving the box if unwanted), `ExtraTlsPorts`
    (list, default empty, for TLS services beyond 443 like 8443), and a
    repeating `StaticHostnameOverrides` list (IP/CIDR → friendly name)
    for devices that never announce a hostname any other way — this list
    lives in config.xml like any other OPNsense setting, and the daemon
    treats it as the highest-priority hostname source (see correlator.py,
    above).
  - Saving any of these triggers `settings/set` then `service/restart`,
    same pattern as before — no partial-reload complexity needed given how
    lightweight a restart of this daemon is.

## API endpoints

*(`live/search` done — Phase B2. Others not yet implemented.)*

**Correction from the original plan**: grid endpoints follow OPNsense's
real convention, confirmed by reading `ApiControllerBase::searchRecordsetBase()`
on the test VM — a `POST .../search/` action (not a bare GET) taking
Bootgrid's own params (`current`, `rowCount`, `sort`, `searchPhrase`) and
returning `{"total": N, "rowCount": N, "current": N, "rows": [...]}` — not
the `{"rows":[...], "total":N, "generated":"<iso ts>"}` shape originally
sketched. `searchRecordsetBase()` handles sorting/filtering/pagination
generically given a plain PHP array of associative-array records, so each
controller's job is just: query SQLite, build that array, hand it off.

| Method | Path | Params | Returns |
|---|---|---|---|
| POST | `/api/gowiththeflow/live/search/` | Bootgrid standard (`current`, `rowCount`, `sort`, `searchPhrase`) | **DONE.** local/remote (`hostname (ip)`), proto, port, bytes in/out, live-computed duration |
| POST | `/api/gowiththeflow/history/pairs/search/` | + `days`, `local_host?`, `remote_host?` | rollup rows, granularity auto-picked by `days` |
| POST | `/api/gowiththeflow/history/timeseries` | `local_host`, `remote_host`, `days`, `bucket=hour\|day` | `{ts, bytes_in, bytes_out}[]` for charting (not a Bootgrid search — a plain data endpoint) |
| POST | `/api/gowiththeflow/toptalkers/local/search/` | + `days`, `sort_by=bytes\|connections` | ranked local hosts |
| POST | `/api/gowiththeflow/toptalkers/remote/search/` | + `days`, `local_host?`, `sort_by` | ranked remote hosts |
| * | `/api/gowiththeflow/service/*`, `/api/gowiththeflow/settings/*` | — | standard OPNsense service/settings envelopes |
| POST | `/api/gowiththeflow/settings/clearData` | — | truncates `connections_raw`/`rollup_hourly`/`rollup_daily`/`live_sessions` (housekeeping action button) |
| POST | `/api/gowiththeflow/settings/resetHostnameCache` | — | truncates `ip_hostname_cache` only, forcing re-learning (housekeeping action button) |

Local-host display names come from a plain
`LEFT JOIN local_host_identity ON local_ip = ip` in each query — that table
is kept fresh by the daemon's `localhost_identity.py` (see above), so PHP
never talks to Dnsmasq itself; there is exactly one place in the codebase
that knows how to look up a DHCP lease. PHP reads the SQLite file via the
native `SQLite3` class (`SQLITE3_OPEN_READONLY`) — this PHP build has no
PDO drivers compiled in at all, only the `sqlite3` extension.

## Frontend UI

*(Live done — Phase B2. History/Top Talkers/Settings not yet implemented.)*

**Correction from the original plan**: the original design called for one
`index.volt` shell with four client-side tabs. Reading the real
`os-dnsmasq` plugin's own Menu.xml on the test VM showed OPNsense's actual
idiomatic pattern is closer to **separate top-level pages**, each with its
own controller/volt/menu entry (its settings page uses hash-anchored tabs
*within* one page for closely related sub-sections, but its Leases grid —
structurally the same kind of page as this plugin's Live/History/Top
Talkers views — is its own standalone page). `LiveController`/`live.volt`
were built following that Leases pattern; History/TopTalkers/Settings
should follow the same pattern rather than a shared tabbed `index.volt`
shell. No `IndexController.php` — each page's own controller extends
`\OPNsense\Base\IndexController` directly (see `LiveController.php`).

Planned split (unchanged in spirit, just not one shared shell):
- **Live**: Bootgrid polling every 5–10s; Local, Remote (`hostname (ip)`),
  Port/Proto, ↓/↑ bytes, Duration; local-host filter dropdown.
- **History**: day-range picker (1/7/14/30/90), local-host selector,
  per-local-host remote-host breakdown table, Chart.js stacked-area from
  the timeseries endpoint.
- **Top Talkers**: two Bootgrids (local/remote) with a bytes-vs-connections
  sort toggle and days selector, plus a Chart.js horizontal bar of the top
  10.
- **Settings**: enable checkbox; multi-select capture interfaces; editable
  local-subnets list; retention (raw/hourly-rollup/daily-rollup) and
  CPU/memory-cap number inputs; a "Hostname resolution" sub-section with
  the DNS/SNI/PTR enable toggles and extra-TLS-ports list; a Bootgrid-style
  editable table for static IP/CIDR → hostname overrides (add/edit/delete
  rows, same pattern as Unbound's host-override list elsewhere in
  OPNsense); "clear all data now" and "reset hostname cache" action
  buttons; a debug-logging toggle. Save → `settings/set` → `service/restart`.

Menu/ACL: `Menu.xml` adds a "Flow Monitor" entry (under Reporting) with the
four tab URLs; `ACL.xml` grants `ui/gowiththeflow/*` and
`api/gowiththeflow/*`. A shared `formatHost(name, ip)` Vue helper
(`name ? "${name} (${ip})" : ip`) is used consistently across all grids and
chart tooltips, satisfying the "always show a hostname where known, else
IP" requirement everywhere.

## Distribution: custom pkg repo, one command then 1-click GUI install

*(Design decided; not yet executed — the two GitHub repos described below
don't exist yet. TODO before Phase C.)*

This is exactly the pattern already used by several real community OPNsense
add-on repos (e.g. `mimugmail`'s and `repo-mihak`'s) — no need to get
`os-gowiththeflow` merged into the official `opnsense/plugins` collection
for it to show up and install cleanly in the GUI.

**Two GitHub repos, split by visibility** (per user decision — keeps source
private with zero web-server maintenance, using GitHub Pages' free hosting
for the small public half):
- **`opnsense-gowiththeflow`** (private, **not yet created on GitHub** —
  currently just a local git repo) — the actual source tree from the
  Package layout section above (`net/gowiththeflow/...`).
- **`gowiththeflow-pkg-repo`** (public, separate repo, **not yet created**)
  — holds *only* the built pkg-repo output: the generated catalog files
  (`packagesite.yaml` etc.), the `.pkg` file(s), and the small bootstrap
  `gowiththeflow.conf`. Nothing but build artifacts and that one conf file
  — no source, no history that matters. GitHub Pages is enabled on this
  repo (free on a public repo, no paid plan needed) serving its root,
  giving a stable base URL:
  `https://<github-user>.github.io/gowiththeflow-pkg-repo/`.

**Publishing an update (manual, for now — no CI yet per user decision)**:
1. Build the `.pkg` for the new version inside the dev VM (per Build order,
   below).
2. Copy it into a local clone of `gowiththeflow-pkg-repo`, under its
   `repo/${ABI}/All/` layout.
3. Run `pkg repo <dir>` in that clone to regenerate the catalog
   (`signature_type: none`, so no signing key to manage).
4. `git add`/`commit`/`push` — GitHub Pages redeploys automatically. The
   firewall sees the update on its next Firmware → Check for updates /
   `pkg update`, no further manual step on the firewall side.

  (If this ever becomes tedious, steps 2-4 are a natural candidate for a
  later GitHub Actions workflow — deliberately deferred for now, not
  designed away.)

**Mechanics** (verified against FreeBSD `pkg.conf(5)` and a real example
repo config): OPNsense's Firmware → Plugins page simply asks `pkg` for
every package matching `os-*` across all *enabled* repos, official and
custom alike — a package doesn't need to come from the official repo to
appear there. A custom repo is just a small `.conf` file dropped into
`/usr/local/etc/pkg/repos/`, e.g.:

```
gowiththeflow: {
  url: "https://<github-user>.github.io/gowiththeflow-pkg-repo/repo/${ABI}",
  priority: 5,
  enabled: yes,
  signature_type: "none"
}
```
(`priority: 5` keeps it lower than the official repo's `priority: 0`, so
official packages always win if a name ever collided; `${ABI}` is filled in
by pkg itself, e.g. `FreeBSD:15:amd64`. `signature_type: "none"` skips
package-signing verification — a real, deliberate trade-off for a
low-stakes personal repo, not a hidden gap; call it out to the user rather
than silently accepting unsigned packages. This `.conf` file itself is one
of the few files committed to the public `gowiththeflow-pkg-repo`, served
at its Pages root.)

**One-time install command** (run once, as root, on the firewall):
```
fetch -o /usr/local/etc/pkg/repos/gowiththeflow.conf https://<github-user>.github.io/gowiththeflow-pkg-repo/gowiththeflow.conf
```
After that, `os-gowiththeflow` shows up in **System → Firmware → Plugins**
and installs/updates with the same one-click GUI flow as any official
plugin — no further command-line steps, ever again. This needs the
firewall to reach `github.io` over HTTPS (normal outbound internet, no
inbound exposure of anything on the LAN).

**Dependencies stay simple by construction**: the port's `Makefile`
declares `RUN_DEPENDS` (Python 3, and `py313-scapy`/`py313-pypcap` —
confirmed both available in the official OPNsense/FreeBSD repo, same
version as the dev venv) the normal FreeBSD-ports way. `pkg` resolves and
installs those automatically as part of one `pkg install os-gowiththeflow`
— the user never sees or does anything extra for them; they're pulled in
silently, same as any other plugin's dependencies.

## Build order & safe testing strategy

**Production is never the first (or fifth) place this runs.** The daemon
only ever *reads* — `pfctl -s state`, passive BPF packet capture, Dnsmasq's
read API — it never touches pf rules, DHCP, or interface config, so even a
crash degrades to "monitoring stopped," not "internet down" (pf/DHCP/DNS
are independent OPNsense services). Still, real testing happens on a
disposable VM, with production only touched at the very end and under
explicit resource caps.

Per the user's request, each stage adds exactly **one** new piece on top of
an already-verified base, and is checked against fixtures/output that
don't depend on anything not yet verified — so a bug found in stage N can
only be in stage N's new code, not a tangle of everything built so far.

### Phase A — one daemon module at a time, fully off-box, no VM — DONE

| # | Adds | Verified against | Proves | Status |
|---|---|---|---|---|
| A1 | `pf_state_poller.py` | captured/synthetic `pfctl -vvs state` text | open/update/close diffing and byte-counter math | done, later corrected against real output (see corrections) |
| A2 | `db.py` + schema | A1's parsed output | rows land correctly, zero hostname logic yet | done, schema later extended with baseline/checkpoint columns |
| A3 | `rollup.py` | synthetic `connections_raw` rows | hourly/daily aggregation + retention pruning math | done |
| A4 | `dns_sniffer.py` | pcap fixture files (built via scapy) | correct `(ip, hostname, ttl)` extraction | done, confirmed live |
| A5 | `sni_sniffer.py` | hand-built ClientHello byte fixtures, incl. shared-IP-CDN case | SNI extraction, flow hint cache, stream reassembly | done, later fixed for real-world Ethernet padding |
| A6 | `localhost_identity.py` | mocked `configctl dnsmasq list leases` JSON fixture | `local_host_identity` populates from lease data | done, later corrected against real JSON shape |
| A7 | `correlator.py` + `hostcache.py` + `ptr_resolver.py` | all of A1–A6's fixtures fed together | full hostname priority order resolves correctly | done |

### Phase B — first time real OPNsense is involved (the isolated VM)

0. **DONE.** Isolated OPNsense 26.7 VM provisioned (see Status above).
1. **DONE.** Daemon deployed and run against real capture; SQLite output
   inspected directly via `sqlite3` CLI. Five real bugs found and fixed
   (see "Real-world corrections"). DNS-path and SNI-path hostname
   resolution both confirmed working end-to-end against real traffic.
2. **DONE.** `LiveController` + `live.volt` — the simplest read path —
   verified against the DB already proven correct in B1 (see Status above).
3. **NEXT.** `HistoryController`/`history.volt`, verified once a rollup cycle has
   actually run (or the bucket boundary is advanced manually for a fast
   check).
4. `TopTalkersController`/`toptalkers.volt` — verify rankings against
   known synthetic traffic volumes.
5. `SettingsController`/`settings.volt` + `ServiceController` (enable/
   disable/restart, every toggle from the Settings section above) —
   verify each toggle actually changes daemon behavior.
6. **Resilience check**: deliberately `kill -9` the daemon mid-capture and
   confirm pf/DHCP/DNS on the VM are completely unaffected — the concrete
   proof of the "monitoring stops, internet doesn't" claim above, not
   just an assumption.

### Phase C — packaging & distribution (still in the VM) — not started

Build the actual port (Makefile/pkg-plist/pkg-descr), create the two
GitHub repos, stand up the custom pkg repo (see Distribution section
above), and test the full `fetch` bootstrap + Firmware → Plugins
install/upgrade/`pkg delete` cycle inside the VM — so packaging and
distribution are both proven clean before either is ever pointed at the
real firewall.

### Phase D — staged production rollout, with rails — not started

Run the one-time `fetch` bootstrap command on the real firewall, install
via Firmware → Plugins with the service **disabled by default**
(`Enabled=false`). Before ever enabling it, apply a FreeBSD `rctl`
resource cap (CPU/memory ceiling) to the daemon's process/login class so a
runaway process gets throttled or killed rather than degrading the box.
Enable it, watch `top`/System Health for a while, and keep the kill switch
obvious and close at hand (`service gowiththeflow stop` over SSH/console,
or disable-and-`pkg delete`) — but per the isolation point above, the
worst realistic outcome is losing this one plugin's data collection, not
losing routing/DHCP/DNS/internet.
