# Go With The Flow

An [OPNsense](https://opnsense.org/) plugin, the primary inspiration was
to for blocking devices on my local network on a schedule (ie. block the
kids phones, ipads etc at night so they sleep). Then added a few more
features like live details of what's actually going across the network
showing resolved hostnames instead of just IPs.

## Features

- **Live per-host traffic view** — which remote hosts each device on
  your network is talking to right now, with live bandwidth and a
  running "Top Talkers" ranking, built from pf's own connection-state
  table (no extra packet capture load for this part).
- **Hostname resolution without a DNS proxy** — passively watches DNS
  answers and TLS ClientHello SNI as traffic crosses the firewall to
  put a real hostname (`netflix.com`, not just an IP) on HTTPS/QUIC
  connections plain firewall logs can't label, with reverse-DNS (PTR)
  as a last-resort fallback.
- **History with configurable retention** — a raw connection log plus
  automatic hourly/daily rollups, so long-term bandwidth trends stay
  queryable long after the raw detail ages out.
- **DNS Queries page** — what each device is actually looking up, and
  what came back.
- **Automatic category classification** — destinations are tagged
  (social media, video streaming, ads, etc.) from a maintained public
  category list, with manual overrides for anything you want classified
  differently, and an offline pass (`recategorize.py`) to re-tag
  already-recorded history when the category list grows.
- **Optional deep packet inspection** — an opt-in nDPI-based pass for
  finer-grained protocol classification beyond what hostname/port alone
  can tell you.
- **Block rules** — named rules, each covering a *group* of devices:
  block a group entirely, or just a set of domains for it (full blocks
  ride your existing pf ruleset; domain-only blocks ride Unbound's own
  DNS blocklist feature, with automatic subdomain coverage). Optional
  weekly schedule (e.g. "block the kids' devices 10pm–7am on school
  nights"), correctly spanning midnight. Duplicate a rule to reuse it as
  a starting point for a similar one.
- **Devices identified by name, not just IP** — local hosts are labeled
  from DHCP reservations/observed hostnames rather than raw addresses,
  so a device stays recognizable even as its IP changes.

## Architecture

- **`src/opnsense/scripts/gowiththeflow/`** — the daemon
  (`gowiththeflowd.py`) and its supporting modules: passive packet
  capture (`dns_sniffer.py`, `sni_sniffer.py`), periodic `pfctl` state
  polling (`pf_state_poller.py`), stream correlation (`correlator.py`,
  `hostcache.py`, `ptr_resolver.py`), local identity tracking
  (`localhost_identity.py`), SQLite storage (`db.py`), rollup/retention
  (`rollup.py`), category classification (`categories.py`,
  `category_updater.py`, `manual_categories.py`, `recategorize.py`),
  optional DPI (`dpi_classifier.py`), and host/domain blocking
  (`blocklist.py`, `block_host.py`, `block_rules.py`,
  `block_rules_engine.py`, `block_schedule.py`, `dnsbl_apply.php`). The
  daemon only ever *reads* — `pfctl -s state`, passive BPF capture,
  Dnsmasq's read API — it never touches pf rules, DHCP, or interface
  config, so even a crash degrades to "monitoring stopped," not
  "internet down."
- **`src/opnsense/mvc/`** — the GUI: Phalcon MVC controllers/models/
  views under `OPNsense/GoWithTheFlow/`, following OPNsense's own plugin
  conventions (PHP reads via `openDb()`/SQLite directly; anything that
  mutates state goes through `configd`, since PHP itself can't touch pf
  or the daemon's database).
- **`src/etc/`** — the rc.d service script and the `plugins.inc.d` hook
  that registers this plugin's own pf table + block rules with core.
- **`pkg/`** — `+MANIFEST`, `pkg-plist`, and `build-pkg.sh` (a
  straight `pkg create -m -p` staged build, no ports tree/poudriere
  required — must run on a FreeBSD/OPNsense box).
- **`tests/`** — pytest unit tests for every daemon module (fixture- and
  synthetic-data based, no VM required to run them) plus
  `tests/manual/` diagnostic SQL/scripts for real-VM verification.

See [`DESIGN.md`](DESIGN.md) for the full build history, every real bug
found along the way and how, and the reasoning behind non-obvious
design decisions.

## Installing (as a built package)

```
fetch -o /usr/local/etc/pkg/repos/gowiththeflow.conf https://tobyandzuzka.com/gowiththeflow-pkg-repo/gowiththeflow.conf
```

Then either `pkg update && pkg install os-gowiththeflow`, or go to
**System → Firmware → Plugins** in the GUI and install `os-gowiththeflow`
from there. See the [package repo](https://github.com/tobydoig/gowiththeflow-pkg-repo)
for what's actually being served.

## Development

```
cd net/gowiththeflow
python -m pytest tests/ -q
```

Tests run anywhere with Python 3.13 — no OPNsense box or VM needed for
the daemon-side unit tests. Building an installable package
(`sh pkg/build-pkg.sh`) does require running on a real FreeBSD/OPNsense
box, since `pkg create` doesn't cross-build.

## License

MIT, with the [Commons Clause](https://commonsclause.com/) — free to
use, modify, and redistribute, including within a business, but nobody
may sell it or sell a product/service whose value comes substantially
from it. See [`LICENSE`](LICENSE).
