# Go With The Flow — OPNsense per-host connection & bandwidth tracking plugin

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
- **Phase B3 — complete.** `HistoryController`/`Api/HistoryController.php`/
  `history.volt` built and verified end-to-end against the real VM with
  synthetic `rollup_hourly` data: same-pair hourly buckets sum correctly
  across a day range, the local-host filter works, and the `days=90`
  daily-rollup path returns cleanly when `rollup_daily` is empty.
  Extracted `Api/DbApiControllerBase.php` (shared `DB_PATH`/`openDb()`/
  `formatHost()`) since a second controller now needed the same logic
  `LiveController` had. The Chart.js timeseries chart from the original
  design is deliberately deferred (Chart.js confirmed bundled on the VM
  as `chart.umd.min.js`), not dropped — the breakdown table was this
  stage's core deliverable.
- **Phase B4 — complete.** `ToptalkersController`/`Api/ToptalkersController.php`/
  `toptalkers.volt` built and verified end-to-end against the real VM
  (local/remote ranking totals, and the `local_host` filter correctly
  isolating one host's share of a shared-IP remote rather than the
  combined total). One real bug found: `TopTalkersController` (a compound
  two-word class name) 404'd until renamed to `ToptalkersController` —
  Phalcon's URL-to-class convention capitalizes only the first letter of
  the whole URL slug (confirmed against the real `NetworkinsightController`
  example). Dropped the originally-planned separate `sort_by` param —
  Bootgrid's native column-click sorting covers it for free.
- **Phase B5 — complete.** The full Settings surface: `GoWithTheFlow`
  config model, `ServiceController` (start/stop/restart/status/
  reconfigure via `ApiMutableServiceControllerBase`), `SettingsController`
  + `settings.volt` (declarative form, save/apply, two housekeeping
  action buttons), and — pulled forward from Phase C because
  `ServiceController` needed something real underneath it — a genuinely
  working rc.d script + configd action. All verified end-to-end against
  the real VM: settings save to `config.xml`, `service/reconfigure`
  actually starts the daemon once enabled, and both housekeeping buttons
  correctly truncate their tables. Several real bugs found along the way
  (see the three B5 commits for full detail): a missing shebang line;
  rc.subr's `_run_rc_doit` running the command *synchronously* rather
  than forking it (fixed with OPNsense core's own bundled `Daemonize`
  helper, matching the real `os-netflow` plugin); absolute paths needed
  for every subprocess call under rc.d's minimal PATH; rc.subr's
  rc.conf enable-gate needing `onestart`/`onestop`/etc. instead of plain
  verbs; and two `ApiMutableModelControllerBase` conventions
  (`getAction()` needs a real GET, `setAction()` needs fields nested
  under the model name) that only surfaced by reading the real PHP error
  log at `/var/lib/php/tmp/PHP_errors.log`.
  The `staticOverrides` grid+dialog editor is deliberately deferred
  (same pattern as the History chart) — the model/schema already support
  it via direct API calls without a polished grid UI.
- **Phase B6 — complete.** Resilience check: `kill -9`'d the running
  daemon on the real VM and confirmed pf (still enabled, uptime
  uninterrupted), `dnsmasq` (same PID, unaffected), Unbound (unchanged
  from its pre-existing baseline state), and internet connectivity (ping
  still worked) were all completely unaffected — concrete proof of the
  "monitoring stops, internet doesn't" design claim, not just an
  assumption.
