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
- **Roadmap item #1 (app/category classification) — built, not yet
  VM-verified or published.** Followed the user's "let's get started"
  on the roadmap below, in stages:
  1. `categories.py` (parser + `CategoryMatcher`, offline-testable) and
     `category_updater.py` (fetch/disk-cache the v2fly domain lists,
     network logic kept separate) — committed as `b56da28`/`98d5c61`,
     21 tests. Two real bugs found by re-testing against actually
     fetched upstream data (fixtures alone wouldn't have caught
     either): a tag-scoped `include:` (e.g. `category-ads`'s
     `include:google @ads`) was pulling in every domain instead of
     just the tagged subset, and the tag filter didn't propagate
     through *nested* includes (`category-ads` -> `meta` -> `facebook`),
     so `facebook.com` wrongly landed in Ads/Tracking instead of Social
     Media. Both fixed and covered by regression tests using the exact
     shapes that triggered them.
  2. Threaded `category` through the storage/resolution layer: a new
     column (with an `ALTER TABLE` migration for pre-existing installs)
     on all four session/rollup tables, `db.record_diff()`'s
     opened/updated/closed paths, `rollup.py`'s hourly/daily aggregation
     (same `COALESCE`/"most recent wins" pattern already used for
     hostname), and `correlator.resolve_remote_hostname()`/
     `make_resolver()` gaining an optional `categorize_fn` applied
     uniformly regardless of which source (static override, live SNI
     hint, hostcache) resolved the name. 97 tests passing (added
     end-to-end `categorize_fn` coverage in `test_correlator.py`, plus
     category assertions in `test_rollup.py`/`test_db.py` rather than
     leaving the new column and 3-tuple contract exercised only
     incidentally).
  3. Wired a `CategoryMatcher` into `gowiththeflowd.py`: built at
     startup from whatever's already disk-cached (so a fresh install
     doesn't block on a network fetch before the daemon can start),
     refreshed in a background thread on the existing daily job
     cadence. Caught a real bug while wiring this in: the PTR-fallback
     branch still unpacked `resolver(snap)` as a 2-tuple after
     `make_resolver()` started returning a 3-tuple in the previous
     stage — would have crashed the daemon's main loop on every poll
     the moment a PTR lookup was attempted. Also caught that
     `categories.py`/`category_updater.py` were missing from
     `pkg-plist` entirely — `pkg create -p` only packages what the
     plist lists, so despite `build-pkg.sh` copying the whole
     `scripts/gowiththeflow` directory into the staging root, the built
     `.pkg` would have shipped without them and the daemon would have
     died with an `ImportError` on a real install, invisibly to every
     test run so far (tests import against `src/` directly, never
     through the packaged plist). Both fixed before this ever reached
     the VM.
  4. Exposed it in the UI: a Category column on Live, History, and Top
     Talkers' remote grid, plus a new "By Category" tab (a new
     `ToptalkersController::categoryAction()`) aggregating bytes/
     connections per category over the selected day window — the
     "bytes-by-category rollup" from the roadmap item below.
     Unresolved traffic shows as "Uncategorized" rather than being
     dropped, so the category totals still reconcile against what Top
     Talkers reports for the same window.
  Version bumped to **1.0.8**, built and installed on the test VM
  (upgrading in place from 1.0.7 via `pkg add -f`, post-install
  correctly auto-restarted the already-enabled service). Confirmed on
  the real box: the built `.pkg` actually contains `categories.py`/
  `category_updater.py` this time (`pkg info -l -F` against the built
  package, not just the plist source); the daemon ran continuously for
  7+ minutes with no crash, including exercising the exact PTR-fallback
  code path that had the 2-tuple/3-tuple bug; the background category
  refresh completed inside the live daemon process (280 files fetched
  into `/var/db/gowiththeflow/categories`); and feeding that same
  real on-disk cache into `CategoryMatcher` directly reproduced every
  previously-fixed real-world case correctly (`doubleclick.net` ->
  Ads/Tracking, `facebook.com` -> Social Media, plus new checks against
  the live corpus like `steampowered.com` -> Gaming). **One gap**: no
  live *new* connection was observed actually landing in the DB with a
  category populated end-to-end -- the test VM's isolated host-only LAN
  doesn't route this dev machine's or the box's own traffic through pf
  in a way that generates fresh categorizable connections on demand, so
  that specific link (poll -> resolver -> categorize_fn -> DB write,
  for a brand new session) is verified at the unit-test level
  (`test_categorize_fn_result_lands_in_the_db_via_make_resolver`) and
  via each of its pieces independently on the real box, not via a
  single live end-to-end observation. Published to the pkg-repo
  afterward (old 1.0.7 `.pkg` removed, catalog regenerated with
  `pkg repo .` on the VM, following the repo's own "replace, don't
  accumulate" convention) -- available to nostromo on its next
  `pkg update -f && pkg upgrade`.
- **1.0.9 — real-box category feedback from nostromo, three real issues
  found and fixed.** The user ran 1.0.8 against real traffic and
  reported: Top Talkers' By Category tab showing only "Uncategorized";
  several real hostnames (`anthropic.com`, `googleapis.com`,
  `teams.microsoft.com`, `oculus.com`, `facebook.com`) with no category
  on the Live page; and `ec2-3-248-160-245.eu-west-1.compute.amazonaws.com`
  wrongly categorized as Shopping. Investigated by fetching the real
  v2fly files directly (no nostromo access needed for this part) rather
  than assuming:
  1. **Confirmed real bug**: v2fly's own `amazon` file does a bare
     `include:aws`, and the `aws` file lists `amazonaws.com` *and*
     `cloudfront.net` -- both were landing in Shopping via the same
     "coarse company file" pattern already caught once for
     `doubleclick.net`/Ads_Tracking. Fixed the same way: added `"aws"`
     to Cloud Infrastructure's own sources and moved Cloud
     Infrastructure earlier in `CATEGORY_SOURCES` so it's checked
     before Shopping. Re-verified against a freshly-fetched real corpus
     (not just the offline test fixtures) that this and every
     previously-fixed case are still correct.
  2. **Confirmed real scope gaps**: `oculus.com` has no top-level v2fly
     file of its own reachable from our Social Media sources (only
     reachable via a `meta` file we never included) -- fixed by adding
     `"oculus"` directly. `anthropic.com`/`openai.com` had nowhere to
     go at all despite v2fly carrying real, well-populated files for
     both -- added a new `"AI"` category sourced from them.
     `googleapis.com`/`teams.microsoft.com`/`facebook.com`, on the
     other hand, checked out fine against the real fetched data (all
     three correctly resolve given a populated cache) -- their gap on
     nostromo is most plausibly the next item, not a matching bug.
  3. **Real reliability gap**: `category_updater.fetch_all()` fetched
     its ~280 files one at a time with only a 10s-per-file timeout and
     no retry -- fine on the lab VM's clean VirtualBox NAT path (~2s
     total, measured), but plausibly much slower and less reliable on
     nostromo's real home connection, which would explain "categories
     for some, but not many" shortly after upgrading (anything resolved
     before the slow initial fetch finished got permanently frozen at
     no-category, the same "hostname snapshotted at write time"
     behavior already accepted for closed sessions). Fixed by fetching
     each round concurrently (bounded pool, `ThreadPoolExecutor`) with
     one retry per file before giving up.
  Also built, per the user's own suggestion, the other half of the
  workflow for domains no upstream file will ever cover: a
  `manual_categories.py` module (hostname/suffix -> category,
  initially empty) checked *before* the v2fly-based `CategoryMatcher`
  -- same precedence `static_overrides` gets over the automated
  hostname resolvers -- plus a new Top Talkers "Uncategorized Hosts"
  tab (`ToptalkersController::uncategorizedAction()`) listing real
  currently-uncategorized hostnames by traffic volume, exportable to
  CSV, so future gaps get found from what the box actually sees and
  brought back for a manual entry rather than guessed at ahead of time.
  103 tests passing. Version bumped to **1.0.9**.
  **VM-verified**, including a real testing-methodology bug caught
  along the way: `pkg add -f localfile.pkg` on an already-installed
  same-named package does *not* run the old package's `pre-deinstall`
  hook the way a genuine `pkg upgrade` transaction does -- so the
  1.0.8->1.0.9 upgrade tested that way left the *old* process running
  in memory despite the new files being on disk (same pid throughout,
  confirmed via `ps`), giving a false-looking "no crash" signal that
  was actually just "never loaded." Re-tested properly with a
  temporary local file-based pkg repo (`pkg repo .` against a copy of
  just the new `.pkg`, added as a second `enabled` repo alongside the
  real one) and a genuine `pkg upgrade -y os-gowiththeflow` -- this
  correctly ran `pre-deinstall` (daemon stopped) then `post-install`
  (fresh process, new pid, confirmed via `ps`), i.e. the exact
  mechanism nostromo's `pkg update -f && pkg upgrade` will use. With
  that real upgrade path exercised: the freshly-loaded 1.0.9 process's
  own background refresh completed in well under a minute (281 files,
  matching the ~2s measured on a clean dev connection, both far faster
  than 1.0.8's serial fetch); feeding that real on-disk cache into
  `CategoryMatcher` directly re-confirmed every case from the bug
  report resolves correctly now (`amazonaws.com`/`cloudfront.net` ->
  Cloud Infrastructure, not Shopping; `oculus.com` -> Social Media;
  `anthropic.com` -> AI); and the new `uncategorizedAction` SQL was
  run directly against the real database and returns exactly the
  pre-category historical hostnames (`github.com`, `opnsense.org`,
  etc.) the user's own guess predicted.
- **Roadmap item #2 ("Internal Traffic" -- local<->local session
  tracking) — built, not yet VM-verified or published.** Followed the
  same discipline as category classification: exploration first (3
  parallel research agents covering the pf_state_poller/db/rollup data
  model, the existing local-hostname-resolution machinery, and the
  PHP/Volt conventions), then a dedicated Plan agent to stress-test the
  design before writing any code. That review caught one genuinely
  critical bug before it shipped: a naive `GROUP BY (proto, ip_a, ip_b)`
  in the hourly rollup would have fragmented the *same* device pair into
  two separate rollup rows whenever traffic was initiated from both
  directions across different flows (e.g. host A mounts a share on host
  B during the day, host B backs up to host A overnight) -- directly
  undermining the point of a "which pairs talk the most" ranking. Fixed
  by canonicalizing `ip_a`/`ip_b` (numeric IP comparison, swapping the
  directional byte/packet counters together) in `rollup_internal_hourly`
  only -- the live-session and raw tables stay uncanonicalized
  deliberately, since the Live tab should show genuine per-flow
  direction. Also caught: joining `local_host_identity` twice in one
  query (once per endpoint, since neither side of an internal pair is
  "more local") risks a 2×2 row fan-out if either IP ever has a
  duplicate-MAC row -- avoided by using two correlated scalar subqueries
  instead of two `LEFT JOIN`s in the new `InternalController`.
  A genuine scope reduction was found during research, not assumed: no
  new hostname-resolution code was needed at all -- `local_host_identity`
  is already joined by IP at query time for the existing "local" side of
  every grid, so the same join, done twice, already names both endpoints
  of an internal pair.
  Built bottom-up, test-as-you-go: `pf_state_poller.py` gained a fully
  parallel, independent pipeline (`InternalPairKey`/`Snapshot`/
  `DiffResult`, `classify_internal_pairs()`, `poll_internal_pairs()` --
  deliberately re-parsing the same already-fetched pfctl text a second
  time rather than changing `poll()`'s existing, widely-depended-on
  `DiffResult` contract); `db.py` gained 4 new tables (`internal_*`, no
  hostname/category columns -- device-to-device flows don't have an
  "internet service" to categorize) and `record_internal_diff()`;
  `rollup.py` gained the internal checkpoint/hourly/daily mirrors plus
  the canonicalization fix, and `prune_raw`/`prune_hourly`/`prune_daily`
  were generalized to take a `table`/`rollup_watermark_kind` parameter
  (pure SQL-shape duplicates across pipelines, unlike checkpoint/rollup
  which genuinely differ in column set -- a deliberate case-by-case call
  on duplication vs. generalization, not a blanket rule either way);
  `gowiththeflowd.py` wired it into the existing poll/hourly/daily loop
  structure, reusing the same already-fetched `pfctl_output` text and the
  existing retention settings (no new Settings fields). 18 new tests (121
  total passing), including a dedicated regression test for the
  canonicalization fix using the exact bidirectional-traffic shape that
  motivated it.
  New top-level page "Internal Traffic" (Reporting > Go With The Flow,
  after Top Talkers) with Live and History tabs -- the History tab
  doubles as a "which pairs talk the most" ranking, so no separate
  Top-Talkers-style page was needed. `InternalController` (single-word
  slug, avoiding the already-known Phalcon capitalization gotcha), 3 new
  `pkg-plist` lines, one new `Menu.xml` line, no `ACL.xml` change needed
  (already wildcard-granted). Version bumped to **1.1.0** (minor, not
  patch -- this project's first genuinely new feature since app/category
  classification, not a bugfix).
  **VM-verified via the real `pkg upgrade` transaction** (not `pkg add
  -f`, per the testing-methodology lesson from 1.0.9) -- and this time
  with genuine real-traffic end-to-end proof, not just offline logic
  checks: the daemon's own SSH management sessions to the VM (10.0.0.9 ->
  10.0.0.1, both within `local_subnets`) were correctly classified,
  checkpointed, and rolled up. Forced a checkpoint + `rollup_internal_hourly`
  pass against that real accumulated data (51 real connections across
  tcp+udp) and the aggregation came out byte-for-byte correct
  (`bytes_a_to_b`/`bytes_b_to_a`/`conn_count` summed exactly across every
  underlying flow). Both `InternalController` files pass `php -l`.
  **Real finding from that same test, decided with the user rather than
  assumed**: any admin-plane traffic to OPNsense's own LAN IP (SSH, the
  web UI, DNS queries to it) satisfies "both endpoints local" the same as
  genuine device-to-device pass-through traffic (the camera<->NVR
  motivating case), so it shows up in Internal Traffic too -- notably
  different from `classify_local_remote()`, which has always excluded
  admin-plane traffic from the *remote*-tracking pipeline (see the
  existing `test_real_capture_admin_plane_traffic_to_opnsense_itself_is_skipped`
  test). Decision: ship as-is -- it's technically accurate (both ends
  really are local), not worth the extra complexity of detecting
  OPNsense's own interface addresses unless it proves annoying in
  practice. Revisit if it does.