- **Phase B7 — complete.** End-to-end functional test on the real VM,
  prompted by the discovery that enabling the service via the Settings
  GUI had no effect at all. Found and fixed three real, previously
  unnoticed wiring bugs, all now fixed and verified with real captured
  traffic (github.com session: SNI-resolved hostname, 91s duration,
  9,060 bytes out / 612,404 bytes in, correctly closed out of
  `live_sessions` into `connections_raw`):
  1. `gowiththeflowd.py`'s `__main__` built `Config()` from bare
     dataclass defaults and never read the Settings model at all --
     enabling the service in the GUI changed nothing about how the
     daemon actually ran. Fixed by adding a real config-generation path:
     a Jinja template (`service/templates/OPNsense/GoWithTheFlow/config.json`,
     wired via `ServiceController::$internalServiceTemplate`, the same
     mechanism `os-dnsmasq`/`os-netflow` use) renders the model to
     `/var/etc/gowiththeflow.json` on every reconfigure, and
     `Config.load()` reads it at daemon startup. This also fixed a
     latent second bug the same template exposed: `captureInterfaces`
     stores logical names (`lan`, `wan`), but scapy's `sniff()` needs
     physical device names (`le0`, `le1`) -- solved with the same
     `helpers.physical_interfaces()` Jinja helper `os-dnsmasq` uses.
  2. `PfStatePoller`/`classify_local_remote()` discards every pf state
     where local_subnets is empty (neither side matches any configured
     subnet) -- so an unset `localSubnets` (optional field, easy to
     leave blank while testing other settings) silently drops 100% of
     traffic with no error anywhere. Not code-fixed (this is arguably
     correct behavior for an unset required-in-practice field); worth
     a follow-up UX improvement (validation warning or a sane subnet
     auto-detect default) before Phase D.
  3. `DbApiControllerBase::DB_PATH` and `SettingsController::DB_PATH`
     (two independent copies) were still hardcoded to
     `/tmp/test_flows.db` from early Phase B testing, while the real
     daemon has always written to `/var/db/gowiththeflow/flows.db` --
     so the Live/History/TopTalkers grids and the housekeeping buttons
     were reading/clearing a file the daemon never touches. Both fixed
     to the real path.
- **Phase B8 — complete.** rctl (CPU/memory caps) actually wired up,
  ahead of the real home-box deployment. `general.cpuLimitPct`/
  `memLimitMB` now flow into `/var/etc/gowiththeflow.json` (added to the
  config template) and `rc.d/gowiththeflow`'s new `start_postcmd` applies
  `rctl -a process:$pid:pcpu:deny=<pct>` and
  `process:$pid:memoryuse:deny=<mb>M` once the daemon's real pid is
  known. Two real findings from testing this on the VM:
  1. `kern.racct.enable` is a boot-time-only tunable (`/boot/loader.conf`,
     not settable live via `sysctl`) -- added via the proper OPNsense
     Tunables mechanism (`OPNsense\Core\Tunables` model +
     `configctl service restart login/sysctl`, matching what the real
     Tunables GUI page does), then a VM reboot to activate. **This means
     enabling caps on the real home box will require the same reboot --
     flag this to the user and get explicit confirmation before doing it,
     since it's a brief internet outage for the whole household, not
     something to do silently.**
  2. `pcpu:throttle` -- the action documented for CPU limiting -- returned
     `EOPNOTSUPP` on this kernel; `pcpu:deny` is the portable choice and
     is what's actually used.
  3. `start_postcmd` fires before Daemonize's double-fork has necessarily
     written the pidfile (`_run_rc_doit` returns as soon as the *first*
     fork's parent exits) -- fixed with a short poll loop (up to 2s)
     rather than assuming the pidfile already exists.
- **Phase C (packaging) — package build + VM install cycle complete.**
  No ports tree exists on an installed OPNsense box (confirmed on the
  VM), and OPNsense's real plugin build pipeline (poudriere-style,
  cloning core+plugins+ports repos) is heavy overhead just to produce an
  installable package -- so `net/gowiththeflow/pkg/build-pkg.sh` instead
  hand-builds a real package directly with `pkg create -m +MANIFEST -p
  pkg-plist`, staging files from `../src` into a temp root. Verified
  end-to-end on the VM exactly as committed (not just informally): built
  `os-gowiththeflow-1.0.0.pkg`, removed all manually-deployed files,
  `pkg add`'d the built package, ran the real `register.php install`
  (confirmed `system.firmware.plugins` in config.xml gets the entry, same
  as the real Firmware GUI), started the service, confirmed rctl caps
  still apply against the fresh pid, then `pkg remove`'d it and confirmed
  a fully clean uninstall -- no leftover files or directories anywhere
  under `/usr/local/opnsense`.
  Three real findings along the way:
  1. `pkg create -m metadatadir -r rootdir` does **not** auto-include
     files found under rootdir -- without an explicit `-p plist` it
     silently produces a 0-file, 0-byte package (no error). A plist is
     required.
  2. `pkg remove` doesn't stop the service on its own -- OPNsense's own
     `remove.sh` just does `pkg remove -y` with no service-stop step, so
     the package needs its own `+MANIFEST` `"scripts": {"pre-deinstall":
     ...}` calling `onestop` (not `stop`, since `gowiththeflow_enable`
     is never set to YES by design -- same enable-gate finding as B5),
     confirmed removed the running daemon cleanly before its files went
     away.
  3. Python's own bytecode cache (`__pycache__`) isn't part of any file
     manifest, so it silently survived the first `pkg remove` test as an
     orphaned leftover -- fixed with `gowiththeflow_env=
     "PYTHONDONTWRITEBYTECODE=1"` in the rc.d script (rc.subr's
     `${name}_env` mechanism) so it never gets created at all, plus
     explicit `@dir` plist entries so the plugin's own now-empty MVC
     directories (controllers/models/views, never anything shared like
     the parent `OPNsense/` namespace dir) get removed too.
  Package metadata lives in `net/gowiththeflow/pkg/` (`+MANIFEST`,
  `pkg-plist`, `version.json.tmpl`, `build-pkg.sh`); `build-pkg.sh` must
  run on a FreeBSD/OPNsense box with `pkg(8)` (pkg create doesn't
  cross-build).
- **Phase C (repo catalog + fetch-based install) — complete on the VM.**
  `pkg repo <dir>` generates a real catalog (`packagesite.pkg`,
  `meta.conf`, `data.pkg`) from the built `.pkg`; served over plain HTTP
  via `python3 -m http.server` on the VM standing in for what
  `gowiththeflow-pkg-repo` + GitHub Pages will serve for real, registered
  as a custom repo (`signature_type: "none"` -- fine for now, revisit
  before this repo is public) via
  `/usr/local/etc/pkg/repos/gowiththeflow.conf`. Full real lifecycle
  verified through `pkg update` / `pkg install` / `pkg upgrade` (a
  genuine 1.0.0 -> 1.0.1 test bump) / `pkg remove`, all fetching from the
  repo catalog rather than a local file, plus the actual
  `firmware/remove.sh` script (confirms `register.php remove` correctly
  clears `system.firmware.plugins` in config.xml). The upgrade path
  specifically confirmed pkg's documented script ordering (new
  pre-install -> **old** pre-deinstall -> replace -> new post-install) --
  our `pre-deinstall` hook fired and stopped the old daemon correctly
  before files got replaced.
  One real, non-gowiththeflow-specific finding: `firmware/install.sh`
  refuses to install **any** plugin unless the box's own core package is
  fully up to date against the configured repo ("Installation out of
  date. The update to opnsense-26.7.2_2 is required.") -- this test VM's
  core isn't current against the real `pkg.opnsense.org` repo, so
  `install.sh` itself was not exercised end-to-end (its underlying `pkg
  install` + `register.php install` calls were, directly, and are the
  actual mechanism being tested). **This means the real home box will
  need to be fully up to date on core before this plugin can be
  installed there** -- standard OPNsense practice for any plugin, not an
  extra requirement this project introduces, but worth checking before
  Phase D.
  Test-only infrastructure (the local http.server, the `gowiththeflow`
  repo conf, the 1.0.1 test bump) was torn down afterward; the VM was
  left with `os-gowiththeflow` actually installed (v1.0.1) and running,
  registered the same way a real Firmware-GUI install would leave it.
- **`gowiththeflow-pkg-repo` is now actually live on GitHub Pages**
  (served via a pre-existing custom domain on the account's user-pages
  site, `tobyandzuzka.com` -- `tobydoig.github.io` URLs 301-redirect
  there; documented in that repo's README). A real `os-gowiththeflow
  1.0.0` build (rebuilt clean from exactly what's committed, not the
  1.0.1 test-upgrade artifact) plus its catalog were pushed and verified
  reachable. Added a downloadable `gowiththeflow.conf` so setup on a
  real box is one `curl` into `/usr/local/etc/pkg/repos/`, then the rest
  through Firmware > Plugins -- verified this exact one-liner against
  the live URL on the test VM before handing it to the user.
- **Phase D has effectively started** (ahead of the original plan --
  the user ran the one-liner + installed on their real home box, nostromo,
  before the planned core-up-to-date check or the rctl/reboot
  conversation happened). Two more real, install-only bugs found from
  that real box that never surfaced on the VM (the VM's own repeated
  reboots across earlier phases incidentally masked both):
  1. **`localSubnets`'s form field was fundamentally the wrong type.**
     `type="NetworkField"` + `<AsList>Y</AsList>` makes
     `BaseSetField::getNodeData()` return list-shaped data (`{value:
     {value, selected}}`, the shape a `select_multiple` checkbox-style
     widget expects) but the form field was declared as plain
     `<type>text</type>` -- so after a save, reloading Settings showed
     the field blank (list-shaped data fed into a plain text input).
     Fixed by changing the form field to
     `type="select_multiple"` + `style="tokenize"` +
     `allownew="true"` (confirmed via `os-dnsmasq`'s working
     `captureInterfaces` field using the same pairing, and via OPNsense
     core's `form_input_tr.volt` template source). Checked directly on
     the real box's rendered `/var/etc/gowiththeflow.json` that the
     *daemon's own config* was actually correct throughout
     (`local_subnets` correctly split out) -- so this was a real bug,
     but display-only, not what was keeping Reporting empty.
  2. **`configd` doesn't pick up a newly-installed plugin's
     `actions.d/*.conf` until it's restarted** -- it only scans that
     directory at its own startup. `firmware/install.sh` doesn't restart
     it, and neither did our package (no post-install step existed).
     On the VM this never surfaced because a reboot (for B6's kill test,
     then B8's `kern.racct.enable` change) always happened between
     install and first status-check, coincidentally restarting configd
     too. On the real box, `configctl gowiththeflow status` returned
     "Action not allowed or missing" until `service configd restart`
     was run manually. Fixed with a `post-install` script (added
     alongside the existing `pre-deinstall` one) that runs
     `service configd restart` and clears the menu cache
     (`/var/lib/php/tmp/opnsense_menu_cache.xml` -- the same manual step
     every VM test needed, also never actually fixed in the package
     until now).
  3. **Root cause of the "OK but nothing ever runs" mystery, found and
     fixed.** `db.connect()`'s very first line, `sqlite3.connect(db_path)`,
     fails immediately if `/var/db/gowiththeflow` doesn't exist -- and
     nothing in the install path ever created it. This was invisible on
     the dev VM for the entire project because that directory has existed
     there since early manual testing, before packaging even existed --
     every test this project ever ran was against a box that already had
     it. The exception happens inside `Daemonize`'s forked child, *after*
     its fds are already redirected to `/dev/null`, so it's completely
     silent: no pidfile, no process, no syslog entry (`Daemonize` only
     logs its own "Starting daemon."/pidfile-failure messages via
     syslog, and even the parent's own `sys.exit(0)` on a successful fork
     tells you nothing about what the child does afterward -- confirmed
     by reading `site-python/daemonize.py`'s source directly). Confirmed
     by reproducing on the VM: `rm -rf`'d its long-standing
     `/var/db/gowiththeflow`, got the exact same silent-failure symptom
     the user hit, then confirmed the fix resolves it. Fixed with
     `os.makedirs()` in `db.connect()` itself (guarded for the
     `":memory:"` case the test suite uses, which has no dirname), plus a
     regression test using a genuinely nonexistent nested path rather
     than pytest's `tmp_path` (which is always already a real directory,
     exactly the blind spot that let this ship in the first place).
  Rebuilt and republished as `os-gowiththeflow` **1.0.1** (a real bump,
  not the earlier throwaway 1.0.1 test artifact -- a same-numbered
  rebuild wouldn't have been detected as an upgrade by `pkg`, since it
  compares version strings, not content) with all three fixes, verified
  against a fully clean install cycle on the VM: no pre-existing data
  directory, no manual configd restart, no manual menu-cache clear --
  `pkg add` alone now gets a genuinely working, running service.
- **Real-box validation, end to end.** The user upgraded on their actual
  home box (nostromo) via the real GUI path (System > Firmware >
  Plugins > Update), not a manual `pkg` command -- first real proof the
  whole one-liner-repo-config -> GUI-driven-lifecycle story holds up.
  One more expected (non-bug) wrinkle: a `pkg upgrade` replaces files and
  our `pre-deinstall` stops the *old* version, but nothing restarts the
  *new* one automatically -- consistent with how OPNsense plugin updates
  generally work (the Settings "Apply" button is the actual restart
  trigger), and confirmed: hitting Apply after the upgrade brought the
  daemon up and the Live grid started showing real traffic on the user's
  actual home network.
- **Phase D core-check and rctl caps — done on the real box.** Core
  confirmed current (26.7.2_2, matching what the VM needed to update to)
  via System > Firmware > Status before any of this started. Added
  `kern.racct.enable="1"` via System > Settings > Tunables (the GUI path
  this time, not the model-script trick used on the VM) and rebooted;
  confirmed afterward with `sysctl kern.racct.enable` (1) and
  `rctl -h process:<pid>` showing both `pcpu:deny=10` and
  `memoryuse:deny=256M` actually applied against the real daemon's pid
  on nostromo. This closes out the "wire up rctl before going live"
  requirement from the start of Phase C -- the plugin is now running on
  real production hardware, resolving real hostnames for real traffic,
  under enforced resource caps.
- **1.0.2 / 1.0.3: two more real user-reported fixes, both shipped.**
  1. **Live grid "Local Port" column added** (1.0.2). The user spotted
     what looked like duplicate rows (same local/remote host, similar
     durations) and asked whether it was a bug. It wasn't -- the
     model's `UNIQUE` constraint already includes `local_port`, and
     wildly differing byte counts/durations between the "duplicate"
     rows confirmed they were genuinely separate concurrent connections
     (a phone or headset maintaining several parallel connections to
     the same CDN/API endpoint is completely normal) -- but the grid
     never displayed `local_port`, so there was no way to see that at a
     glance. Added the column so this is verifiable rather than taken
     on faith.
  2. **Auto-restart after upgrade if already enabled** (1.0.3). Real
     complaint: after a `pkg upgrade`, an already-running service was
     left stopped until the user manually hit Settings > Apply, even
     though `pre-deinstall` had just stopped a *genuinely running*
     daemon moments before. `post-install` now unconditionally
     re-renders the config template (safe on a real fresh install too,
     since the model defaults to `enabled=0`, so the render correctly
     says so and nothing auto-starts) and only calls `onestart` if that
     render says `enabled=1` -- verified both properties directly on
     the VM before publishing: an enabled+running install upgraded and
     came back up with no manual step, and a config reset to
     `enabled=0` beforehand correctly stayed stopped after a fresh
     install.
  1.0.2 confirmed installed on the real box via the actual GUI
  Firmware > Plugins > Update path; 1.0.3 published and VM-verified,
  not yet installed there.
- **Not yet started**: the deferred History chart and staticOverrides
  grid editor, proper repo signing before this pkg-repo is relied on for
  anything that matters. See "Roadmap" below for the larger post-launch
  feature set (app/category classification, local<->local tracking,
  Sankey visualization, DPI) agreed after the user asked to aim for
  rough feature parity with the commercial ZenArmor plugin.
- **Distribution repos**: both GitHub repos created and pushed —
  `github.com/tobydoig/opnsense-gowiththeflow` (private, source, this repo)
  and `github.com/tobydoig/gowiththeflow-pkg-repo` (public, placeholder
  README only — no package published yet, that's the rest of Phase C).
  Push access uses a dedicated passphrase-free deploy key
  (`~/.ssh/gowiththeflow_deploy`, ed25519) added to the GitHub account —
  needed because the existing personal keys were passphrase-protected and
  `ssh`/`ssh-add` can't prompt for a passphrase in a non-interactive shell.

## Roadmap: post-launch feature set (ZenArmor-inspired, scoped down)

Prompted by the user asking to make this "as full-featured as possible,
on a par with the commercial ZenArmor plugin." ZenArmor's real feature
set spans several genuinely different problem domains -- malware/botnet
detection and threat-intel feeds, multi-site cloud management, SSL
inspection -- that the user explicitly said they don't want (no interest
in malware detection or "multi-cloud whatnot"), so the scoped-down list
below is deliberately narrower than "everything ZenArmor has":

1. **App/category classification** (agreed as the first of these to
   build). Not DPI -- a lookup enrichment on hostnames already resolved
   via DNS/SNI/PTR against a free, actively-maintained domain->category
   list (candidate: `v2fly/domain-list-community`, MIT licensed, files
   like `category-social-media`, `netflix`, `youtube`, `games`; matched
   by domain suffix, which is a better fit than IP-based lists since CDN
   IPs rotate but the resolved hostname doesn't). Store the category
   alongside the existing hostname cache, surface it as a column/filter
   in Live/History/TopTalkers, add a bytes-by-category rollup.
2. **Local<->local traffic tracking** (agreed, general case only).
   `classify_local_remote()` currently discards any pf state where both
   ends are local -- extending this to emit a third "local<->local"
   category needs its own schema (asymmetric local/remote columns don't
   fit) and hostname resolution via ARP/DHCP-lease lookups on *both*
   ends rather than DNS/SNI. **Important caveat, confirmed on the real
   box**: this only works for local traffic that actually routes through
   the firewall (e.g. devices on separate VLANs/subnets). Checked the
   motivating example directly (`cam-path` -> `nvr`, both on the same
   192.168.200.0/24) via `pfctl -vvs state` and found zero states
   between them -- only broadcast/multicast discovery traffic and the
   NVR's own internet-bound connection appeared. Two devices on the same
   L2 broadcast domain switch traffic directly and never reach the
   firewall at all, so this feature (or any firewall-based tool,
   ZenArmor included) categorically cannot see same-subnet traffic like
   that -- not a limitation of this project specifically.
3. **Live page redesign + Sankey-style flow visualization.** The Live
   grid is currently one row per individual connection -- on a real
   network with several always-reconnecting devices (see the
   `cam-cabin`/TIME_WAIT investigation above) that's 5 pages of mostly
   noise, not useful at a glance. Agreed redesign: one row per *local
   host* by default, showing aggregate totals (bytes in/out summed
   across that host's current connections) instead of a flat per-
   connection list; clicking a host row drills into a Sankey-style
   diagram of just that host's own connections (local host -> each
   remote host/category, sized by bytes). Client-side rendering on data
   we already have; more interesting once category data from #1 exists
   (local host -> category -> remote host is a much better diagram than
   local -> remote alone, and gives the Sankey a middle tier to branch
   through).
4. **DPI (nDPI or similar)** -- added to the backlog, explicitly scoped
   after discussing the tradeoff: gives protocol/app identification
   independent of hostname/port (works for non-standard ports, obscure
   protocols) and QUIC/HTTP-3 awareness (a real current gap -- some of
   the unresolved port-443 entries the user noticed are likely QUIC,
   which the current sniffer doesn't parse at all), plus some resilience
   to encrypted DNS (DoH) since it doesn't depend on seeing the DNS
   query. Does **not** help with Encrypted Client Hello (ECH) -- SNI
   hidden cryptographically, unreadable by any packet inspection;
   defeating it needs active SSL/TLS interception (installing a trusted
   root cert on every device, decrypting and re-encrypting everything),
   a much bigger and more invasive undertaking than DPI itself, out of
   scope. A materially heavier architecture than the passive-sniffing
   design this project started with (new dependency, more CPU/memory) --
   user is explicitly fine with the resource cost given their hardware
   (16GB RAM, 4-core i7) and belief that requirements won't exceed
   ZenArmor's own, but this needs its own scoping pass before starting,
   not a quick add-on to the existing daemon.

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
    │   ├── mvc/app/                        # Phase B2-B5 -- all done
    │   │   ├── controllers/OPNsense/GoWithTheFlow/
    │   │   │   ├── LiveController.php          # DONE -- UI page controller (extends IndexController, picks live.volt)
    │   │   │   ├── HistoryController.php       # DONE
    │   │   │   ├── ToptalkersController.php    # DONE -- note: single-capitalized-word class name (see below)
    │   │   │   ├── SettingsController.php      # DONE -- loads forms/general.xml via getForm()
    │   │   │   ├── forms/general.xml           # DONE -- declarative field defs for the settings form
    │   │   │   └── Api/
    │   │   │       ├── DbApiControllerBase.php # DONE -- shared DB_PATH/openDb()/formatHost()/rollupTableForDays()
    │   │   │       ├── LiveController.php      # DONE -- reads live_sessions via native SQLite3, searchRecordsetBase()
    │   │   │       ├── HistoryController.php   # DONE -- aggregates rollup_hourly/rollup_daily by (local_ip, remote_ip)
    │   │   │       ├── ToptalkersController.php # DONE -- localAction()/remoteAction(), ranks by bytes/connections
    │   │   │       ├── ServiceController.php   # DONE -- trivial ApiMutableServiceControllerBase subclass
    │   │   │       └── SettingsController.php  # DONE -- ApiMutableModelControllerBase + clearData/resetHostnameCache
    │   │   ├── models/OPNsense/GoWithTheFlow/
    │   │   │   ├── GoWithTheFlow.xml           # DONE -- enable, interfaces, subnets, retention, rctl caps, hostname tuning
    │   │   │   ├── GoWithTheFlow.php           # DONE -- plain BaseModel, no custom validation needed
    │   │   │   ├── ACL/ACL.xml                 # DONE -- ui/gowiththeflow/*, api/gowiththeflow/*
    │   │   │   └── Menu/Menu.xml               # DONE -- Reporting > Go With The Flow > Live/History/Top Talkers; Services > Go With The Flow > Settings
    │   │   └── views/OPNsense/GoWithTheFlow/
    │   │       ├── live.volt                   # DONE -- Bootgrid with byte/duration formatters
    │   │       ├── history.volt                # DONE -- Bootgrid breakdown table + day-range/local-host filters
    │   │       ├── toptalkers.volt              # DONE -- two Bootgrids (local/remote) + shared days selector
    │   │       └── settings.volt                # DONE -- form + save/apply + housekeeping buttons
    │
    │   Naming gotcha (confirmed on the real VM): OPNsense/Phalcon's
    │   URL-to-controller-class convention capitalizes only the *first*
    │   letter of the whole URL slug -- a controller serving
    │   /ui/gowiththeflow/toptalkers must be class `ToptalkersController`,
    │   not `TopTalkersController` (compare the real core example,
    │   `NetworkinsightController`, not `NetworkInsightController`). Any
    │   future compound-word page name needs the same single-capitalized-
    │   word treatment.
    │   └── service/conf/actions.d/          # DONE -- pulled forward from Phase C during B5
    │       └── actions_gowiththeflow.conf       # configd actions using onestart/onestop/onerestart/onestatus
    └── etc/rc.d/gowiththeflow                   # DONE -- pulled forward from Phase C; uses OPNsense's bundled Daemonize helper
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

*(Done — Phase B5, plus the rc.d/configd pieces pulled forward from
Phase C. One gap vs. the original design: the rc.d script does not yet
actually apply the `rctl` CPU/memory cap it's supposed to — the
`CpuLimitPct`/`MemLimitMB` model fields exist and save correctly, but
nothing reads them into an actual `rctl` rule yet. TODO before Phase D.)*

- `src/etc/rc.d/gowiththeflow`: rc(8) script starting/stopping
  `gowiththeflowd.py` via OPNsense core's bundled `Daemonize` helper (see
  Real-world corrections above) — not yet applying the `rctl` CPU/memory
  cap (below) to the daemon's process/login class at start.
- `actions_gowiththeflow.conf`: configd actions using rc.subr's own
  `onestart`/`onestop`/`onerestart`/`onestatus` verbs (not the plain
  `start`/`stop`/`restart`/`status` originally sketched — see Real-world
  corrections above for why).
- `Api/ServiceController.php` extends `ApiMutableServiceControllerBase`
  (3 static properties, no method overrides needed), giving
  `/api/gowiththeflow/service/{start,stop,restart,status,reconfigure}`.
  The Settings page's Apply button calls `reconfigure`, not a plain
  `restart` — matching the real base class's behavior (stop/start/reload
  based on the model's `enabled` field), not a hand-rolled restart call.
- Settings model (`GoWithTheFlow.xml`), scoped to "Essential + hostname
  tuning" per user decision, all fields implemented and confirmed
  round-tripping through `config.xml`:
  - **Essential**: `enabled` (default false — installing the package does
    nothing until explicitly turned on), `captureInterfaces` (multi-select
    interface list — saved as a comma-separated string, not a PHP array;
    see Real-world corrections), `localSubnets` (CIDR list via
    `NetworkField`/`AsList`, editable so VPN tunnel subnets can optionally
    count as "local"), `rawRetentionDays` (default 10),
    `rollupHourlyRetentionDays` (default 45), `rollupDailyRetentionDays`
    (default 730), `cpuLimitPct` and `memLimitMB` (rctl cap fields exist
    and save correctly; not yet wired into an actual `rctl` rule — see
    the gap noted above).
  - **Hostname tuning**: `enableDnsSniffing`, `enableSniSniffing`,
    `enablePtrFallback` (independent bools), `extraTlsPorts` (comma list,
    default empty). The `staticOverrides` repeating list (IP/CIDR →
    friendly name) exists in the model/schema and is reachable via direct
    API calls, but its grid+dialog editor UI is deliberately deferred
    (same pattern as the History chart).
  - Save flow is `settings/set` then `service/reconfigure` (see above) —
    confirmed via `SimpleActionButton`/`saveFormToEndpoint` exactly
    matching the real `os-netflow` plugin's own settings page pattern.

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
| POST | `/api/gowiththeflow/history/search/` | + `days`, `local_host?` | **DONE.** rollup rows aggregated by (local_ip, remote_ip), granularity auto-picked by `days` vs. the 45-day hourly-retention threshold, plus a `local_hosts` map for the filter dropdown |
| — | *(deferred)* timeseries endpoint for the History chart | `local_host`, `remote_host`, `days`, `bucket=hour\|day` | `{ts, bytes_in, bytes_out}[]` for charting (not a Bootgrid search — a plain data endpoint) |
| POST | `/api/gowiththeflow/toptalkers/local` | `days` | **DONE.** ranked local hosts by total bytes/connections (sortable by clicking either column — no separate `sort_by` param needed) |
| POST | `/api/gowiththeflow/toptalkers/remote` | `days`, `local_host?` | **DONE.** ranked remote hosts; filtering by `local_host` correctly shows only that host's share of a shared-IP remote, not the combined total |
| GET/POST | `/api/gowiththeflow/service/{start,stop,restart,status,reconfigure}` | — | **DONE.** standard `ApiMutableServiceControllerBase` envelope |
| GET/POST | `/api/gowiththeflow/settings/{get,set}` | note: `get` needs an actual GET; `set` needs fields nested under `gowiththeflow[...]` | **DONE.** standard `ApiMutableModelControllerBase` envelope |
| POST | `/api/gowiththeflow/settings/clearData` | — | **DONE.** truncates `connections_raw`/`rollup_hourly`/`rollup_daily`/`live_sessions` (housekeeping action button) |
| POST | `/api/gowiththeflow/settings/resetHostnameCache` | — | **DONE.** truncates `ip_hostname_cache` only, forcing re-learning (housekeeping action button) |

Local-host display names come from a plain
`LEFT JOIN local_host_identity ON local_ip = ip` in each query — that table
is kept fresh by the daemon's `localhost_identity.py` (see above), so PHP
never talks to Dnsmasq itself; there is exactly one place in the codebase
that knows how to look up a DHCP lease. PHP reads the SQLite file via the
native `SQLite3` class (`SQLITE3_OPEN_READONLY`) — this PHP build has no
PDO drivers compiled in at all, only the `sqlite3` extension.

## Frontend UI

*(All four pages done — Phases B2-B5.)*

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
- **Settings — DONE, minus one deferred piece**: enable checkbox;
  multi-select capture interfaces; editable local-subnets list; retention
  (raw/hourly-rollup/daily-rollup) and CPU/memory-cap number inputs; a
  "Hostname resolution" section with the DNS/SNI/PTR enable toggles and
  extra-TLS-ports field; "Clear All Data" and "Reset Hostname Cache"
  action buttons (confirm dialog via `stdDialogRemoveItem` + `ajaxCall`,
  matching the real `os-netflow` reset-button pattern); a debug-logging
  toggle. Save → `settings/set` → `service/reconfigure` (not a plain
  `restart` — see API endpoints above). **Deferred**: the Bootgrid-style
  editable table for static IP/CIDR → hostname overrides — the
  `staticOverrides` field exists in the model and is reachable via direct
  API calls, just without a polished grid+dialog editor yet.

Menu/ACL: `Menu.xml` adds two "Go With The Flow" entries — Live/History/Top
Talkers under **Reporting**, Settings under **Services** (confirmed the
same tag name under two different top-level parents renders cleanly, no
collision); `ACL.xml` grants `ui/gowiththeflow/*` and `api/gowiththeflow/*`
under one key covering both. A shared `formatHost($hostname, $ip)` PHP
helper (`DbApiControllerBase::formatHost()`) is used consistently across
all three data grids, satisfying the "always show a hostname where known,
else IP" requirement everywhere.

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
3. **DONE.** `HistoryController`/`history.volt` — verified against
   synthetic rollup data shaped like real `rollup.py` output (see Status
   above).
4. **DONE.** `ToptalkersController`/`toptalkers.volt` — verified rankings
   against synthetic traffic volumes (see Status above).
5. **DONE.** `SettingsController`/`settings.volt` + `ServiceController` —
   verified end-to-end (see Status above): settings save to config.xml,
   `service/reconfigure` genuinely starts/stops the daemon based on the
   model's `enabled` field.
6. **DONE.** Resilience check — see Status above.

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