- **1.2.0 — unified the local/remote and Internal Traffic pipelines into
  one "peer" model, added pf state + Live/History Overview charts,
  fixed a real History performance bug. Built and unit-tested (113
  tests), not yet VM-verified or published.** Using 1.1.0's separate
  Internal Traffic page in practice, the user observed the real
  distinction was never "local<->remote vs local<->local" as two kinds
  of thing -- it's always "one machine talking to another," where the
  other machine happens to be local or remote. Two parallel pipelines
  were duplicating the same logic and made the pages harder to reason
  about (the user's original "why don't I see cam-frontdoor talking to
  nvr" question came from checking the wrong page for exactly this
  reason). **This supersedes roadmap item #2 below** -- local<->local
  tracking is no longer a separate feature, it's `peer_is_local=1` rows
  flowing through the same Live/History/Top Talkers pipeline as every
  other peer.
  - Schema: `remote_ip`/`remote_port`/`remote_hostname` renamed to
    `peer_ip`/`peer_port`/`peer_hostname` on all four session/rollup
    tables; new `peer_is_local` column (0 = internet peer, 1 = local
    peer -- today's Internal Traffic case, named via a
    `local_host_identity` lookup at query time instead of DNS/SNI, and
    given the literal category sentinel `'Internal'`). The four
    `internal_*` tables and their dedicated poller/rollup/PHP code paths
    were deleted outright. Confirmed with the user that clearing
    existing tracking data is acceptable (still dev/testing) -- no
    migration path was built, `flows.db` just gets deleted before the
    upgraded daemon starts.
  - **A dedicated Plan-agent validation pass, run before writing any
    code (same discipline used for Internal Traffic originally), caught
    a real, previously-unconsidered bug**: canonicalizing
    `peer_is_local=1` pairs by numeric IP (needed so the same device
    pair doesn't fragment into two rollup rows depending on who
    initiated) means `local_ip` for an internal pair no longer reliably
    means "the host to filter/rank by." Three places would have
    silently misattributed or dropped internal-pair traffic for
    whichever pair member has the numerically larger address: History's
    `local_host` filter, Top Talkers' "Top Local Hosts," and -- most
    ironically -- Top Talkers' "Top Peers" (the ranking meant to let a
    device like an NVR show up as a top talker would have systematically
    failed for exactly that kind of low-static-IP device). Fixed
    entirely in the PHP query layer (no schema/rollup change) via
    `UNION ALL` queries that credit both members of an internal pair
    from their own point of view, swapping `bytes_in`/`bytes_out` on the
    reinterpreted branch. Independently verified correct against
    synthetic data via standalone Python `sqlite3` scripts before being
    trusted (no PHP test harness or local PHP interpreter exists in this
    project).
  - New `state` column (pf's own connection state, e.g. `ESTABLISHED`,
    `TIME_WAIT`, `FIN_WAIT_2:CLOSE_WAIT`) captured from `pfctl -vvs
    state`'s header line (previously parsed and discarded) and shown as
    a new Live column, addressing the user's stated skepticism about
    whether every row in the Live list is genuinely still active.
  - Real, reproduced-and-measured performance fix: the History page
    took 7.5s against a realistic 45-day/39k-row synthetic dataset,
    because its correlated subqueries had no index with
    `local_ip`/`peer_ip` as a leading column. Two new covering indexes
    (`(local_ip, peer_ip, bucket_start)` and `(peer_ip, bucket_start)`
    on both rollup tables, kept alongside the existing bucket_start-
    leading indexes -- both shapes are needed for different query
    patterns) brought it to 0.089s (~84x), confirmed via `EXPLAIN QUERY
    PLAN` showing an index `SEARCH` where there was previously a `SCAN`.
  - Live and History both became 2-tab pages (Overview + Table).
    Live's Overview: one shared client-side delta-computation pipeline
    (diffs each poll's cumulative bytes against the previous poll,
    grouped by local host or peer port, capped to the top 10 groups +
    "Other") rendered through 3 switchable views -- Line, Stacked Bar
    (both trivial Chart.js config swaps on one shared instance), and an
    experimental hand-drawn SVG node-link "Graph" view (edges/nodes
    fade out over ~4s when a connection closes, rather than vanishing
    instantly) -- category-based grouping and a real Sankey library were
    both explicitly rejected for this tab (category coverage isn't
    trusted enough yet for a glanceable view; Sankey wasn't confirmed
    available and wasn't needed for what this tab requires). Clicking a
    line/node/legend entry switches to the Table tab filtered to that
    host/port. History's Overview: a new `HistoryController::
    timeseriesAction()` endpoint (plain JSON, not a Bootgrid search)
    backing a per-local-host Line/Stacked-Bar chart at 1-hour or 1-day
    resolution (per-minute was considered and dropped as overkill),
    reusing the existing day-range and local-host-filter controls.
    Chart.js confirmed already bundled on the target OPNsense VM
    (`chart.umd.min.js`, same convention OPNsense core's own
    Diagnostics > Traffic page uses) -- no new frontend dependency.
  - Default retention lowered: `rollupHourlyRetentionDays` 45 -> 8,
    `rollupDailyRetentionDays` 730 -> 32 (`GoWithTheFlow.xml`'s
    `<Default>`, `general.xml`'s form `<hint>`, and
    `gowiththeflowd.py`'s `Config` dataclass fallbacks all kept in sync).
    `DbApiControllerBase::HOURLY_RETENTION_DAYS` -- a hardcoded PHP
    constant that decides whether History/Top Talkers read `rollup_hourly`
    or `rollup_daily` for a given day-range, meant to mirror this same
    setting -- was updated to 8 alongside it; missing that would have
    silently queried already-pruned hourly rows for any selection past 8
    days. The daily-prune job this retention setting feeds
    (`rollup.prune_hourly`/`prune_daily`) already runs on a schedule --
    it's a timer inside the daemon's own long-running loop
    (`gowiththeflowd.py`'s `last_daily_job`/`DAILY_JOB_INTERVAL_S`), not
    an OS-level cron entry, consistent with "the daemon is the only
    process" -- no separate scheduled job needed to be added. **Existing
    installs** (this VM, nostromo) keep whatever value is already saved
    in their `config.xml` -- an XML `<Default>` only applies to a field
    that's never been explicitly saved, so the new lower defaults only
    take effect for a fresh install; an existing install needs its
    Settings page value changed and re-applied manually if the new
    default is wanted there too.
  - Version bumped to **1.2.0** (minor, given the schema rewrite and the
    genuinely new charting feature, not just a bugfix).
- **1.2.1 -- real-box feedback from nostromo after upgrading to 1.2.0,
  four issues found and fixed.**
  1. **All search APIs 500'd after upgrading** (Live, History, Top
     Talkers) even though the History Overview chart worked fine.
     Root cause: `flows.db` wasn't deleted before the upgrade, so
     `CREATE TABLE IF NOT EXISTS` left the *old*-schema tables in place
     untouched; every query referencing the renamed `peer_*` columns
     failed, while the Overview chart's `timeseriesAction()` happened to
     only touch columns that existed under both schemas
     (`bucket_start`/`local_ip`/`bytes_in`/`bytes_out`), which is exactly
     why it alone kept working -- a useful diagnostic signal in hindsight.
     Not a code bug -- fixed by actually doing the documented step
     (`configctl gowiththeflow stop`, delete `flows.db`(+`-wal`/`-shm`),
     `configctl gowiththeflow start`) -- but underlines that this step is
     easy to forget in practice and has no automated guard.
  2. **Live Overview's Line/Stacked-Bar chart re-scaled its x-axis on
     every tick** instead of showing a fixed time window from the first
     draw, because `chartHistory` started empty and grew one point per
     poll until it reached `MAX_POINTS`. Fixed by seeding it with
     `MAX_POINTS - 1` empty placeholder points (spaced backward from now
     by the current refresh interval) the first time data arrives, so
     the chart is full-width from tick one.
  3. **A dominant host (the firewall's own admin-plane traffic) drowns
     out every other line**, making real per-device activity look flat
     by comparison -- an expected consequence of ranking by raw bytes,
     not a bug, but genuinely awkward in practice. Added shift-click on
     a legend entry to toggle that one line's visibility in place
     (tracked in our own `hiddenGroupKeys` set rather than relying on
     Chart.js's per-index legend state, since which keys land in the
     top-10 "top" set can shift between ticks); a plain click keeps
     jumping to the filtered Table tab, unchanged.
  4. **The Graph view didn't read as a network graph at all** -- real
     user reaction: "just shows a single line from a host to a remote
     host." The shipped renderer was a vertical list of host/bar/peer
     rows, not the bipartite node-link diagram the design actually
     called for (left column = local hosts, right column = peers, edges
     between them) -- an implementation shortcut that quietly diverged
     from the approved plan rather than a deliberate simplification.
     Rewritten as a real hand-drawn SVG node-link diagram matching the
     original spec: local hosts and peers as positioned nodes, edges
     sized by throughput, overflow beyond the top-10 pairs collapsed
     into edges toward one shared "Other" peer node (previously silently
     dropped), edges (not nodes, kept a deliberate simplification) fade
     out over a few seconds when a pair disappears rather than vanishing
     instantly.
  No schema/Python changes -- Volt/JS only. Version bumped to **1.2.1**.
- **1.2.2 -- "Last Activity", a real click-to-filter bug, and a full
  Graph-view redesign, all from continued real-box use.**
  1. **New `last_activity` column on `live_sessions`.** The user pointed
     out `last_seen` isn't actually useful for spotting a stale
     connection -- it bumps on every poll a session is still present in
     pf's own state table, regardless of whether any real traffic
     happened. Added a second, additive column that only advances when
     `bytes_in`/`bytes_out`/`state` actually change since the previous
     poll (a `CASE` inside the same `UPDATE`/`INSERT ... ON CONFLICT`
     statement, comparing against the row's own pre-update values --
     no separate read needed). Deliberately kept `last_seen` unchanged
     (still feeds History's `duration_s`/`ended_at`, which legitimately
     means "how long pf kept the state alive," idle tail included) --
     considered and rejected repurposing `last_seen` itself, since that
     would have silently changed what every closed session's duration
     means. Migrated via the same `ALTER TABLE` pattern the `category`
     column used, with a backfill from `last_seen` (not left at the
     placeholder 0/1970) since this one is `NOT NULL`. New "Last
     Activity" column on Live, next to the existing "Last Seen".
  2. **Real bug: the Overview chart's click-through never actually
     filtered anything.** `#grid-live` is ajax-backed, so Tabulator's own
     client-side `setFilter()` only filters whatever page of rows is
     already loaded locally -- it never asks the server for the real
     matching set, unlike History/Top Talkers' own filters. Replaced with
     the same server-side filter-param pattern those pages already use:
     `LiveController::searchAction()` gained optional `local_ip`/
     `peer_ip`/`peer_port` (exact-match, ANDed) and `host_ip` (matches
     *either* side -- needed because `live_sessions` is never
     canonicalized by role for a `peer_is_local` pair, so one IP can be
     `local_ip` in one session and `peer_ip` in another) params, wired
     through a `requestHandler` and a small "Filtered by X -- Clear"
     indicator shown above both tabs (since the filter also scopes the
     Overview chart while active, matching History's own shared-filter
     behavior).
  3. **Live Overview chart: y-axis showed -1..1 on first load.**
     Chart.js's linear-scale auto-range pads symmetrically around 0 when
     every visible point is still 0 (right after load, or an empty
     window) -- fixed with an explicit `min: 0` (byte counts are never
     negative). Same latent bug existed on History's Overview chart;
     fixed there too.
  4. **The Graph view didn't read as a network graph at all, then went
     through two more real-usage rounds before landing.** Round 1 (this
     entry's starting point) was a two-column node-link diagram --
     real feedback: "just shows a single line from a host to a remote
     host," and separately, described a materially different mental
     model of what should be nodes/edges. Rebuilt as a flat circular
     network graph per that description: every unique host (local or
     peer) is a node on one circle, one edge per (local host, peer,
     *destination port*) triple -- not collapsed by host+peer alone,
     since two different ports to the same peer are genuinely different
     things to look at; multiple edges between the same two nodes fan
     out via increasing bezier curvature instead of drawing on top of
     each other. Iterated twice more from there: edge color was first a
     relative-to-current-max gradient, changed to fixed absolute
     KB/MB bands with a legend (a relative scale made the same 50KB
     connection look "red" on a quiet network and "pale blue" on a busy
     one); a ctrl/cmd+click "solo one host" mode was built, then
     abandoned -- neither the click event's own `ctrlKey` nor a
     separately-tracked `keydown`/`keyup` fallback ever reliably reached
     the handler on the user's real Windows setup (something in the
     input stack was swallowing the modifier before either path saw it),
     and once a non-modifier icon-based alternative was floated the user
     said no to adding an icon at all, so the whole solo-mode idea was
     dropped rather than chasing it further. Final addition: a small
     arrowhead at each edge's midpoint pointing `local_ip -> peer_ip`
     (the side pf itself recorded as source), positioned using the
     property that a quadratic bezier's tangent at its midpoint is
     always parallel to the straight chord between its endpoints
     regardless of curvature -- so the rotation angle is a plain
     `atan2` on the two node positions, no real curve math needed.
     Edge opacity (recency, from `last_activity`) was kept from the
     prior round unchanged.
  Also caught and fixed along the way (nostromo, not the VM): after
  upgrading to 1.2.1, every search API 500'd except the History
  timeseries chart -- turned out to be operator error, not a code bug:
  `flows.db` wasn't deleted before the 1.2.0 upgrade, so the old-schema
  tables were still in place (`CREATE TABLE IF NOT EXISTS` is a no-op
  against them), and the timeseries chart alone kept working because its
  query happens to only touch columns that existed under both schemas.
  117 tests passing. Version bumped to **1.2.2**.
- **1.2.3 -- Graph view uncapped.** The user asked whether the Graph
  view limits how many hosts it shows -- yes, indirectly: edges were
  capped to the busiest `TOP_N` (10), and nodes were only ever the
  endpoints of surviving edges, so a host with no edge in the top 10
  didn't appear at all, and per-port edges (added in 1.2.2) made this
  worse by letting one busy host-peer pair eat several of those 10
  slots on its own. Asked whether to just raise the cap or guarantee
  each host at least one edge; the user's answer was neither -- show
  every host and every edge, no cap at all. Removed the `slice(0,
  TOP_N)` entirely (kept the sort, now only deciding curve-offset order
  for same-pair edges, not what's shown). The circle's radius is now
  driven by how many nodes actually need to fit (enough arc length per
  node to keep labels legible) rather than clamped to the wrapper's
  visible size -- the canvas grows and the wrapper scrolls (both axes
  now) instead of cramming a busy network into a fixed box. `TOP_N`
  remains unchanged for the unrelated Line/Bar chart's per-tick group
  cap. Version bumped to **1.2.3**.
- **1.2.4 -- Graph view rebuilt again as a real force-directed layout,
  then a canvas-sizing fix once real-box data made the space problem
  obvious.**
  1. The user pointed at Highcharts' own network-graph example image
     and asked for that look instead of the ring -- a genuine force-
     directed layout (nodes repel each other, edges pull connected
     nodes together like springs), not a fixed arrangement at all.
     Implemented a compact Fruchterman-Reingold-style simulation by
     hand (all-pairs repulsion + spring attraction along edges + a mild
     pull toward center so a sparse graph doesn't drift off-canvas),
     with node positions kept in a `forceNodePositions` map that
     persists across polls -- each tick only runs ~20 relaxation
     iterations starting from wherever nodes already are, so the layout
     gently resettles as edges/nodes come and go instead of jumping to
     a totally different arrangement every few seconds. O(n^2) per
     iteration from the repulsion pass is trivial at realistic host
     counts. One attraction spring per distinct (host, peer) *pair*,
     not per edge -- several destination-port edges to the same peer
     pulling on the same two nodes repeatedly would only distort the
     layout without adding information the per-edge color/opacity/arrow
     don't already carry. Node-label placement (which side of the node
     to put the text on) switched from angle-around-a-ring to simply
     "which side of the canvas center is this node currently on."
  2. Real-box use (nostromo, not the VM -- confirmed by actual device
     hostnames like `cam-garage`/`alexa-kitchen`/`iphone-mum` showing up
     clustered exactly as a force-directed layout should) immediately
     surfaced a real sizing bug: the canvas height was a fixed
     `width * 0.65` aspect ratio and the wrapper had a flat `max-height:
     560px` cap, so on a tall browser window most of the tab's actual
     available space sat empty below a needlessly small, clipped graph.
     Fixed by sizing the canvas to the real remaining viewport height
     (`window.innerHeight` minus the wrapper's own offset from the top
     of the page, floor 400px) first, then growing further on top of
     that floor only if enough nodes are present that even the full
     viewport isn't roomy enough -- letting the wrapper's own scrolling
     pick up whatever still doesn't fit, rather than a fixed number
     unrelated to the actual browser window.
  117 tests passing (Python side untouched -- Volt/JS only both times).
  Version bumped to **1.2.4**.
- **1.2.5 -- fixed a genuine runaway-growth bug in 1.2.4's sizing fix,
  found immediately on real-box use.** The Graph panel's legend was
  still landing off-screen, and scrolling down to see it made the panel
  even taller on the next poll, pushing the legend further down again
  -- compounding without bound until the tabs themselves scrolled off
  the top of the page. Root cause: the height calc used
  `getBoundingClientRect().top`, which is relative to the *current
  scroll position* -- scrolling down to reveal the clipped legend
  shrank that value, which grew the next tick's computed height, in an
  unbounded loop. Fixed by adding `window.scrollY` back to convert it
  into a scroll-independent distance from the top of the document (a
  layout fact that doesn't change as the user scrolls, only if the
  static content above the wrapper actually changes size). Also
  switched the svg-vs-legend split from independent JS pixel math to
  CSS flexbox (`#live-graph-wrapper` as a flex column, the legend
  `flex: 0 0 auto`, the svg `flex: 1 1 auto`) so the legend always gets
  its natural size and the svg gets whatever's left, rather than
  requiring the two calculations to be kept in sync by hand. One more
  real offset found after that: OPNsense's own page chrome has a
  `position: fixed` footer (`.page-foot`) that overlaps the bottom of
  the viewport regardless of scroll, which `window.innerHeight` alone
  doesn't account for -- measured its real height directly
  (`.getBoundingClientRect().height`) rather than guessing a constant.
  Version bumped to **1.2.5**.
- **1.2.6 -- Line/Bar chart got the same viewport-fill treatment.** The
  user noticed Live's Line/Bar chart still sat in a fixed 320px box
  while the Graph view now fills the tab -- a plain leftover, never
  revisited when the Graph view's sizing was fixed. Factored the 1.2.5
  sizing logic out into a shared `gwtfFillTabHeight(el)` helper and
  applied it to `#live-chart-canvas-wrapper` too (Chart.js's own
  `responsive`/`maintainAspectRatio: false` handling picks up the
  resulting container-height change on its own; an explicit
  `chart.resize()` call after `update()` makes it immediate rather than
  waiting on Chart.js's internal resize observer). The markup's fixed
  `height: 320px` became a `min-height: 320px` floor for the moment
  before JS first runs. Version bumped to **1.2.6**.
- **1.2.7 -- configurable Line/Bar range/Top N, then a real and
  significant bug found by a concrete real-box test.**
  1. Added user-configurable controls next to Live's Overview "Group
     by": **Range** (2/5/10/30 minutes -- previously a fixed ~60-point
     buffer whose real-time width silently depended on whatever poll
     interval happened to be selected) and **Top N** (5/10/20/0=All,
     0 meaning show every group with no cap). Persisted via
     localStorage, same convention as the existing interval dropdown.
     `chartHistory`'s length is now reconciled to
     `range_minutes * 60000 / poll_interval_ms` on every tick and
     immediately on a range/interval change (padding with empty
     placeholder points at the front when it needs to grow, trimming
     from the front when it needs to shrink), rather than a fixed
     `MAX_POINTS` constant. Ranking by total throughput *within the
     currently-displayed window* -- something the user separately
     asked about -- turned out to already be correct: `chartHistory`
     itself only ever holds the visible window's points, so summing
     over it for the Top-N ranking was never using a different (e.g.
     lifetime) measure to begin with.
  2. **Real bug, found via a real test**: running speedtest.net on a
     phone several times (large, genuinely dominant transfers) never
     made it appear on the Overview chart at all, even though the same
     traffic was immediately obvious on OPNsense's own Reporting >
     Traffic graph. Root cause: the chart was fed from the Table tab's
     own Bootgrid `responseHandler` -- but that's one *paginated* page
     of results (default page size 50), and `last_seen` bumps on every
     poll for every still-open session regardless of actual traffic,
     so on a network with more concurrent sessions than one page, which
     sessions land on page 1 vs. page 2+ is essentially arbitrary --
     not weighted toward high-traffic connections in any way. A
     dominant host's rows simply not being on the page the table
     happened to be showing meant its traffic was invisible to the
     chart, silently. Fixed with a dedicated `LiveController::
     overviewAction()` -- unpaginated, every currently-open session,
     only the handful of fields the chart/graph renderers actually
     read -- polled independently of the table's own ajax cycle (one
     extra request per tick; correctness here matters more than saving
     it). Same "don't derive a chart from a paginated table response"
     lesson History's `timeseriesAction()` already encoded; hadn't been
     applied to Live yet.
  117 tests passing (Python side untouched -- Volt/PHP only). Version
  bumped to **1.2.7**.
- **1.2.8 -- smoothed both Overview charts, then a real accuracy bug
  found underneath the "jaggy" complaint.** Switching Live's Line/Bar
  chart from tension-based curves to Chart.js's `cubicInterpolationMode:
  'monotone'` (smooths without letting the curve dip below or overshoot
  past neighboring points -- matters since the y-axis floor is pinned
  to 0) wasn't the real fix, per the user's own follow-up: the
  underlying per-tick values themselves were bouncing between a real
  throughput figure and a literal 0, not just rendered jaggedly.
  Traced to `updateLiveOverview()`'s delta computation: a row not seen
  on the previous poll always contributed 0 for that tick, even once
  the chart had a real, already-established baseline. For a workload
  that cycles through many short-lived connections -- confirmed with a
  multi-stream speedtest.net run on a phone -- this produced a literal
  throughput/0/throughput/0 pattern even though traffic never actually
  stopped. Fixed by only zeroing new rows on the very first tick a
  browser tab ever receives (previousSnapshot still empty -- these
  could be connections that have been open for hours, so charging their
  entire lifetime total to one tick would draw a meaningless spike);
  any row newly appearing after that point counts its full current
  total as this tick's delta, since it must have opened within roughly
  this poll interval. Applied the same interpolation-mode change to
  History's Overview chart too. One smaller residual gap not fixed:
  a connection that *closes* between two polls still has no "current"
  entry to diff against, so its own last partial interval is dropped --
  would need a connections_raw lookup for whatever just closed to fix
  properly; left as a known, smaller limitation for now.
  117 tests passing (Python side untouched -- Volt only). Version
  bumped to **1.2.8**.
- **1.2.9 -- root-caused the real shape behind "still jaggy" (a
  daemon/browser polling-rate mismatch), plus a log-scale toggle.**
  A real screenshot from nostromo (2-second browser refresh) showed the
  actual mechanism directly: two hosts' lines had sharp narrow
  spike-then-drop-to-zero shapes rather than smooth bursts.
  `gowiththeflowd.py` itself only polls pf every ~5 real seconds
  (`POLL_INTERVAL_S`) -- refreshing the browser faster than that can't
  produce genuinely higher-resolution data, it just re-reads
  byte-for-byte identical `live_sessions` rows 1-2 extra times before
  the real update lands. 1.2.7/1.2.8's delta fix correctly computed
  delta=0 for those identical-data reads (accurate, not a bug) -- but
  recording each of them as its own distinct chart point meant a real
  ~5-second update's full worth of bytes landed on whatever narrow
  2-second-wide x-axis slot it happened to arrive in, flanked by
  correctly-zero neighbors, which is indistinguishable on a chart from
  an actual sharp spike. Fixed by gating how often `updateLiveOverview`
  records a new point (and runs the delta computation at all) to at
  most once per `GWTF_DAEMON_POLL_INTERVAL_MS` (5000, matching the
  daemon's own constant) regardless of the browser's own refresh
  interval -- `gwtfLiveMaxPoints()`/`gwtfReconcileChartHistoryLength()`
  use this same effective interval too, so the requested Range still
  covers the right amount of *real* time. Table/Graph polling itself is
  untouched -- only the Line/Bar chart's own point-recording rate
  changed. Also added a Scale toggle (Linear/Logarithmic) next to Top N
  so one dominant spike doesn't flatten every other host's line to
  invisible -- Chart.js instantiates a concrete scale object per axis
  `type` at chart-creation time, so switching between linear and log on
  an already-running chart needed the instance destroyed and recreated
  rather than just mutating `options.scales.y.type` in place (which
  doesn't reliably take effect otherwise).
  117 tests passing (Python side untouched -- Volt only). Version
  bumped to **1.2.9**.
- **1.2.10 -- real security investigation on nostromo confirmed the
  bytes in/out pipeline is correct end-to-end, plus a real chart-color
  stability bug found along the way.** User spotted two alarming
  Live/Top-Peers numbers (a phone sending 485.9MB UDP to an unfamiliar
  host on port 88 having received only 25.6MB; a device sending 6.9GB
  to a Netflix Open Connect node having received only 60.5MB) and
  suspected the byte-direction mapping was reversed. Verified it isn't,
  with live evidence rather than just re-reading the code: ran a real
  file download and a real UDP DNS query on the test VM, in both cases
  confirming pf's raw `bytes_a:bytes_b` counters land correctly as
  small `bytes_out`/large `bytes_in` through `classify_sessions()`, and
  traced the field names unchanged (never swapped) through `db.py`,
  `rollup.py` (the one swap site, `_canonicalize_local_peer()`, is
  correctly gated to `peer_is_local=1` rows only), and
  `ToptalkersController::peerAction()`'s genuine-peer branch. User
  independently confirmed with `fast.com` that bytes in/out read
  correctly for a real Netflix flow -- the son's phone really was
  sending large amounts of data to an unfamiliar host, a genuine
  security finding, not a plugin bug.
  While investigating, confirmed and fixed a real, unrelated bug on
  Live's Overview Line/Bar chart: a host's line color was assigned by
  its array index in `topGroupKeysGWTF()`'s per-tick, throughput-sorted
  ranking, so a continuously-active host's color would change whenever
  the ranking reshuffled around it (e.g. a burst from another host
  temporarily outranking it) -- contradicting the reasonable
  expectation that a host keeps its color for as long as it's active.
  Fixed with a persistent `groupColorSlots` map (raw key -> palette
  index) reconciled every render against the tick's full key set (not
  just the rendered top N -- a host temporarily demoted to "Other"
  keeps its reserved slot and gets the same color back if it re-enters
  the top N), freeing a slot only when a key has no activity left
  anywhere in the window at all.
  117 tests passing (Python side untouched -- Volt only). Version
  bumped to **1.2.10**.
- **1.3.0 -- moved the Live Overview chart's data server-side
  (`live_ticks`), removing per-tab redundant polling and the need to
  approximate a backgrounded tab's missing history at all.** User asked
  directly: "would it be crazy to track the live data server-side...
  so multiple people aren't each polling the server" -- previously each
  open browser tab independently diffed its own poll of
  `/live/overview` and kept its own sliding-window buffer, so N open
  tabs meant N redundant re-derivations of the same numbers, and a
  reconnecting tab had no real history to recover (the Worker fix and
  averaged-fill fallback from 1.2.9/1.2.10-adjacent work were both
  covering for that gap, not fixing it). Now `gowiththeflowd.py`
  computes each tick's per-`(local_ip, peer_port)` delta once, server-
  side (`live_ticks.compute_tick_deltas()`, new pure module, 8 new
  tests), writes it to a new bounded `live_ticks` table (pruned every
  poll cycle, not just hourly, to ~35 minutes), and a new
  `LiveController::seriesAction()` endpoint serves it incrementally via
  a `since` watermark the Worker owns internally. A reconnecting/
  backgrounded tab now just fetches the real ticks it missed -- the
  gap-averaging logic from the previous entry is gone entirely, not
  just improved.
  A pre-implementation correctness review (this project's established
  practice before a design like this gets coded) caught a real bug:
  the original rule ("an `opened` snapshot's full cumulative bytes IS
  this tick's delta") is only true for a session that genuinely just
  opened, but `diff.opened` really means "not in the poller's previous
  set" -- true for every open connection on a cold table too (fresh
  install, or a schema-migration wipe), which would have charged an
  hours-old connection's entire lifetime total to one 5-second tick.
  Fixed by gating on pf's own reported `age_s`, mirroring the
  equivalent client-side guard this replaces, just decided per-session
  instead of table-wide. A second, pre-existing limitation (a session
  closing and a different one opening on the identical 5-tuple within
  one poll interval, producing a spurious clamped-to-zero delta) isn't
  new here -- the client's own code already had the same clamp -- so
  it's documented, not fixed.
  User also offered a simplification adopted here: the chart's range is
  now a fixed 30 minutes and its point spacing is fixed to the daemon's
  real tick rate (`POLL_INTERVAL_S`, staying at 5s), rather than
  approximated from the browser's own chosen poll rate -- removing the
  entire class of "effective interval" quantization drift discussed
  earlier (a 2s refresh landing on a 6s, not 5s, chart cadence). The
  "Refresh every" dropdown still exists but now only controls Table
  tab/Graph view freshness. Measured the real `pfctl -vvs state` +
  parse cost on the test VM (4-10ms at ~20-30 open states) before
  deciding whether `POLL_INTERVAL_S` should ever drop below 5s -- cheap
  enough on this lightly-loaded VM, but that cost scales with open
  connection count and hasn't been measured on a busier real network,
  so `POLL_INTERVAL_S` stays at 5 for now; a separate, later decision.
  Also deleted dead code found during the review: `renderLiveGraph()`'s
  second parameter (`deltasByGroup`/`lastDeltasByGroup`) was never
  actually read -- the Graph view colors/sizes edges from `/overview`'s
  cumulative bytes directly, not per-tick deltas.
  Small, separately-requested fix in the same release: the Live Table
  tab now defaults to sorting by Last Activity descending (was Last
  Seen, which bumps on every poll for every still-open session
  regardless of real traffic, making "most recently active" harder to
  spot at a glance).
  125 tests passing (117 + 8 new for `live_ticks.py`). Version bumped
  to **1.3.0**.
- **1.3.1 -- fixed a real packaging bug: `live_ticks.py` was never
  actually shipped in the 1.3.0 package.** `pkg-plist` is a manually
  maintained file list, not auto-generated from the source tree, and it
  never got a new line added for the new module -- so a genuinely fresh
  `pkg upgrade` extracted every OTHER 1.3.0 file correctly but simply
  never installed `live_ticks.py` at all. `gowiththeflowd.py`'s `import
  live_ticks` then failed with `ModuleNotFoundError` on daemon startup,
  which is a fatal, silent failure (Daemonize has no log file for a
  startup exception -- nothing at all in `/var/log`) -- the daemon just
  never started, `/live/series` 500'd with "Call to a member function
  bindValue() on false" (SQLite's `prepare()` returns false rather than
  throwing when the referenced table doesn't exist, since `live_ticks`
  itself is only ever created by the daemon's own `init_schema()`, which
  never ran), and -- worse than either error -- tracking had silently
  stopped entirely on nostromo.
  This shipped despite the test VM's own verification supposedly
  confirming `live_ticks.py` matched the committed source post-upgrade
  -- a real blind spot in the verification process itself, not just the
  code: earlier in this same session, `live_ticks.py` had been manually
  `scp`'d directly to the VM's installed path for a quick interactive
  test, *before* the official `build-pkg.sh` → `pkg upgrade` → diff
  cycle ever ran. Since `pkg` only manages files listed in `pkg-plist`,
  the real upgrade transaction simply left that leftover file
  untouched -- so the later `diff` against committed source reported a
  clean match for entirely the wrong reason, masking the omission
  instead of catching it. Root-caused live, working from nostromo's
  actual symptoms (`/overview` fine and clearly showing real hostnames
  across dozens of real devices, `/series` 500ing, `configctl ...
  status` reporting not running, then a direct foreground run of
  `gowiththeflowd.py` on nostromo surfacing the exact traceback) rather
  than guessing -- there's no SSH access to nostromo in this project, so
  every diagnostic step so far was a command handed to the user to run
  and paste back.
  Fixed: added the missing `pkg-plist` line, and diffed every git-
  tracked file under `src/opnsense` against `pkg-plist` to confirm this
  was the *only* omission (it was). For verification this time, the
  installed `live_ticks.py` is deleted from the VM *before* the real
  `pkg upgrade` test runs, specifically so a clean-state install is what
  gets proven, not whatever residue happens to already be sitting on
  disk from earlier interactive testing -- a discipline worth keeping
  for any future new file, not just this one.
  No code/logic changes -- 125 tests passing, unchanged. Version bumped
  to **1.3.1**.
- **1.4.0 -- DPI protocol/app classification (roadmap item #4, the last
  unbuilt piece of the original ZenArmor-inspired feature set), via
  nDPI's `ndpiReader` in periodic batch bursts.** Motivation:
  protocol/app identification independent of hostname/port (works for
  non-standard ports and obscure protocols), QUIC/HTTP-3 awareness (a
  real gap -- some unresolved port-443 entries the user had already
  noticed in real traffic are likely QUIC, which the DNS/SNI sniffer
  can't see at all), and some resilience to encrypted DNS (DoH), since
  DPI doesn't depend on seeing the plaintext DNS query.
  Feasibility research on the test VM found `ndpi` genuinely packaged
  for this OPNsense/FreeBSD build (`ndpi-5.0.d20251224`, from the
  OPNsense repo), shipping `ndpiReader` with a `-K json` output mode --
  but confirmed directly (polled its output file every 5s during a live
  25s capture, 0 lines at every checkpoint, all flows appearing only at
  process exit; `-m` "split duration" tried too, same result) that this
  output is **batch-only, not a live stream**, and there's no packaged
  `nDPIsrvd` (nDPI's own streaming daemon) in this port. Presented the
  resulting tradeoff directly: build a genuinely live classifier via
  custom `libndpi.so` bindings (a much bigger, riskier undertaking --
  nDPI's C API is a flow-struct/packet-feeding state machine, real
  ctypes/FFI memory-safety risk inside the daemon process, no existing
  pattern in this codebase to build from), or accept periodic batch
  classification as a slower enrichment alongside the existing PTR
  fallback. **User chose the batch approach.**
  Built as `dpi_classifier.py` (new module, mirrors `dns_sniffer.py`/
  `sni_sniffer.py`'s background-thread-with-callback shape): runs
  60-second `ndpiReader` bursts back-to-back forever, parses each
  burst's JSON-lines output (`parse_ndpi_output()`, pure and
  independently tested against a real captured JSON line from this
  session's own research plus synthetic edge cases -- 6 new tests),
  reorients onto (local, peer) the same way `pf_state_poller.
  classify_sessions()` does, and best-effort `UPDATE`s
  `live_sessions.dpi_protocol` by 5-tuple (a new nullable column, same
  unconstrained shape as `category`, added to all four tables via the
  same `ALTER TABLE` migration pattern `category`/`last_activity`
  already established). Carried through `connections_raw` (added to
  both write paths -- the normal session-close path in
  `db.record_diff()` and the long-lived-session hourly checkpoint in
  `rollup.checkpoint_long_lived_sessions()`, both of which read
  `live_sessions` and previously didn't select this new column at all)
  and into the rollup tables with its **own independent "most recent
  wins" rank**, deliberately not folded into the existing hostname/
  category rank -- since `dpi_protocol` is populated by a completely
  separate, differently-timed pathway, a row with a fresh classification
  but no hostname (or vice versa) must not blank out whichever of the
  two some other row in the same bucket already supplied.
  Accepted, documented limitations (not silently worked around): DPI
  lags real traffic by roughly one burst duration (not ~5s like
  everything else in this plugin); a long-lived connection can take
  several bursts, or never, to reach a confident classification, since
  each burst starts detection from scratch with no memory of earlier
  packets; a short-lived connection that closes between bursts may never
  get classified at all. `enable_dpi` defaults **off** (unlike DNS/SNI
  sniffing) pending real CPU/memory cost measurements on a busy network
  -- this plugin's first opt-in-only capability.
  Surfaced identically to how `category` already flows through the UI:
  a `dpi_protocol`/"Protocol" column on Live's Table, History, and Top
  Talkers' Top Peers grids, plus a new "By Protocol" tab in Top Talkers
  (`ToptalkersController::protocolAction()`, a near-verbatim copy of
  `categoryAction()`'s `GROUP BY` + `COALESCE(..., 'Unclassified')`
  shape) -- the "how much of my traffic is QUIC vs TLS vs HTTP vs DNS"
  view that's the actual motivating use case, independent of which
  domain traffic is going to.
  New hard package dependency on `ndpi` (net/ndpi), alongside the
  existing `py313-scapy` one -- `enableDpi` can't do anything useful
  without it installed, so it's pulled in automatically like scapy
  already is, rather than needing a separate manual `pkg install ndpi`
  step to make the toggle actually work.
  131 tests passing (125 + 6 new for `dpi_classifier.py`). Version
  bumped to **1.4.0**.
- **1.4.1 -- fixed a real crash-on-first-burst bug in the DPI capture
  thread, found live on nostromo, plus the observability gap that made
  it nearly undiagnosable.** User enabled DPI on nostromo and the
  Protocol column stayed empty. Ruled out, in order, with real evidence
  at each step rather than guesswork: the daemon not restarting after
  the Settings save (it had); `ndpiReader` itself (ran it manually with
  the real interfaces and real traffic -- worked perfectly, correctly
  classified TLS/QUIC/WireGuard/STUN/DNS flows); the `rctl`
  cpu/memory caps (temporarily raised the memory limit to 1024MB,
  no change); a stale pidfile left over from an unrelated prior crash
  (found and cleared -- `ps aux` showed no process at all, but
  `/var/run/gowiththeflow.pid` still had a dead PID in it, so `start`/
  a manual foreground run both refused, thinking it was already
  running). None of those were it.
  The real bug: `parse_ndpi_output()` already guarded `src_ip`/
  `dest_ip` against a missing key, but the `src_port`/`dst_port` lookups
  a few lines below were bare, unguarded dict indexing -- a real
  classified flow nDPI reports without ports at all (e.g. ICMP) raised
  an uncaught `KeyError`, which propagated out of `capture_loop()`'s
  original zero-error-handling `while True` and killed the whole
  background thread permanently, silently, after its *first* successful
  60-second burst. This explains every earlier-ruled-out symptom at
  once: `ndpiReader` genuinely did run once (hence working fine when
  tested manually, and hence `enable_dpi`/interfaces/rctl all checking
  out clean), but every later check happened after the thread had
  already died, so no second burst ever started.
  Diagnosing this took an extra turn because `Daemonize` (the OPNsense
  helper this project's rc.d script uses) unconditionally redirects
  stdin/stdout/stderr to `/dev/null` -- so even running the daemon
  directly in the foreground taught us nothing (it double-forks and the
  foreground process exits almost instantly regardless of what happens
  next, by design), and the crashed thread's traceback had nowhere to
  go at all. Fixed the crash (extend the existing try/except to also
  cover `src_port`/`dst_port`, skip a portless flow the same way
  `pf_state_poller.classify_sessions()` already skips a portless pf
  state) **and** the observability gap: `capture_loop()` now catches
  broad exceptions per burst and logs via `syslog` (visible through
  OPNsense's own log viewer) instead of dying silently, with a
  burst-duration backoff so a persistent failure logs steadily rather
  than flooding syslog in a tight crash loop; `run_capture_burst()`
  also now logs a non-zero `ndpiReader` exit it previously discarded
  entirely. `syslog` is POSIX-only, so the module does a defensive
  `try/except ImportError` around it to stay importable in this
  project's own (Windows) test environment.
  132 tests passing (131 + 1 new, pinning the portless-flow case against
  a synthetic ICMP-shaped record). Version bumped to **1.4.1**.
- **1.5.0 -- new "Top Talkers" tab on the Live page**, per-local-host
  bytes in/out/total/connections both "since refresh" (the server's
  real ~5s tick) and over the same 30-minute window as the Overview
  chart; clicking a row filters the existing Table tab to that host
  (`filterLiveTableByLocalHostGWTF()`, unconditionally by `local_ip` --
  unlike `filterLiveTableByGroupGWTF()`, not affected by the chart's own
  local_ip/peer_port grouping toggle).
  Entirely client-side, reusing data the Worker already fetches -- no
  new API endpoint. "Since refresh" is rebuilt fresh from whatever raw
  `live/series` ticks just arrived (`sinceRefreshByHost`); the 30-minute
  window sums a new `hostWindowHistory` (mirrors `chartHistory`'s
  per-tick bucketing, but always keyed by `local_ip` regardless of the
  chart's toggle, since Top Talkers needs a per-host breakdown
  unconditionally). "Connections (30 min)" was originally scoped as
  distinct peer ports (all `live_ticks` retains -- no `peer_ip`), then
  changed to a genuine distinct-connection count per user feedback:
  tracked via a self-pruning `local_ip -> Map<row_id, lastSeenMs>`
  (`hostConnSeenTimes`) fed from `/live/overview`'s full 5-tuple
  `row_id` each poll, ages out entries older than the window on every
  update so a host that's gone quiet still empties out after 30 minutes
  rather than staying stuck.
  The new grid uses the `UIBootgrid` wrapper's local (non-ajax) data
  mode (`ajax: false` -- confirmed by reading the wrapper source that
  this switches Tabulator to local sort/filter/paginate) rather than a
  server search endpoint, since the rows are entirely computed
  client-side; pushed in via `setData()` on the same cadence as the
  chart, instead of an ajax poll.
  Getting the default sort (ascending by `window_bytes_total`, per
  explicit user preference -- descending was the first request, then
  reconsidered) right took three real, confirmed-live iterations, each
  ruling out the previous theory with actual evidence rather than
  guesswork: (1) suspected Tabulator's own sort *persistence*
  (`tabulatorDefaults()` sets `persistence: {sort: true}` project-wide,
  writing a user's last sort choice to `localStorage` and restoring it
  on rebuild) -- disabled `sort` persistence for just this grid via the
  wrapper's `tabulatorOptions` override (confirmed via source: `{
  ...tabulatorDefaults(), ...compatOptions, ...tabulatorOptions }`, so a
  caller's `tabulatorOptions` wins), which was a real, worth-keeping fix
  but didn't fully resolve the symptom. (2) Switched to Tabulator's
  declarative `initialSort` option instead of an imperative
  `table.setSort(...)` call in a `tableBuilt` handler -- the imperative
  version was proven dead on arrival: the user manually clicking the
  header for the first time produced the normal from-nothing toggle
  (ascending, then descending on a second click), meaning no sort
  whatsoever had actually been in effect beforehand, not merely the
  wrong direction. `initialSort` didn't fully stick either, once
  `setData()` started replacing rows every tick. (3) Actual fix: force
  `table.setSort(...)` explicitly exactly once, chained onto the first
  real `setData()` call's own returned promise (so it can't race the
  data actually landing) and gated behind a flag so it never re-fires
  and fights a user's own later manual re-sort.
  No Python changes -- 132 tests passing, unchanged. Version bumped to
  **1.5.0**.
- **1.5.1 -- two more real, confirmed-live Top Talkers sort bugs, found
  right after 1.5.0 shipped.** (1) The "exactly once, gated behind a
  flag" fix from 1.5.0 turned out not to be enough on its own: forcing
  `setSort()` once after the *first* `setData()` visibly stuck in the
  header's sort icon but stopped actually ordering rows as soon as the
  *next* tick's `setData()` replaced the row set -- Tabulator does not
  keep re-applying an active sort across this table's repeated
  `setData()` calls. Fixed by reading back the table's current sorters
  (`table.getSorters()`) before every single `setData()`, falling back
  to `window_bytes_total`/asc only if none is set, and re-forcing that
  same sort after *every* tick, not just the first -- this also means a
  user's own manual re-sort now survives ticks, since it becomes
  "current" and gets carried forward, rather than only the one
  hardcoded default ever being protected. (2) Once that was actually
  sticking, the user immediately spotted a second, different bug: "Total
  (30 min)" was sorting lexicographically ("38.7 MB" before "5.6 MB"),
  not by value -- while the grid's other byte/count columns sorted
  correctly. Root cause: `data-type="numeric"` only selects a cell
  *formatter* in `opnsense_bootgrid.js`'s column parsing, never a real
  Tabulator `sorter` -- every other column here is only ever sorted by a
  genuine user header click, by which point Tabulator has real numeric
  data to auto-type against, but `window_bytes_total` is also sorted
  *programmatically*, and doing so the moment the very first tick's data
  lands raced Tabulator's own type auto-detection and locked the column
  into a string sorter. Fixed by explicitly forcing `sorter: "number"`
  via `column.updateDefinition(...)` on every byte/count column in this
  grid once the table builds, rather than leaving any of them to
  auto-detection guesswork.
  No Python changes -- 132 tests passing, unchanged. Version bumped to
  **1.5.1**.
- **1.5.2 -- client-side performance fix for the Live page, after the
  user reported it getting noticeably slow (and, on nostromo under real
  traffic, DevTools' own Performance panel choking on the recorded
  trace) after leaving Live -> Top Talkers open for a few minutes.**
  Chrome's own "Forced reflow"/"Recalculate Style" violations pointed at
  three real, independent causes, all in `live.volt`'s ~5s poll handler:
  (1) `renderLiveChart()`/`renderLiveGraph()` (the Overview tab's
  chart/graph) ran their full Chart.js `.update()`/`.resize()` plus a
  layout-reading helper (`gwtfFillTabHeight()`, two `getBoundingClientRect()`
  calls + a style write) on *every* tick regardless of which tab was
  actually on screen -- wasted work, invisible to the user, whenever
  they were sitting on Top Talkers or Table instead. Both now skip via a
  new `gwtfIsTabPaneActive()` check, with `shown.bs.tab` handlers on
  Overview/Top Talkers forcing one immediate re-render from already-cached
  data the moment either tab becomes visible again (mirroring the
  existing Graph-view-switch pattern), so switching tabs never shows
  data staler than the last ~5s tick. (2) Top Talkers itself replaced
  its *entire* table via `table.setData(rows)` every tick, even for
  hosts whose numbers hadn't changed at all -- forcing a full style
  recalc/layout pass across every row, and scaling directly with
  distinct-host count (explaining why nostromo's real traffic hit this
  far harder than the quiet test VM). Switched to Tabulator's
  `updateOrAddData()` (only touches changed rows) plus explicit
  `deleteRow()` calls for hosts that aged out (which `updateOrAddData()`
  never removes on its own). Making that switch surfaced a real latent
  bug: this grid's `row_id` was never actually wired into Tabulator's
  internal row index -- confirmed by reading the wrapper source
  (`opnsense_bootgrid.js`) that the real index field is
  `this.options.datakey`, silently defaulting to a nonexistent `uuid`
  field, harmless under the old full-`setData()` approach (which never
  needed a working index) but would have collided every row onto the
  same undefined index under the new incremental one. Fixed by passing
  `datakey: 'row_id'` explicitly at the grid's init. (3) The "Open
  Conns" column recomputed a full re-filter of all open connections once
  per host (O(hosts * connections)) instead of a single pass building a
  per-host count map (O(hosts + connections)) -- also scales with real
  traffic in a way the test VM never surfaced.
  No Python changes -- 132 tests passing, unchanged. Version bumped to
  **1.5.2**.
- **1.5.3 -- a real Performance recording after 1.5.2 shipped showed the
  1.5.2 fixes hadn't actually removed the dominant per-tick cost.** Both
  call stacks bottomed out in the same place: `Row.js calcHeight() ->
  calcMaxHeight() -> Cell.js getHeight() -> offsetHeight` -- Tabulator's
  own dynamic row-height re-measurement, a forced synchronous layout
  read, running twice per tick: once per row inside `updateOrAddData()`
  (83 of a 112ms task), and again as a full-table sweep right after (32
  of a 40ms follow-up task). This is Tabulator's own built-in
  bookkeeping, unrelated to 1.5.2's changes -- it runs regardless of
  whether a row's content actually changed. Confirmed via Tabulator's
  own bundled source (`tabulator.min.js`) that setting a fixed
  `rowHeight` option skips this measuring chain entirely (`this.height
  = rowHeight` instead of measuring). Set to `28` -- computed, not
  guessed, from this table's real rendered cell metrics (`opnsense-
  bootgrid.css`/`tabulator.min.css`: 4px cell padding, 15px font-size,
  1.2 line-height, 1px border).
  Verifying this required first working around an unrelated hiccup: the
  test VM had rebooted mid-session (explaining an earlier SSH outage)
  and the daemon hadn't come back -- traced to a real, separate gap
  (this plugin has no boot-time auto-start wiring at all; the daemon
  only ever starts via the pkg post-install script's one-time `onestart`
  or a manual `configctl` call, so a plain reboot with no package
  operation -- exactly what nostromo will eventually see -- leaves it
  stopped indefinitely). Deliberately not fixed as part of this release;
  flagged for its own follow-up.
  No Python changes -- 132 tests passing, unchanged. Version bumped to
  **1.5.3**.
- **1.5.4 -- Top Talkers follow-ups from real production (nostromo)
  testing.** (1) Default sort flipped back to descending on
  `window_bytes_total` (the 1.5.0 "ascending" preference reversed again
  per direct user request). (2) The "since refresh" byte columns were
  too volatile at a live 5s cadence -- a single quiet or spiky tick swung
  the number with no sense of a sustained rate -- replaced with a
  trailing 1-minute moving-window sum (`GWTF_TOPTALKERS_RATE_WINDOW_MS`),
  computed by filtering `hostWindowHistory`'s existing real per-tick
  bucket timestamps rather than tracking a separate buffer; fields
  renamed `refresh_bytes_*` -> `min1_bytes_*` and headers to "(1 min)"
  since the old names no longer described what they held. (3) A real
  Performance recording on nostromo (45 real hosts) showed
  `updateOrAddData()` doesn't skip a row whose values are unchanged --
  confirmed by reading Tabulator's own bundled source that it runs the
  full per-cell height/layout bookkeeping (`Cell.setHeight()`, an
  unconditional `offsetHeight` read) on every row it's given regardless.
  Fixed by diffing against a new `lastToptalkersRowValues` cache
  ourselves and only pushing rows that actually changed -- a quiet host
  costs nothing on a tick where nothing moved.
  A user-supplied AI-generated (Gemini, via Chrome DevTools' "Ask AI")
  second-opinion suggested further fixes; each was checked against this
  project's real bundled Tabulator source rather than trusted at face
  value. `blockRedraw()`/`restoreRedraw()` were confirmed real and
  adopted -- wrapping the stale-host `deleteRow()` (now one array call
  instead of one call per host, also confirmed via source) and
  `updateOrAddData()` together so a tick with both produces exactly one
  consolidated render pass. `renderVertical: "virtual"` was rejected:
  the wrapper's own source deliberately defaults virtual DOM *off*
  ("pages where we expect this will speed up rendering a lot, i.e. log
  pages") and `scrollBarCheck()` (the AI's flagged hotspot) was confirmed
  to run after every render regardless of virtual/basic mode -- not
  virtual-DOM-specific, and not a fit for this table's small, bounded
  row count. "Batch updates instead of row-by-row" was already true of
  this code (one `updateOrAddData()` call per tick, not per message).
  No Python changes -- 132 tests passing, unchanged. Version bumped to
  **1.5.4**.
- **1.5.5 -- two Top Talkers layout bugs found live on nostromo right
  after 1.5.4 shipped, both from the same root cause.** This grid is
  constructed while its own tab-pane is still hidden (`display:none`,
  since Overview is the default active tab) -- Tabulator lays out a
  table against a zero-size container. (1) The header rendered nearly
  collapsed (a few pixels of text) the first time the tab was shown
  after a fresh page load; the wrapper's own built-in
  `IntersectionObserver` (`opnsense_bootgrid.js`) already calls a plain
  `redraw()` when a grid becomes visible, but that wasn't enough to
  recover a header laid out against zero width. Fixed by calling
  `table.redraw(true)` (confirmed via Tabulator's bundled source that
  `force=true` fully recomputes both column and row layout) in the
  `#live-toptalkers` tab's `shown.bs.tab` handler. (2) That same
  hidden-at-construction state meant `initialSort`'s effect on the
  header's sort-arrow icon didn't render either, and
  `renderLiveTopTalkers()` only calls `setSort()` when there's actual
  data to push -- switching to the tab before the very first tick had
  delivered any rows left the arrow missing until that tick landed.
  Fixed by forcing the sort explicitly in the same handler, wrapped
  defensively (see below).
  Chasing (1) surfaced a real, uncaught exception from 1.5.4's own
  `blockRedraw()`/`restoreRedraw()` change: `restoreRedraw()`
  internally re-applies the current sort as part of its own recovery,
  which threw inside Tabulator's bundled `Sort.js` (`setColumnHeader()`,
  `e.getElement(...).setAttribute is not a function`) specifically while
  the header element was still in that half-initialized state --
  confirmed via the real browser stack trace the user captured on
  nostromo. Since the actual fix is (1) above (a properly laid-out
  header no longer hits this), the `.catch()`/`try`-`catch` added around
  both sort-forcing call sites is defense-in-depth, not the real fix --
  it just keeps a residual Tabulator-internal hiccup from surfacing as
  an uncaught rejection instead of self-healing silently.
  No Python changes -- 132 tests passing, unchanged. Version bumped to
  **1.5.5**.
- **1.5.6 -- two independent fixes reported after 1.5.5 shipped.**
  (1) Top Talkers > Uncategorized Hosts was taking ~2 minutes per page
  on nostromo's real dataset (8033 hosts, 2282 uncategorized), with low
  CPU load throughout -- an I/O-bound signature, not a compute-bound
  one. `ToptalkersController::uncategorizedAction()`'s query filters/
  sorts a correlated subquery by `(peer_hostname, bucket_start)` with no
  index covering it at all, forcing a full table scan per distinct
  hostname; the sibling `peerAction()` does the same kind of lookup
  keyed on `peer_ip`, which *is* indexed (`idx_ru_d_peer_recency`),
  which is why that one was never slow. Added
  `idx_ru_d_hostname_recency`/`idx_ru_h_hostname_recency` on
  `(peer_hostname, bucket_start)`, mirroring the existing peer index
  exactly -- same fix shape as the original `idx_ru_*_peer_recency`
  indexes' own "7.5s -> 0.089s" precedent. Verified with a synthetic
  dataset matching nostromo's reported scale: 9.6s -> 0.078s for the
  identical query, same row count returned. Plain `CREATE INDEX IF NOT
  EXISTS` additions need no migration handling (unlike a new column) --
  they take effect on the next daemon restart against an existing,
  populated database.
  (2) Row hover highlighting requested for the grids generally.
  OPNsense's own theme CSS (`opnsense-bootgrid.css`) turned out to
  already have a `:hover` rule for Tabulator rows, but it's gated on
  `.tabulator-selectable` (only present when a grid has row selection
  enabled -- none of this plugin's grids do) and even then re-asserts
  the exact same background color as the row's resting state, so it's a
  no-op regardless of selection. Added an explicit override (a
  translucent white overlay rather than a hardcoded color, so it holds
  up under any theme) to `live.volt`/`history.volt`/`toptalkers.volt`.
  No Python test changes -- 132 tests passing, unchanged (schema-only
  addition, no new logic to cover). Version bumped to **1.5.6**.
- **1.5.7 -- first real pass at `manual_categories.py`'s `OVERRIDES`**,
  which had sat empty since it was introduced -- populated from a real
  nostromo export of the Top Talkers "Uncategorized Hosts" tab (2282
  hostnames), exactly the workflow its own docstring describes. Grouped
  into buckets safe to categorize by domain suffix alone (Network
  Infrastructure -- DNS root/TLD/registry servers, third-party DNS
  providers, Microsoft's Azure Traffic Manager `*-msedge.net`
  nameservers, NTP; Smart Home/IoT -- TP-Link/Tapo, Wyze, Ring, Tuya,
  Firewalla, Hue, Ecobee, Garmin, Synology, Samsung health/cloud;
  Gaming -- Star Citizen, Photon, several mobile-game backends; Cloud
  Infrastructure -- Sky's own ISP-embedded CDN cache nodes; Communication
  -- WebRTC/STUN signalling; Peer-to-Peer -- well-known UK residential-
  broadband reverse-DNS). Measured against a large sample of the real
  export: 312 of 2140 sampled hosts (14.6%) now resolve.
  Deliberately NOT attempted: reverse-DNS PTR hosts under a generic VPS
  provider (`*.ip.linodeusercontent.com`, `datapacket.com`, ...) -- the
  hosting provider says nothing about what's actually running there, so
  a suffix rule would be a guess, not a fact, unlike the buckets above
  where the suffix itself is a reliable single-purpose signal. Also
  skipped as a first-pass scope decision (not because they're unsafe):
  general retail/news/finance company domains -- some, like bbc.co.uk,
  genuinely span more than one category (News, Streaming/iPlayer,
  Music/Sounds) and would need real per-subdomain judgment rather than
  one bulk rule.
  Worth noting for whoever deploys this: categorization happens when a
  hostname is first resolved, not as a backfill job -- this won't
  retroactively shrink the Uncategorized count for already-stored
  history, only for new traffic going forward (old uncategorized rows
  age out via the existing retention windows).
  New test (`test_real_overrides_spot_check`) spot-checks a handful of
  the real entries. 133 tests passing. Version bumped to **1.5.7**.
- **1.5.8 -- new "DNS Queries" page**: what's actually being queried
  over DNS and what came back, including failed lookups (NXDOMAIN/
  SERVFAIL/etc.) and every answer record type (CNAME, TXT, ...), not
  just the A/AAAA-only hostname cache `dns_sniffer.py` already built.
  Its own standalone `Reporting` page (not a Top Talkers tab, decided
  with the user) -- every existing Top Talkers/History table is a
  `GROUP BY`/`SUM` byte-ranking aggregate; this is a raw activity log
  with no byte counts at all. `enable_dns_query_log` defaults **on**
  (unlike DPI) since it rides the already-running DNS sniffer thread
  rather than adding new capture cost.
  New `dns_sniffer.extract_query_events()` reuses the same re-parsed
  packet `extract_observations()` already dissects (one `sniff_loop()`
  callback feeds both). Written to a new `dns_query_log` table via an
  **hourly-bucketed upsert**, not one row per query -- DNS lookups
  happen far more often than actual connections, so row growth is
  bounded by distinct (host, query, type) combinations per hour, not
  raw query frequency (confirmed live: a real repeat lookup collapsed
  into one row with `count` incrementing, exactly as designed).
  `rollup.prune_daily()`'s existing `table=` param reuses unmodified for
  retention (`dns_query_log_retention_days`, default 7) -- no new rollup
  job needed. `ToptalkersController`'s pattern of fully materializing
  results into a PHP array before paging (no SQL push-down anywhere in
  this codebase) meant this needed an explicit hard row cap
  (`Api/DnsqueriesController::MAX_ROWS = 2000`) that no prior grid in
  this plugin has needed, since this is the first one backed by a table
  that isn't aggregation-bounded.
  **Real bug found and fixed during VM verification, not specific to
  this feature**: a brand-new Settings field added to an
  *already-configured* plugin instance rendered as empty (or its
  falsy-default value, regardless of the XML schema's declared
  `<Default>`) via `configctl template reload`, until the Settings form
  is saved once through the real GUI -- confirmed directly by
  instantiating the model in standalone PHP (`new \OPNsense\
  GoWithTheFlow\GoWithTheFlow()`), which *did* show the correct default,
  proving the gap is in `configctl template reload`'s own rendering path
  not hydrating a full model object, not in the model/schema layer
  itself. This exact same latent gap already existed for
  `enableDnsSniffing`/`enableSniSniffing`/`enablePtrFallback` (all
  declared default-on) -- it was invisible there only because their
  config.xml nodes have existed since this plugin's very first schema
  version, and never went uncreated on a real box the way a *newly
  added* field does. Fixed for `enable_dns_query_log`/
  `dns_query_log_retention_days` specifically by making the Jinja
  template itself defensive (`{{ 0 if ... == '0' else 1 }}` and
  `{{ ... or 7 }}`), the same defensive-fallback pattern this template
  already relied on elsewhere (`captureInterfaces or ''`, etc.) for
  exactly this reason -- not fixed for the three pre-existing fields,
  since they've never actually hit it in practice; worth remembering as
  the correct pattern for *any* future new Settings field on this
  project, regardless of its default.
  New tests: `extract_query_events()` (9 cases: success, NXDOMAIN,
  SERVFAIL, non-A query types, mixed-type answer chains, truncation of a
  pathological answer count, queries-not-responses, non-DNS packets,
  IPv6) and `record_dns_query_event()` (5 cases: fresh insert, same-
  bucket repeat increments count, different bucket/query/type/host are
  separate rows). 147 tests passing.
  Verified against real capture on the test VM: real NOERROR and a
  forced real NXDOMAIN both captured correctly with the right rcode;
  toggling `enable_dns_query_log` off confirmed to stop new rows while
  `ip_hostname_cache` kept updating in real time (proving the DNS
  sniffer thread itself is unaffected); the exact controller SQL run
  directly against the live database returned correct, real-shaped rows
  (including a real `nostromo.internal` NXDOMAIN worth investigating
  separately). Page/menu/grid rendering itself wasn't visually confirmed
  in a real browser as part of this pass.
  **Real bug found by the user on first look at real data, fixed same
  day**: the same (host, query, type) recurring across many hours showed
  up as one row per hour bucket -- correct per the storage design (dedup
  is only ever within one hour, deliberately, to keep write volume
  bounded), but looked exactly like duplicates over a multi-day `days`
  window (confirmed live: `accounts.google.com` from one host appeared
  as 6 separate rows across 6 hours, each already correctly deduped
  within its own hour). Fixed at the display layer, not storage:
  `Api/DnsqueriesController::searchAction()` now aggregates across every
  bucket in the selected window (`GROUP BY local_ip, query_name,
  query_type`, `SUM(count)`, `MAX(last_seen)`), collapsing back to one
  row per (host, query, type) -- relying on SQLite's specific guarantee
  that non-aggregated columns in a query containing `MAX()` come from
  the same row that produced the max value, so the displayed
  `rcode`/`answers` are genuinely the most recent, not an arbitrary
  bucket's. Confirmed live: the 6-row `accounts.google.com` case (counts
  1/6/6/6/6/3) collapsed to one row with `count: 28` and the correct
  latest answer.
- **1.6.0 -- "block a host"**: a per-row block icon on Live's Top Talkers
  and Table tabs that stops all traffic to/from that row's local device
  (both directions, existing connections killed immediately, not just new
  ones), plus a new History > Blocked tab to view/unblock. This is the
  original motivating real-world case from the "Not yet started" note
  below (catching a kid's gaming device active late at night) -- a
  manual, permanent-until-unblocked action, deliberately distinct from
  the still-unbuilt scheduled/time-of-day version of that idea.
  Decided with the user: the block is deliberately **total** -- the rule
  sits *above* OPNsense's own anti-lockout allow rule, so a blocked host
  loses access to the firewall's own GUI/SSH too (blocking your own
  current browsing IP, or the firewall's own addresses, is refused
  outright); and a blocked host still gets DHCP lease renewals, so it
  stays pinned to the blocked IP instead of lapsing onto a new,
  unblocked one.
  Mechanism resolved by reading `OPNsense\Firewall\Plugin.php` in full
  and the live compiled ruleset (`/tmp/rules.debug` has zero `anchor`
  call points anywhere, ruling out an independently `pfctl`-loaded
  anchor -- it would never actually be evaluated): a new
  `etc/inc/plugins.inc.d/gowiththeflow.inc` defining
  `gowiththeflow_firewall($fw)`, using `Plugin::registerTable()` +
  `registerFilterRule()` -- the same native shape core's own "overload
  table" feature (`virusprot`/`sshlockout`) already proves works in this
  OPNsense version. The table is `file`-backed
  (`/var/db/gowiththeflow/blocked_hosts.tbl`, one IP per line, atomic
  writes), not a bare `persist` table, so a block is live from the very
  first ruleset load at boot and immune to `update_tables.py`'s
  Alias-replay wipe on an unrelated Firewall > Apply (confirmed: that
  replay logic only ever touches tables derived from
  `OPNsense\Firewall\Alias`). Confirmed live: the compiled rules matched
  exactly, and a real filter reload did not wipe a test block.
  New `blocklist.py` (pure pf/DB primitives) + `block_host.py` (CLI,
  three new configd actions: `block`/`unblock`/`sync_blocked`) --
  `pfctl -t gowiththeflow_blocked -T replace` to sync the table,
  `pfctl -k` (two passes, source and destination) to kill existing
  states. New `blocked_hosts` SQLite table (source of truth; the pf
  table/file are always derived from it, so drift self-heals) and
  `Api/BlockedController.php` (PHP only ever reads via `openDb()` and
  mutates through `configdpRun()`, same split as everywhere else in this
  project).
  **Real bug found and fixed**: `OPNsense\Core\Backend::configdpRun()`
  returns an *empty string*, not the script's real stdout, when the
  underlying configd-invoked process exits non-zero -- confirmed by
  instantiating `Backend` directly in a standalone PHP test script and
  calling it against a deliberately-failing case. This would have
  silently discarded the one error detail (e.g. "refusing to block one
  of the firewall's own addresses") a user actually needs to see, so
  `block_host.py` now always exits 0, encoding success/failure only in
  its JSON `status` field.
  New `test_blocklist.py` (35 tests: IP normalization incl. Python's
  `ipaddress` deliberately rejecting ambiguous leading-zero octets,
  own-address parsing against a real captured `ifconfig -a` fixture,
  table-file rendering/atomicity, DB CRUD, `pfctl`/state-kill argv).
  188 tests passing.
  **Real bug found on a genuine cold reboot of the test VM (not caught by
  any prior verification, since every earlier check either used
  `onestart` directly or `pkg install`'s own `post-install`, both of
  which bypass it): `gowiththeflowd.py` never actually auto-started at
  boot.** Its rc.d script is gated by the standard FreeBSD
  `gowiththeflow_enable` rc.conf variable, but this plugin only ever
  templated its own `config.json` (the daemon's runtime config), never a
  `rc.conf.d` file to populate that gate from the Settings "enabled"
  checkbox -- confirmed by checking a real installed reference plugin
  (`os-netflow`'s own `service/templates/OPNsense/Netflow/rc.conf.d`,
  rendering `netflow_enable`) uses exactly this mechanism, and that
  `/etc/rc.conf.d/gowiththeflow` didn't exist at all after a fresh boot.
  Fixed with a new `service/templates/OPNsense/GoWithTheFlow/rc.conf.d`
  template (`gowiththeflow_enable` from `GoWithTheFlow.general.enabled`)
  registered in `+TARGETS`. Verified with an actual `shutdown -r now` on
  the test VM, not just a template-reload check: the daemon was running,
  unprompted, 33 seconds after boot.
  **Second real bug, found while trying to test an actual block**: a
  local host's own subnet broadcast address (e.g. `10.0.0.255` for
  `10.0.0.0/24`) can genuinely show up as a session's `local_ip` --
  broadcast traffic gets classified the same as any other local<->local
  pf state (see `pf_state_poller.classify_sessions()`) -- so it appeared
  as a seemingly-real "host" in Top Talkers, blockable like any other.
  It isn't a device; blocking it would be meaningless at best. Added
  `blocklist.is_subnet_edge_address()` (checks a subnet's network/
  broadcast address, skipping /31 and /32 which have neither per RFC
  3021) and wired it into `block_host.py`'s existing own-address refusal
  path. Compounding this, both refusals (the firewall's own address and
  this new one) were failing **silently** in the UI -- `live.volt`'s
  block/unblock handlers never checked the response `status`, so a
  refusal produced no visible feedback at all, which is what actually
  made this confusing to test. Fixed in both `live.volt` and
  `history.volt` to show a `stdDialogInform` error with the real reason
  on any non-`ok` response.
- **1.6.1 -- real bug: bytes in/out swapped for some sessions, found by
  the user on nostromo** (a phone downloading a 5.6GB Ubuntu ISO showed
  ~1GB "out" and 12.6MB "in" -- backwards). Root-caused with real data
  the user captured directly (`pfctl -s states -vv`, two blocks for the
  same NAT'd connection from its two per-interface views): pf's "N:N"
  byte/pkt pair is fixed to a real direction, but which of the two
  numbers is "the printed src's own traffic" flips with the arrow
  (`<-` vs `->`) -- confirmed because the same state, viewed from two
  interfaces, printed src and dst *swapped* between the two lines while
  the counters stayed byte-for-byte identical across both. `_parse_
  header_line()` discarded the arrow entirely, so `classify_sessions()`
  always assumed bytes_a was the printed src's own traffic regardless --
  correct for a `->` line (the shape every prior test happened to use),
  silently backwards for a `<-` one. Fixed by capturing the arrow and
  reorienting bytes/pkts onto (printed-src-own, printed-dst-own) before
  the existing local/peer split, which left the already-correct `->`
  case unchanged (verified: no existing test needed to change) and fixed
  the `<-` case. New regression test uses the user's real captured
  numbers directly (288279:1064917 pkts, 16881282:1437521618 bytes --
  the packet-size math alone is a strong tell: ~59 bytes/pkt for the
  small side matches bare TCP ACKs, ~1350 bytes/pkt for the large side
  matches full data segments). 189 tests passing.
  A related, secondary gap noticed but deliberately NOT fixed here (out
  of scope for this bug, needs its own dedicated test coverage): the
  *other* per-interface view of a NAT'd state -- the one showing the
  translated address plus the original in parentheses, e.g. `192.168.0.2
  :62831 (192.168.200.213:35178) -> ...` -- fails to parse at all
  (`_split_addr_port()` chokes on the parenthesized token), so that
  view is silently dropped rather than misread. Harmless today only
  because the *other* view of the same state still parses and now
  computes correctly; worth fixing properly later so a state that ONLY
  ever appears in that form isn't invisible outright.
- **1.6.2 -- hardened `compute_tick_deltas()` against a stale/re-meaning
  seeded baseline**, the general form of the artifact seen while
  verifying 1.6.1 on nostromo (a download still open across the upgrade
  restart briefly showed matching, inflated in/out totals). Real gap:
  `tick_prev_bytes`'s per-session baseline was populated identically
  whether it came from a normal tick five seconds ago or from whatever
  `live_sessions` happened to hold whenever the daemon last started --
  seconds or hours stale, or (as 1.6.1 proved) not even meaning the same
  thing if a labeling bug was fixed in between. Diffing against it
  either way meant any restart mid-transfer could dump an arbitrary
  amount of already-counted-or-mislabeled history into one tick. Fixed
  by tagging a seeded entry as such; its first `updated` tick now
  establishes a fresh baseline with a 0,0 delta instead of diffing
  against a baseline this run never actually measured, at the cost of
  under-counting at most one ~poll-interval's worth of throughput right
  after a restart -- a clearly better failure mode. Two new tests
  (seeded-baseline-establishes-without-a-delta, then diffs normally on
  the next tick). 191 tests passing.
- **1.6.3 -- real bug found by the user on nostromo: the DNS/SNI sniffer
  threads can die silently, with zero trace anywhere, taking hostname
  resolution and DNS query logging down with them while the rest of the
  daemon (Live/Top Talkers, pf-state polling) keeps running normally.**
  Root-caused live, over an extended back-and-forth with the user
  checking real data on their own box: an initial suspicion around the
  `bucket_start` hourly boundary turned out to be a timezone artifact in
  comparing a UTC process-start timestamp against a browser-local
  "last updated" time (both were actually the same instant); the real
  smoking gun was a genuinely new domain resolving fine right after a
  restart, working for one burst of a few queries, then permanently
  producing bare IPs on Live for anything new -- meaning the whole
  capture thread had died, not just the query-log half of it.
  `gowiththeflowd.py`'s `Daemonize` wrapper redirects stdin/stdout/
  *stderr* all to `/dev/null` (confirmed by reading its source) -- so an
  unhandled exception anywhere in `dns_sniffer.sniff_loop()`/`sni_
  sniffer.sniff_loop()`'s per-packet callback propagates out of scapy's
  own `sniff()`, kills that thread, and leaves genuinely zero trace in
  any log, which is exactly why grepping the system log twice during
  diagnosis came up empty despite something clearly being wrong.
  `dpi_classifier.capture_loop()` already had the right pattern for this
  exact class of bug (a real fix from 1.4.1: catch broad exceptions
  around each unit of work, log via the stdlib `syslog` module, keep
  going) -- it just hadn't been generalized to the other two sniffer
  threads. Fixed by wrapping both threads' per-packet `_handle()` bodies
  the same way. The specific packet/condition that originally triggered
  the crash on nostromo was never identified (couldn't be, given zero
  trace existed) -- the fix doesn't need to know what it was; it makes
  the *next* occurrence, on any thread, actually diagnosable instead of
  silently fatal. Verified on the test VM: normal operation unaffected
  (dns_query_log kept growing correctly post-fix), and a real
  `shutdown -r now` confirmed the daemon and both sniffer threads still
  come up cleanly. 191 tests passing (sniff_loop() itself remains
  untested by unit tests, by this module's own long-standing design --
  it's only ever proven against real capture on a real box).
- **1.7.0 -- block rules on a schedule, and per-domain blocking.** The
  user's original motivating case (catching a kid's gaming device active
  late at night and blocking it during set hours) plus a second one
  raised alongside it (block just youtube.com for one device, not the
  whole thing). Ended up smaller than either sounded alone: per-domain
  blocking rides OPNsense's own native Unbound DNSBL feature (Services >
  Unbound DNS > Blocklist) rather than any custom DNS-blocking mechanism
  -- confirmed live, not assumed, that a `dnsbl.blocklist` row's
  `wildcards` field blocks a domain and every subdomain automatically,
  and `source_nets` genuinely scopes the block to one client IP (proved
  with a real test entry: blocked for the target IP including a
  subdomain, passed for every other IP). The actual new work is a
  scheduler sitting on top of both this and the existing pf-based
  host-block mechanism, plus a friendlier interface than either
  Unbound's own blocklist editor or hand-writing pf rules.
  New `block_rules` table (db.py) -- deliberately separate from
  `blocked_hosts`, which stays exactly as it was (the literal,
  continuously-rewritten mirror of pf's own block table): a domain-only
  rule must never land there, or it would get full-host-blocked by
  mistake. `block_rules` is a real table of rules (host-only or
  host+domain, each with an optional weekly schedule), not just a set of
  currently-blocked IPs. A schedule is one or more `{days, start, end}`
  windows (`block_schedule.py`, pure logic, no I/O -- overnight-spanning
  windows, e.g. the user's own "8pm-8am weekdays, 9pm-7:30am weekends"
  example, and multiple chained/overlapping windows merging into one
  continuous segment, are both exercised directly by its own tests).
  Decided with the user: a manual unblock while a schedule would
  otherwise be blocking is a real, temporary override lasting until that
  window's own end (not just until the next reconcile tick), and
  symmetrically a manual block during a gap holds until the next window
  would naturally start anyway -- that second half wasn't explicitly
  decided, it's this feature's own natural extension of the first,
  called out as such rather than silently assumed.
  `gowiththeflowd.py` gained a new ~60s reconcile tick (`block_rules_
  engine.reconcile_all()`, same elapsed-gating shape as the existing
  hourly/daily jobs, plus one extra call at startup so a restart
  mid-window re-asserts state immediately) that is the *only* thing
  deciding what's currently blocked -- kept in the daemon rather than a
  separate PHP-driven cron specifically so there is still exactly one
  writer of "what's blocked right now" (this project's existing
  `blocked_hosts`/pf architecture already had exactly one, on purpose)
  and because killing in-flight pf states on a schedule-triggered block
  needs the same `blocklist.kill_states()` capability manual blocking
  already uses, which only exists inside the running daemon process. A
  domain rule's actual enforcement crosses into PHP regardless (Unbound's
  config is PHP-model-owned) via a new, small, dedicated CLI script
  (`dnsbl_apply.php`, shelled out to directly, not via configd) rather
  than Python re-implementing Unbound's config generation -- confirmed
  live end-to-end through the real daemon's own reconcile tick, not just
  a direct CLI call: created a scheduled domain rule with a ~2-minute
  test window, confirmed it was blocked while active, then watched the
  daemon's own background loop actually flip it back to "Pass" within
  ~15 seconds of the window ending, with no manual trigger at all.
  The existing block-a-host quick-block icon on Live/Top Talkers keeps
  working completely unchanged -- `block_host.py`'s own `cmd_block`/
  `cmd_unblock` now also upsert/delete a mirrored "always" host rule in
  `block_rules` in the same call, so both surfaces stay in lockstep with
  no separate migration step, and so the scheduler's own reconcile tick
  can never fight with a block made the old way (an easy trap: without
  this, unblocking via the old icon would leave a stale enabled rule
  behind that the next reconcile tick would silently re-block within
  ~60s).
  A domain rule's target device must already have a static DHCP
  reservation (refused, not auto-created, if it doesn't) -- confirmed
  live which exact Dnsmasq.xml field shape to check
  (`\OPNsense\Dnsmasq\Dnsmasq()->hosts`, an ArrayField whose own `ip`/
  `hwaddr` fields ARE the reservation, not a separate nested collection
  as originally guessed from reading the XML alone) rather than trusting
  the schema on paper.
  The Add/Edit dialog's "Device" field accepts a known hostname as well
  as a raw IP (`BlockrulesController::resolveDeviceIp()`, a case-
  insensitive `local_host_identity` lookup, most-recent-wins like every
  other identity lookup in this project) plus a `<datalist>`-driven
  autocomplete of every locally-known device -- confirmed live against a
  real inserted identity row (exact hostname, mixed-case hostname, raw
  IP, and an unknown name correctly returning null).
  New unified "Block Rules" page (`blockrules.volt`, its own top-level
  menu entry) replaces History's old Blocked tab -- one grid for every
  rule regardless of kind, each row showing its schedule (or "Always")
  and live status (read from a cache field the reconcile tick itself
  writes, rather than a second PHP implementation of the schedule
  predicate that could drift from the real one -- up to one reconcile
  interval's staleness right after a boundary is an accepted,
  honestly-labeled tradeoff). `BlockedController::searchAction()` (the
  old tab's own endpoint) was deleted outright as dead code rather than
  left behind, now that nothing calls it.
  New tests: `block_schedule.py` (14), `block_rules_engine.py` (26,
  covering the full CRUD surface plus the pure decision logic and the
  reconcile loop surviving one rule's bad data), `block_rules.py`'s CLI
  (12), plus `blocklist.py`'s new shared `refuse_reason_for_host_block()`
  guard (factored out of `block_host.py` so the new `block_rules.py`
  entry point can't drift from the original's own firewall-lockout
  checks) and `block_host.py`'s new lockstep behavior (5). 252 tests
  passing.
- **1.7.0 follow-ups.** Two robustness gaps found via the user's own
  pointed questions after the above shipped, both from the same root
  cause this project has now hit three times (see dns_sniffer.py's
  RRSIG fix in 1.6.3): `Daemonize` redirects stdin/stdout/stderr to
  `/dev/null`, so an unhandled exception anywhere in the main loop or a
  background thread is completely invisible unless it's explicitly
  caught and logged.
  1. `gowiththeflowd.py`'s call to `block_rules_engine.reconcile_all()`
     wasn't itself exception-wrapped -- only per-rule failures inside
     `reconcile_all()` were caught. A failure in the reconcile call's own
     setup (e.g. its initial query) would have taken the *entire* daemon
     down silently. Fixed with a `_reconcile_schedules()` wrapper
     (log-and-continue, same shape as every other guarded call site).
     Verified live: renamed the real `block_rules` table away, confirmed
     the daemon logged the expected error every cycle for 3+ cycles on
     the *same* PID while every other job (DNS query log writes
     included) kept working normally, then restored the table and
     confirmed a clean recovery with no further errors.
  2. The PTR reverse-DNS fallback (`ptr_resolver.py`) only ever got one
     attempt per peer, made inline in the main poll loop at the moment a
     session first opened, capped at 10 lookups/60s. Real-world symptom
     reported by the user: peers that manually `nslookup`'d fine (e.g. a
     GitHub/Facebook CDN edge) were showing as bare IPs in the Details
     tab. Root cause: a burst of new sessions from several devices at
     once could exceed that one poll's lookup budget, and once a peer
     missed its single shot it was never retried for the life of that
     flow -- worse, a single failed lookup (even a transient one) was
     negative-cached for a full hour, so a lookup that would have
     succeeded moments later stayed blocked. Fixed by moving PTR lookups
     onto their own background thread (`ptr_resolver.resolve_loop()`,
     same queue/callback shape as `dpi_classifier.capture_loop`) so a
     slow upstream resolver can never stall pf state polling, and
     changing the trigger from "only newly-opened sessions" to "every
     still-open, still-unresolved session, every poll" -- a still-open
     flow's peer now keeps getting retried rather than getting one shot.
     With lookups off the poll loop's hot path, the rate-limit budget
     was raised (10 -> 60/60s) and the negative-cache TTL shortened
     (3600s -> 300s) to act as a retry backoff rather than an hour-long
     lockout. 3 new tests for `resolve_loop()` (hit, miss, and surviving
     a raising resolver without killing the thread). 255 tests passing.
  3. **The `resolve_loop()` background-thread fix above (#2) had its own
     bug, found in real production on the user's own box (nostromo) the
     very first night schedule-driven blocking ran unattended.** The
     daemon died within 60-120s of every restart, with *zero* trace
     anywhere -- not even from #1's own reconcile-wrapper fix, which was
     already installed and never fired, ruling that path out and pointing
     at something else in the main loop entirely. Added a top-level
     try/except around the *whole* loop body (logging the full traceback
     and backing off one poll interval before retrying) specifically to
     get visibility into whatever this was -- and it immediately paid
     off: `ValueError: too many values to unpack (expected 2)` at
     `peer_ip, ptr_hostname = ptr_results.get_nowait()`. Root cause:
     `resolve_loop()` called `on_result(ip, hostname)` as two positional
     arguments, and gowiththeflowd.py wired `on_result` directly to a
     bound `queue.Queue.put` -- but `Queue.put(item, block=True,
     timeout=None)` treats a second positional argument as `block`, not
     a second queued value, so the queue only ever held a bare IP
     *string*, never a tuple. The instant any real PTR result came back
     on a busy real network (which essentially never happened on the
     project's own near-idle dev VM during Phase B verification), the
     consumer's unpack blew up. This had nothing to do with the schedule
     feature the user was actually testing that night -- the timing
     correlation with the 60s schedule-reconcile interval was
     coincidental. Existing unit tests for `resolve_loop()` didn't catch
     it because they used a hand-written two-argument `on_result`
     callback rather than a real `Queue.put` -- fixed by having
     `resolve_loop()` pass a single `(ip, hostname)` tuple instead
     (matching `sni_sniffer.py`'s own established callback convention),
     and added a regression test that wires a *real* bound
     `queue.Queue.put` directly to `resolve_loop()` -- confirmed it fails
     against the old calling convention and passes against this one.
     Verified live on nostromo: hand-patched both files in place (the
     package hadn't been rebuilt yet), watched the daemon run past 3+
     minutes with zero further errors and fresh DB writes throughout,
     versus dying every single time within 60-120s before. This is the
     canonical "your tests only prove your test doubles are consistent
     with each other" lesson -- worth remembering the next time a
     background-thread callback gets wired to a stdlib method directly
     rather than a hand-written function. 256 tests passing.
- **1.8.0.** Everything below shipped as further live-verified rebuilds
  still labeled "1.7.0," across one long session -- by the end that had
  made the version number itself meaningless as a marker of what was
  actually running, which is the whole reason this is a real version
  bump rather than another silent rebuild.
  1. **Block Rules edit (pencil) icon never rendered at all.** Root
     cause: `edit` is a *reserved* command name in
     `opnsense_bootgrid.js`'s own built-in command set
     (`requires: ['get', 'set']`, checked against a `crud` config this
     plugin never provides since it uses its own hand-built modal, not
     `getForm()` scaffolding). The library's merge logic only overwrites
     fields a custom command definition actually provides, so the
     built-in's `requires` survived untouched and the visibility check
     silently failed. Renamed to `gwtfedit`, matching this project's own
     `gwtf`-prefixed convention for custom grid commands.
  2. **Device autocomplete now fills the device field with the
     hostname, not the IP** -- stays correctly attached to a device
     across a DHCP lease change (`resolveDeviceIp()` already accepted
     either form; this just changes which one gets suggested).
  3. **History's "Table" tab renamed to "Details"**, matching the
     earlier Live->Table rename, plus a new **Last Seen** column
     (`MAX(bucket_start)` across the matched rollup rows).
  4. **`manual_categories.py`'s second pass** (~125 new entries seeded
     from a real "Uncategorized Hosts" export: Shopping/News/Banking/
     Education/Government as new buckets, plus extensions to existing
     ones), **then a structural refactor**: the hardcoded `OVERRIDES`
     dict became `domain_categories/`, one plain-text file per category
     (mirroring the upstream v2fly files' own one-file-per-category
     shape), each starting with a `# category: <Display Name>` header
     (three real category names contain a `/`, which can't be a
     filename) followed by one domain per line with the same `#`-comment
     support the dict's inline comments used -- verified byte-for-byte
     that every one of the old dict's 270 entries carried over with zero
     drops or typos before deleting it. `claude.ai`/ChatGPT/Gemini/Grok
     and `anthropic.com` added to AI (correctly pulling
     `gemini.google.com` out of the broader automated "google" ->
     Cloud/Productivity bucket, since manual overrides are checked
     first).
  5. **New `recategorize.py`** -- category is stamped once when a
     connection is first written and never revisited, so growing
     `domain_categories/` only ever affected *newly-observed* traffic
     until now. `list-uncategorized` mirrors the GUI's own
     "Uncategorized Hosts" tab query, straight from the database.
     `apply` (with `--dry-run`) re-resolves every distinct
     already-recorded hostname via a new shared `categories.
     resolve_category()` helper (factored out of gowiththeflowd.py's own
     live categorization so the daemon and this offline pass can never
     drift apart) and updates any row across
     connections_raw/live_sessions/rollup_hourly/rollup_daily whose
     category no longer matches. Exposed as a "Recategorize History"
     button on Settings. Two real bugs found running this live against
     nostromo's actual history (neither caught by any test, since the
     test DB is tiny): `connections_raw`/`live_sessions` had no index on
     `peer_hostname` (apply's per-hostname UPDATE was a full table scan
     each time -- 9+ minutes and climbing on real data, 19s after adding
     the missing indexes defensively, skip rather than raise against the
     ancient pre-rename legacy schema the category-column migration test
     exercises); and the new script had neither a shebang nor the exec
     bit, so its configd action failed with a bare "Execute error" --
     `build-pkg.sh` had only ever explicitly `chmod +x`'d
     gowiththeflowd.py, and every other CLI script's exec bit turned out
     to be incidental state on whichever machine last built the package,
     not anything actually guaranteed. Also added `PRAGMA
     busy_timeout=30000` to `db.connect()` (nothing had one before),
     since recategorize.py's own multi-second write transaction was
     colliding with the daemon's 5s poll cycle and logging avoidable
     "database is locked" errors. Ran for real against nostromo's actual
     history: 871 hostnames corrected, re-run confirmed idempotent (0
     changes the second time), daemon stayed healthy throughout.
- **1.8.1 -- real production freeze, found live on the user's own box the
  same night 1.8.0 was cut.** A single sustained multi-gigabyte download
  (a game client update, confirmed via its pf state: a byte counter
  matching the download size almost exactly) intermittently made
  `pfctl -vvs state` block for minutes at a time -- not a bug in this
  project's own code, but the kernel apparently deprioritizing an
  administrative "dump the whole pf state table" query while genuinely
  busy servicing that much real packet throughput. `subprocess.run()` for
  that call (and localhost_identity.py's two, `configctl dnsmasq list
  leases`/`arp -an`) had no timeout anywhere, so the *entire* daemon froze
  solid each time: no exception, so nothing for the main loop's own
  top-level catch-all (1.7.0 follow-up #3, above) to catch --
  `live_sessions` simply stopped advancing, silently, until a manual
  restart, which then froze again the next time the download got heavy
  enough. Diagnosed without touching the live box beyond read-only
  checks (ps state, a kernel stack dump via `procstat -kk`, and finding
  the actual outsized pf state) -- confirmed the moment the download
  actually finished that the daemon self-recovered on its own (`ps`
  state flipped D -> S, `live_sessions` resumed within seconds), which
  is what pinned this down before the fix even shipped. Added
  `timeout=15` to all three calls -- a timeout expiring now means
  exactly one skipped, logged poll cycle instead of an indefinite
  freeze. 275 tests passing.
- **1.9.0 -- Block Rules: named, multi-device groups + duplication.** A
  rule used to be identified by one device (`local_ip`/`mac`/`hostname`
  columns); real use didn't fit that -- several devices (quest3s, ps5,
  iphone-max, ipad-max) sharing the exact same weeknight schedule each
  needed their own separate rule with no way to see them as one policy.
  Replaced with `name TEXT` + `devices TEXT` (a JSON array of
  `{"ip", "hostname", "mac"}` snapshots) -- a rule now names a *group*.
  Existing rules migrate automatically in `db.init_schema()` (rename old
  table, let `SCHEMA_SQL` recreate the new shape, re-insert each old row
  as a single-device group named after its hostname or IP, drop the old
  table) -- confirmed idempotent on a second run. The old DB-level
  `idx_block_rules_one_host_rule` unique index (one host-block per
  device) can't survive a device moving inside a JSON array, so it
  became an application-level guard,
  `devices_conflicting_with_other_host_rules()`, checked at both
  create and edit (excluding the rule being edited from its own check).
  A domain rule's group gets **N independent Unbound dnsbl.blocklist
  rows**, one per device (each keyed `<unbound_description>:<device_ip>`,
  scoped to that one device's `source_nets`, sharing the same domain
  list) rather than depending on whether a single row's `source_nets`
  accepts multiple values -- sidesteps needing to verify that at all.
  New **Duplicate** rule action (`gwtfduplicate` -- not `duplicate`,
  though that particular word turned out not to be reserved either;
  kept the `gwtf`-prefix convention anyway so a future bootgrid version
  reserving a plain word can never silently break this grid's buttons
  the way `edit` did in 1.8.0) copies a rule verbatim, appends
  " (copy)" to its name, and starts **disabled** -- it must never
  instantly double-block the same devices the original already covers.
  `block_host.py`'s pre-existing "quick block" action mirrors into this
  same table (an "always" single-device host rule, kept in lockstep so
  the unified Block Rules page and the quick block/unblock buttons never
  disagree) -- `create_host_rule()` itself no longer upserts by IP now
  that a rule covers a group, so `cmd_block()` finds-or-updates that
  mirrored single-device rule itself, and `cmd_unblock()` only ever
  deletes a rule that is *exactly* a single-device match for the IP
  being unblocked, never a multi-device group rule that happens to
  include it (that stays managed via the Block Rules UI). Found and
  fixed one more real bug during dev-VM verification before shipping:
  editing a rule to drop a device from its group left that device's pf
  block (or, for a domain rule, its dnsbl row) orphaned forever --
  `_apply_host_rule()`/`_apply_domain_rule()` only ever loop a rule's
  *current* devices, so nothing reconciled a device that fell out of
  the group. `update_rule()` now diffs the old device set against the
  new one and explicitly unblocks/removes anything dropped. 295 tests
  passing.
- **1.9.1 -- device-list field accepted a compound "device" it
  couldn't resolve.** Found live on nostromo the day after 1.9.0
  shipped: typing a comma-separated list of names into the dialog's one
  default device row (the same convention the Domains field already
  uses) sent that whole string as a single array entry, which then
  failed to resolve as one device with the literal error message
  quoting the entire list. Fixed at both layers -- `gwtfCollectDevices()`
  now splits each row's value on commas too, not just trims it, so
  typing several names into one row, using "Add another device" per
  name, or any mix all work the same way; `BlockrulesController::
  resolveDevices()` splits each array entry on commas the same way too,
  defense-in-depth in case the client-side split is ever bypassed or
  out of sync, matching this project's established pattern of enforcing
  guards at both the UI and server layer.
- **1.9.2 -- enabling a rule never ran the host-rule conflict guard,
  and there was no way to enable/disable a rule from the GUI at all.**
  Reported directly: duplicating a rule (which starts disabled on
  purpose, precisely so it can't instantly double-block the original's
  own devices) then enabling it silently created a second enabled host
  rule covering the same device, with no error -- `set_enabled()` never
  ran `devices_conflicting_with_other_host_rules()`, only create/edit
  did. `cmd_set_enabled()` now runs that same guard before turning a
  host rule on, refusing with the conflicting rule's name, exactly like
  create/edit already do (domain rules stay exempt, matching the
  guard's existing host-only scope). Separately, and directly related:
  nothing in the grid ever called the pre-existing set_enabled backend
  at all -- there was no "pause"/"resume" control anywhere in the UI,
  so a duplicated rule genuinely had no path back to enabled through
  the GUI. New **gwtftoggle** grid command (not `toggle` -- also a
  reserved `opnsense_bootgrid.js` command name, same collision class as
  `edit`) fixes that. Also fixed while touching this: a paused rule was
  still showing its last known "Blocked"/"Not blocked" label (set_enabled's
  disable path unwinds enforcement directly, bypassing apply_rule(), so
  it never updated last_effective_state) -- `formatStatus()` now checks
  `enabled` first and shows "Paused"; the schedule-override buttons now
  also hide for a paused rule instead of silently no-opping. Verified
  live on the dev VM by reproducing the exact reported sequence
  (duplicate, enable while the original stayed active -> refused
  naming the original; pause the original, then enable the duplicate
  cleanly -> succeeds), plus a full reboot-survival cycle. 297 tests
  passing.
- **1.9.3 -- conflict error reworded on request.** Was
  `already blocked: 10.0.0.20 (already blocked by rule 'nvr')`; now
  `already blocked by rule "nvr": nvr` -- leads with the rule name (the
  thing the user actually needs to go act on), and shows the device's
  known hostname instead of a bare IP where one's on file. Several
  conflicting devices are grouped by rule name rather than repeating it
  per device. Verified live against the dev VM's real `nvr` rule,
  reproducing the exact wording requested; reboot-survival cycle clean.
  298 tests passing.
- **1.9.4 -- conflict error reworded again on request.** Now
  `<hostname> is already being blocked by rule "<rule name>"` --
  device leads, rule trails, one sentence per conflicting device (the
  grouped-by-rule form from 1.9.3 didn't survive this second pass).
  Verified live against the dev VM's real rule (renamed to "bingbong"
  by the time this shipped, plus a "bingbong (copy)" -- both left
  untouched as the user's own data, not test cruft); reboot-survival
  cycle clean.
- **1.9.5 -- Block Rules grid's commands column too narrow on
  nostromo.** `data-width="8em"` was sized back when this grid had one
  or two row commands; now up to five can appear on one row at once
  (edit, duplicate, pause/resume, one schedule-override button,
  delete), and 8em can't fit them all -- bootgrid silently collapses
  the overflow into a "..." menu rather than erroring, which is why
  this went unnoticed through dev-VM testing (evidently just a wider
  browser window there) until the user hit it for real on nostromo.
  Not something fixable by resizing the column from the grid itself --
  it's a fixed template attribute. Bumped to 14em. Template-only
  change (no Python/PHP logic touched); reboot-survival cycle clean, no
  browser available in this environment to visually confirm the fix
  beyond the CSS math -- flagged to the user to confirm on nostromo.
- **1.9.7 -- the 1.9.5 fix was the wrong kind of guess, not just the
  wrong number.** 14em left a visible empty gap on nostromo's real
  screen; re-measuring from that screenshot and shipping 10em (never
  actually published) would have just been guessing again in the same
  broken units. The user found the real cause via devtools: this
  OPNsense version renders `UIBootgrid()` through Tabulator.js
  internally, and `opnsense_bootgrid.js`'s own column-width parsing only
  takes an em/CSS-unit `data-width` at face value for a *hidden probe
  element's rendered outerWidth()* (padding/border included, plus a
  flat +5px margin) -- so the resulting pixel width depends on this
  page's ambient font-size and was never going to match anyone's em
  arithmetic by hand; confirmed live it was rendering 10em as 215px, not
  anything close to a plain 10 x 16px assumption. A **bare number**
  (no unit) skips all of that and is used as the literal pixel width
  directly -- switched to `data-width="152"`, the exact value the user
  confirmed live via devtools fits all 5 command icons with no leftover
  gap. Template-only change; reboot-survival cycle clean. Still no
  browser available in this environment -- this one is a measured,
  mechanism-verified value rather than another guess, but the user's
  own visual confirmation on nostromo remains the real check.
- **1.9.8 -- dropped the two schedule-override grid buttons; single
  pause/resume covers it.** Reported live: pausing a rule made the
  "Block now" button disappear at the same moment gwtftoggle's own icon
  flipped from pause to play -- and override_unblock's icon was *also*
  a green play arrow, so two different buttons were changing for
  confusingly similar-looking reasons at once. Removed override_block/
  override_unblock from the grid entirely; pause/resume alone covers
  the real need. Now exactly 4 commands always show (edit, duplicate,
  pause/resume, delete), none of them conditionally filtered any more.
  The backend (`rule_override`/`set_override`/`manual_override_state`+
  `override_until`) is untouched, just no longer wired to a button.
  Commands column width dropped from 152 to 122 (152/5 x4, matching
  1.9.7's per-icon measurement for one fewer icon) -- not yet
  re-confirmed live the way 152 was. Reboot-survival cycle clean.
- **Not yet started**: the staticOverrides grid editor, and proper repo
  signing before this pkg-repo is relied on for anything that matters.
  ("Scheduled traffic blocking" -- the user's original motivating
  real-world case, catching a kid's gaming device active late at night --
  is no longer on this list: see the 1.7.0 entry below.) See "Roadmap"
  below for the larger post-launch feature set
  (app/category classification, local<->local tracking, Sankey
  visualization, DPI) agreed after the user asked to aim for rough
  feature parity with the commercial ZenArmor plugin -- items #2 and #3
  below are now partially superseded by the 1.2.0 work above.
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
2. **Local<->local traffic tracking** (agreed, general case only) --
   **built as "Internal Traffic," then superseded by the 1.2.0
   local/remote-unification work (see the Status entry above) -- it's
   no longer a separate page/pipeline, just `peer_is_local=1` rows
   flowing through the same Live/History/Top Talkers views as any other
   peer.**
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
3. **Live page redesign + Sankey-style flow visualization** -- **the
   Live page redesign half was built as part of 1.2.0's Overview/Table
   split (see the Status entry above)**, though not exactly as
   originally sketched: rather than one aggregated row per local host,
   Live's Overview tab is a Line/Stacked-Bar/Graph chart of per-host (or
   per-port) throughput over time, with the Table tab kept as the
   existing flat per-connection list (click-through from the chart, not
   a host-row drill-down). A real Sankey diagram was explicitly
   considered and rejected for now -- the user judged it "too busy" for
   a single source host's own connections (a table is just as good
   there) and category data isn't trusted enough yet to make a
   host -> category -> peer diagram worthwhile. The experimental
   node-link "Graph" view built instead is the closest analog currently
   shipped; revisit true Sankey rendering later if the Graph view proves
   insufficient once category coverage improves.
4. **DPI (nDPI or similar)** -- **built in 1.4.0 (see the Status entry
   above) as periodic batch classification via `ndpiReader`, not the
   genuinely live continuous classifier originally imagined here.**
   Feasibility research found `ndpiReader`'s JSON output is batch-only
   (confirmed directly against a real capture), and there's no packaged
   `nDPIsrvd` (nDPI's own streaming daemon) in this FreeBSD port -- a
   real continuous classifier would need custom `libndpi.so` bindings,
   a materially bigger and riskier undertaking than the rest of this
   plugin's passive-sniffing design. User chose the batch tradeoff
   (lags by ~1 burst duration, no flow continuity across bursts) over
   that heavier path. Gives protocol/app identification independent of
   hostname/port (works for non-standard ports, obscure protocols) and
   QUIC/HTTP-3 awareness (a real gap -- some of the unresolved port-443
   entries the user noticed are likely QUIC, which the DNS/SNI sniffer
   doesn't parse at all), plus some resilience to encrypted DNS (DoH)
   since it doesn't depend on seeing the DNS query. Still does **not**
   help with Encrypted Client Hello (ECH) -- SNI hidden cryptographically,
   unreadable by any packet inspection; defeating it needs active
   SSL/TLS interception (installing a trusted root cert on every device,
   decrypting and re-encrypting everything), a much bigger and more
   invasive undertaking than DPI itself, still out of scope. Ships
   opt-in (`enable_dpi` defaults off) pending real resource-cost
   measurements on a busy network -- the "16GB RAM, 4-core i7, fine with
   the cost" belief this item was originally scoped under hasn't
   actually been measured yet.

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
    │   │   │       ├── HistoryController.php   # DONE -- aggregates rollup_hourly/rollup_daily by (local_ip, peer_ip); timeseriesAction() backs the Overview chart
    │   │   │       ├── ToptalkersController.php # DONE -- localAction()/peerAction(), ranks by bytes/connections
    │   │   │       ├── ServiceController.php   # DONE -- trivial ApiMutableServiceControllerBase subclass
    │   │   │       └── SettingsController.php  # DONE -- ApiMutableModelControllerBase + clearData/resetHostnameCache
    │   │   ├── models/OPNsense/GoWithTheFlow/
    │   │   │   ├── GoWithTheFlow.xml           # DONE -- enable, interfaces, subnets, retention, rctl caps, hostname tuning
    │   │   │   ├── GoWithTheFlow.php           # DONE -- plain BaseModel, no custom validation needed
    │   │   │   ├── ACL/ACL.xml                 # DONE -- ui/gowiththeflow/*, api/gowiththeflow/*
    │   │   │   └── Menu/Menu.xml               # DONE -- Reporting > Go With The Flow > Live/History/Top Talkers; Services > Go With The Flow > Settings
    │   │   └── views/OPNsense/GoWithTheFlow/
    │   │       ├── live.volt                   # DONE -- Overview (Line/Stacked-Bar/Graph chart) + Table tabs, byte/duration formatters
    │   │       ├── history.volt                # DONE -- Overview (per-host chart) + Table tabs, day-range/local-host/resolution filters
    │   │       ├── toptalkers.volt              # DONE -- Bootgrids (local/peer/category/uncategorized) + shared days selector
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
    `rollupHourlyRetentionDays` (default 8, changed from 45 in 1.2.0),
    `rollupDailyRetentionDays` (default 32, changed from 730 in 1.2.0),
    `cpuLimitPct` and `memLimitMB` (rctl cap fields exist
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
| POST | `/api/gowiththeflow/live/search/` | Bootgrid standard, + optional `local_ip`/`peer_ip`/`peer_port` (exact, ANDed) and `host_ip` (matches either side) | **DONE.** local/peer (`hostname (ip)`), `peer_is_local`, `category`, `state` (pf's own connection state), `last_activity` (1.2.2 -- only advances on real bytes/state change, unlike `last_seen`), proto, port, bytes in/out, live-computed duration. The filter params (1.2.2) back the Overview chart's click-through, which needs a real server-side filter since this grid is ajax-backed |
| POST | `/api/gowiththeflow/live/overview` | none (no pagination at all) | **DONE (1.2.7).** Every currently open session, unpaginated -- `local_ip`/`peer_ip`/`peer_port`/`bytes_in`/`bytes_out`/`last_activity`/`local`/`peer`/`row_id` only. Backs the Graph view and the Table/hostname lookups, polled independently of the Table tab's own Bootgrid ajax call -- deriving data from that paginated response (default page size 50) meant a genuinely dominant host's traffic could be silently invisible if its rows weren't on the page the table happened to be showing, confirmed for real with a phone's speedtest.net traffic never appearing on the chart |
| POST | `/api/gowiththeflow/live/series` | `since` (a `tick_time` watermark; 0 or omitted returns everything currently retained) | **DONE (1.3.0).** Flat list of `{tick_time, local_ip, peer_port, delta_bytes_in, delta_bytes_out}` from the new `live_ticks` table -- computed once, server-side, by `gowiththeflowd.py`/`live_ticks.compute_tick_deltas()` every poll cycle, pruned to a rolling ~35-minute window. Backs the Overview Line/Bar chart -- every open tab reads the same recorded history instead of each independently diffing its own poll of `overview`, and a reconnecting tab just fetches the real ticks it missed |
| POST | `/api/gowiththeflow/history/search/` | + `days`, `local_host?` | **DONE.** rollup rows aggregated by (local_ip, peer_ip), granularity auto-picked by `days` vs. `DbApiControllerBase::HOURLY_RETENTION_DAYS` (8, matching the default `rollupHourlyRetentionDays` setting), plus a `local_hosts` map for the filter dropdown. `local_host` filter and bytes correctly account for `peer_is_local=1` pairs via a UNION ALL (see 1.2.0 Status entry) |
| POST | `/api/gowiththeflow/history/timeseries` | `days`, `bucket=hour\|day`, `local_host?` | **DONE (1.2.0).** `{buckets, series: {ip: bytes[]}, local_hosts}`, top-10-by-total capped with an "Other" aggregate — backs History's Overview chart |
| POST | `/api/gowiththeflow/toptalkers/local` | `days` | **DONE.** ranked local hosts by total bytes/connections (sortable by clicking either column — no separate `sort_by` param needed); a UNION ALL credits both members of an internal pair from their own point of view |
| POST | `/api/gowiththeflow/toptalkers/peer` | `days`, `local_host?` | **DONE** (renamed from `remote` in 1.2.0). ranked peers (internet or local); filtering by `local_host` correctly shows only that host's share of a shared-IP peer, not the combined total; a UNION ALL also surfaces the numerically-smaller member of an internal pair, which would otherwise never appear here at all |
| POST | `/api/gowiththeflow/toptalkers/protocol` | `days` | **DONE (1.4.0).** ranked `dpi_protocol` values (nDPI classification, batch-enriched -- see 1.4.0 Status entry), `COALESCE`d to 'Unclassified'; near-verbatim copy of `categoryAction()`'s shape, just grouped by protocol instead of category |
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
