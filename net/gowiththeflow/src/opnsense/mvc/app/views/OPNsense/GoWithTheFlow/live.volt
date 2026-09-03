<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script>
    'use strict';

    // Shared with updateLiveOverview()/renderLiveChart()/renderLiveGraph()
    // below, which are declared outside $(document).ready() -- they must
    // live at this script's top level, not inside the ready() closure, or
    // those functions can't see them at all (a real bug: this exact
    // mistake shipped once already and only surfaced as a live
    // `ReferenceError` in a real browser, since neither the Python test
    // suite nor a syntax check would ever catch a closure-scoping issue).
    let chartHistory = [];             // [{time, groups: {key: bytesDelta}}]
    let groupLabels = {};              // raw key -> display label (hostname where known)
    let hiddenGroupKeys = new Set();   // raw keys shift-clicked out of the Line/Bar chart
    // Fixed, not user-configurable -- the chart's range and point spacing
    // are now both driven by the server's real tick history (live_ticks),
    // not approximated from the browser's own chosen poll rate. The
    // "Refresh every" dropdown still exists, but now only controls how
    // often the Table tab/Graph view poll for freshness.
    const LIVE_CHART_RANGE_MINUTES = 30;
    // Top Talkers' "1 min" byte columns -- a trailing moving window, not
    // "since the last poll" (that was too volatile at a 5s cadence, per
    // real user feedback: a single big/idle tick made the number swing
    // wildly with no sense of a sustained rate).
    const GWTF_TOPTALKERS_RATE_WINDOW_MS = 60000;
    let liveChartTopN = 10;            // Line/Bar chart's line cap, user-configurable -- 0 means "all"
    let liveChartLogScale = false;     // log y-axis so a big spike doesn't flatten smaller signals
    let groupColorSlots = new Map();   // raw key -> GWTF_PALETTE index, stable across re-ranking --
                                         // freed only when a key has no activity anywhere in the
                                         // window at all (not merely demoted out of the top N)
    let usedColorSlots = new Set();
    const GRAPH_FADE_MS = 4000;
    let graphNodes = {};                // "local_ip|peer_ip|peer_port" -> {el, lastSeen, fading}
    let forceNodePositions = {};        // ip -> {x, y} -- persists across ticks so the force layout
                                         // gently relaxes as data changes instead of jumping around
    let lastRows = null;                // most recent live/overview rows, cached so switching
                                         // to Graph mode can render immediately, not wait a tick
    let liveFilter = { local_ip: '', peer_ip: '', peer_port: '', host_ip: '' };  // server-side, via requestHandler
    let liveOverviewWorker = null;      // owns the Overview chart's poll loop -- see gwtfStartLiveOverviewWorker()
    let localHostLabels = {};           // local_ip -> display label, ALWAYS populated regardless of
                                         // the chart's own local_ip/peer_port grouping toggle -- the
                                         // Top Talkers tab is always grouped by local host
    let hostWindowHistory = [];         // [{time, hosts: {local_ip: {bytesIn, bytesOut}}}] -- mirrors
                                         // chartHistory's per-tick bucketing but is ALWAYS keyed by
                                         // local_ip (independent of the chart's own grouping toggle),
                                         // for the Top Talkers tab's window (LIVE_CHART_RANGE_MINUTES) byte columns
    let hostConnSeenTimes = {};         // local_ip -> Map<row_id, lastSeenMs> -- every *distinct*
                                         // connection (by full 5-tuple row_id, not just port) seen
                                         // open at any /overview poll in roughly the last
                                         // LIVE_CHART_RANGE_MINUTES, self-pruned by wall-clock time in
                                         // updateLiveOverview() so a host that's gone quiet still ages
                                         // out after the window passes rather than staying stuck
    let lastToptalkersRowValues = {};   // row_id -> last-pushed row object -- confirmed live (Performance
                                         // panel, real production traffic) that Tabulator's own
                                         // updateOrAddData() does NOT skip a row whose field values are
                                         // unchanged; it still runs the full per-cell height/layout
                                         // bookkeeping regardless. renderLiveTopTalkers() diffs against
                                         // this cache itself and only pushes rows that actually changed.
    // gowiththeflowd.py's own POLL_INTERVAL_S -- live_ticks genuinely
    // can't produce a new tick faster than this, and is also exactly the
    // real spacing between the tick_time values live/series returns, so
    // this is the chart's true, fixed point width -- not an approximation
    // of the browser's own chosen poll rate the way it used to be.
    const GWTF_DAEMON_POLL_INTERVAL_MS = 5000;
    let gwtfBlockedIps = new Set();      // local_ip -> currently blocked, refreshed via
                                         // refreshBlockedSetGWTF() -- read by
                                         // renderLiveTopTalkers() to feed each row's own
                                         // `blocked` field (the Table tab's rows get theirs
                                         // straight from LiveController.php's own query instead)

    // Shared between #grid-live-toptalkers and #grid-live -- both need the
    // exact same block/unblock icon behavior. `filter` (a real, documented
    // UIBootgrid command property -- confirmed by reading
    // opnsense_bootgrid.js's own commands formatter) is what makes only
    // one of the two ever render for a given row; `method`'s `cell` param
    // gives `cell.getData()` regardless of which grid's row shape called
    // it, since both carry a `local_ip` field (Table tab: straight from
    // LiveController.php's SQL; Top Talkers: added below in
    // renderLiveTopTalkers()).
    const GWTF_BLOCK_COMMANDS = {
        gwtfblock: {
            title: "{{ lang._('Block this device') }}",
            classname: 'fa fa-ban fa-fw',
            sequence: 1,
            filter: function (cell) { return !cell.getData().blocked; },
            method: function (event, cell) {
                const d = cell.getData();
                const label = d.local || d.local_ip;
                stdDialogConfirm(
                    "{{ lang._('Confirm block') }}",
                    "{{ lang._('Block all traffic to and from') }} " + label + "? " +
                        "{{ lang._('Its currently open connections will be dropped immediately.') }}",
                    "{{ lang._('Block') }}", "{{ lang._('Cancel') }}",
                    function () {
                        ajaxCall('/api/gowiththeflow/blocked/block', { local_ip: d.local_ip }, function (data) {
                            if (data && data.status !== 'ok') {
                                stdDialogInform(
                                    "{{ lang._('Block failed') }}",
                                    (data && data.error) || "{{ lang._('Unknown error') }}",
                                    "{{ lang._('Close') }}", undefined, 'danger'
                                );
                            }
                            refreshBlockedSetGWTF();
                        });
                    },
                    'danger'
                );
            }
        },
        gwtfunblock: {
            title: "{{ lang._('Unblock this device') }}",
            classname: 'fa fa-ban fa-fw text-danger',
            sequence: 1,
            filter: function (cell) { return !!cell.getData().blocked; },
            method: function (event, cell) {
                const d = cell.getData();
                const label = d.local || d.local_ip;
                stdDialogConfirm(
                    "{{ lang._('Confirm unblock') }}",
                    "{{ lang._('Restore traffic to and from') }} " + label + "?",
                    "{{ lang._('Unblock') }}", "{{ lang._('Cancel') }}",
                    function () {
                        ajaxCall('/api/gowiththeflow/blocked/unblock', { local_ip: d.local_ip }, function (data) {
                            if (data && data.status !== 'ok') {
                                stdDialogInform(
                                    "{{ lang._('Unblock failed') }}",
                                    (data && data.error) || "{{ lang._('Unknown error') }}",
                                    "{{ lang._('Close') }}", undefined, 'danger'
                                );
                            }
                            refreshBlockedSetGWTF();
                        });
                    },
                    'warning'
                );
            }
        }
    };

    // GETs the current blocked-host set once and applies it to both
    // grids -- called on ready and after every block/unblock so the
    // icon flips immediately rather than waiting for the next ~5s tick
    // (Top Talkers) or a manual reload (Table).
    function refreshBlockedSetGWTF() {
        ajaxGet('/api/gowiththeflow/blocked/list', {}, function (data) {
            gwtfBlockedIps = new Set((data && data.blocked) || []);
            $("#grid-live").bootgrid('reload');
            renderLiveTopTalkers();
        });
    }

    $( document ).ready(function() {
        $("#grid-live").UIBootgrid({
            search:'/api/gowiththeflow/live/search/',
            commands: GWTF_BLOCK_COMMANDS,
            options: {
                selection: false,
                multiSelect: false,
                requestHandler: function (request) {
                    request['local_ip'] = liveFilter.local_ip;
                    request['peer_ip'] = liveFilter.peer_ip;
                    request['peer_port'] = liveFilter.peer_port;
                    request['host_ip'] = liveFilter.host_ip;
                    return request;
                },
                formatters: {
                    "bytesformatter": function (column, row) {
                        return formatBytesGWTF(row[column.id]);
                    },
                    "durationformatter": function (column, row) {
                        return formatDurationGWTF(row[column.id]);
                    },
                    "timestampformatter": function (column, row) {
                        return formatTimestampGWTF(row[column.id]);
                    }
                }
            }
        });
        addCsvExportButtonGWTF('grid-live', 'gowiththeflow-live.csv');

        // Local (non-ajax) mode -- this grid's data is entirely computed
        // client-side from data the Worker already fetches (see
        // renderLiveTopTalkers()), not from a server search endpoint, so
        // there's no `search:` URL. `ajax: false` switches the wrapper's
        // sort/filter/paginate to Tabulator's own local-data mode; rows
        // are pushed in via setData() each tick instead of an ajax poll.
        $("#grid-live-toptalkers").UIBootgrid({
            // Required for renderLiveTopTalkers()'s updateOrAddData()/
            // deleteRow() calls to actually match rows by host -- without
            // this the wrapper's real Tabulator `index` silently defaults
            // to a `uuid` field this data never has (confirmed by reading
            // the wrapper source: `datakey` here becomes `this.options.
            // datakey`, which becomes Tabulator's own `index` config), so
            // every row would collide on the same undefined index instead
            // of being matched/updated individually.
            datakey: 'row_id',
            commands: GWTF_BLOCK_COMMANDS,
            options: {
                ajax: false,
                selection: false,
                multiSelect: false,
                formatters: {
                    "bytesformatter": function (column, row) {
                        return formatBytesGWTF(row[column.id]);
                    }
                }
            },
            // Tabulator's own persistence (wrapper default: `sort: true`)
            // writes whatever sort a user clicks into localStorage and
            // silently restores it on the next rebuild -- for a table
            // that's rebuilt via setData() every ~5s rather than loaded
            // once, that meant the header's sort-direction icon could
            // keep showing the default ("desc") while a stale persisted
            // sort quietly kept winning on the actual row order. Same
            // defaults as the wrapper otherwise uses, just sort:false.
            tabulatorOptions: {
                persistence: {
                    sort: false,
                    filter: false,
                    headerFilter: true,
                    group: true,
                    page: false,
                    columns: true,
                },
                // Also re-forced explicitly after every single data
                // update in renderLiveTopTalkers() -- confirmed live that
                // neither this alone nor an imperative table.setSort()
                // called from a "tableBuilt" handler (when the table
                // still has zero rows) survives into data arriving via
                // this table's repeated per-tick updates, and that even
                // forcing it once after the *first* update stops holding
                // once a later tick's update lands. Kept anyway as the
                // correct declarative baseline/initial state; the
                // update-then-setSort() call every tick is the one
                // actually relied on for every tick after that.
                initialSort: [{ column: "window_bytes_total", dir: "desc" }],
                // Confirmed via a real Performance recording (and by
                // reading Tabulator's own bundled source): every single
                // updateOrAddData() call, per row, was triggering a
                // synchronous height re-measurement (Row.js calcHeight()
                // -> calcMaxHeight() -> Cell.js getHeight() -> a forced
                // `offsetHeight` read), plus a second full-table sweep of
                // the same measurement right after -- together the
                // dominant cost of every ~5s tick. Tabulator's own
                // calcHeight() skips all of that entirely and just uses
                // this value directly when `rowHeight` is set (`this.
                // table.options.rowHeight ? this.height = rowHeight :
                // (...expensive measuring...)`). 28px is computed, not
                // guessed, from this table's actual real rendered cell
                // metrics (`opnsense-bootgrid.css`/`tabulator.min.css`:
                // 4px top+bottom cell padding, 15px font-size, 1.2
                // line-height, 1px border) -- comfortably covers every
                // column here since none of them wrap (nowrap/ellipsis).
                rowHeight: 28,
            }
        });
        addCsvExportButtonGWTF('grid-live-toptalkers', 'gowiththeflow-live-toptalkers.csv');
        // Populates gwtfBlockedIps for the very first renderLiveTopTalkers()
        // tick -- #grid-live's own initial ajax load already carries its
        // `blocked` field straight from LiveController.php, so this is only
        // strictly needed for Top Talkers, but it re-reloads #grid-live too
        // for free.
        refreshBlockedSetGWTF();
        let topTalkersTable = $("#grid-live-toptalkers").data('UIBootgrid').getTable();
        topTalkersTable.on("rowClick", function (e, row) {
            const data = row.getData();
            filterLiveTableByLocalHostGWTF(data.row_id, data.local);
        });
        // `data-type="numeric"` only picks a cell *formatter* (see
        // opnsense_bootgrid.js's _parseColumns()) -- it never sets a real
        // Tabulator `sorter`, so a column left at Tabulator's own default
        // sorts as a string. That was invisible on every other column
        // here because they're only ever sorted by a real user header
        // click, by which point Tabulator has genuine numeric data to
        // auto-type against -- but window_bytes_total is also sorted
        // programmatically (see renderLiveTopTalkers()) the moment the
        // very first tick's data lands, and that raced Tabulator's own
        // type auto-detection and locked in a string sorter (confirmed
        // live: it sorted lexicographically, e.g. "38.7 MB" before
        // "5.6 MB"). Force a real numeric sorter explicitly on every
        // byte/count column here so none of them are left to guesswork.
        topTalkersTable.on("tableBuilt", function () {
            [
                "min1_bytes_in", "min1_bytes_out", "min1_bytes_total", "refresh_conn_count",
                "window_bytes_in", "window_bytes_out", "window_bytes_total", "window_conn_count",
            ].forEach(function (field) {
                const col = topTalkersTable.getColumn(field);
                if (col) {
                    col.updateDefinition({ sorter: "number" });
                }
            });
        });

        // The `data-sort="desc"` header attribute (also used, equally
        // ineffectively, on Top Talkers' bytes_total column) isn't actually
        // read anywhere in opnsense_bootgrid.js -- only `data-sorter` is
        // (which picks a sort *function*, not a direction). The real
        // mechanism is Tabulator's own setSort(), called once the table
        // is actually built so it doesn't race the wrapper's own init.
        let liveTable = $("#grid-live").data('UIBootgrid').getTable();
        liveTable.on("tableBuilt", function () {
            liveTable.setSort("last_activity", "desc");
        });

        // Same convention as Reporting > Traffic's interval dropdown:
        // same option values/labels, localStorage-persisted, self-rescheduling
        // poller so a mid-flight change takes effect on the next tick without
        // needing to tear down and rebuild anything. Also doubles as the
        // Overview chart's bucket width -- one control drives both, since
        // they're naturally the same tick anyway.
        const storageKey = 'gowiththeflow.live.interval';
        if (window.localStorage) {
            let stored = window.localStorage.getItem(storageKey);
            if (stored) {
                $("#interval").val(stored).selectpicker('refresh');
            }
            let storedTopN = window.localStorage.getItem('gowiththeflow.live.topN');
            if (storedTopN !== null) {
                $("#live-top-n").val(storedTopN).selectpicker('refresh');
                liveChartTopN = parseInt(storedTopN, 10) || 0;
            }
            let storedScale = window.localStorage.getItem('gowiththeflow.live.scale');
            if (storedScale) {
                $("#live-scale").val(storedScale).selectpicker('refresh');
                liveChartLogScale = storedScale === 'log';
            }
        }

        $("#interval").change(function () {
            if (window.localStorage) {
                window.localStorage.setItem(storageKey, $(this).val());
            }
            // Only affects how often the Table tab/Graph view refresh --
            // the chart's own point spacing is fixed to the server's real
            // tick rate regardless of this setting.
            if (liveOverviewWorker) {
                liveOverviewWorker.postMessage({ type: 'setInterval', intervalMs: gwtfCurrentPollIntervalMs() });
            }
        });

        $("#live-top-n").on("changed.bs.select", function () {
            liveChartTopN = parseInt($(this).val(), 10) || 0;
            if (window.localStorage) {
                window.localStorage.setItem('gowiththeflow.live.topN', String(liveChartTopN));
            }
            renderLiveChart();
        });

        $("#live-scale").on("changed.bs.select", function () {
            liveChartLogScale = $(this).val() === 'log';
            if (window.localStorage) {
                window.localStorage.setItem('gowiththeflow.live.scale', $(this).val());
            }
            renderLiveChart();
        });

        // The chart's own poll runs inside a Worker (see
        // LiveController::overviewWorkerAction()) rather than on this
        // page's own setTimeout chain -- a backgrounded tab gets its own
        // timers throttled by the browser (sometimes to a full stop),
        // which is exactly what produced real gaps in the chart when the
        // user switched away and back. A Worker isn't tied to a
        // document's visibility state, so it keeps polling on schedule
        // the whole time the tab is hidden. It's deliberately a separate
        // poll from the Table tab's own Bootgrid ajax call -- the table's
        // response is one Bootgrid page (default 50 rows) of a result
        // that can easily be larger on a busy network, and last_seen
        // bumping on every still-open session every tick means which
        // sessions land on that one page is essentially arbitrary. A
        // dominant real host's traffic (confirmed with a phone running
        // speedtest.net) could silently never appear on the chart/graph
        // at all if its rows just weren't on the page the table happened
        // to be showing.
        gwtfStartLiveOverviewWorker();

        (function tableReloadPoller() {
            const interval = gwtfCurrentPollIntervalMs();
            if (interval <= 0) {
                // "Don't refresh" -- do nothing, but keep checking in case
                // the user changes the dropdown again later.
                setTimeout(tableReloadPoller, 2000);
                return;
            }
            setTimeout(function () {
                $("#grid-live").bootgrid('reload');
                tableReloadPoller();
            }, interval);
        })();

        $("#live-group-by").on("changed.bs.select", function () {
            chartHistory = [];
            hiddenGroupKeys = new Set();
            renderLiveChart();
            // Forces the worker's next series fetch to return everything
            // currently retained again (its own `since` watermark, not
            // just this tab's local chartHistory, gated what came back) --
            // without this, the chart would sit empty until new ticks
            // slowly trickle in under the new grouping, instead of
            // immediately repopulating with the real retained history.
            if (liveOverviewWorker) {
                liveOverviewWorker.postMessage({ type: 'resetSeries' });
            }
        });

        $("#live-chart-type").on("changed.bs.select", function () {
            const chartType = $(this).val() || 'line';
            $("#live-chart-canvas-wrapper").toggle(chartType !== 'graph');
            $("#live-graph-wrapper").toggle(chartType === 'graph');
            // Top N/Scale only mean anything for Line/Bar -- Graph shows
            // every host/edge currently open, uncapped, unconditionally.
            $("#live-linebar-controls").toggle(chartType !== 'graph');
            renderLiveChart();
            // Graph mode is only ever driven from updateLiveOverview() on a
            // poll tick -- without this, switching to it shows nothing at
            // all until the next tick happens to land.
            if (chartType === 'graph' && lastRows) {
                renderLiveGraph(lastRows);
            }
        });

        $('a[href="#live-table"]').on('shown.bs.tab', function () {
            // The grid lives in a tab that may have been hidden at load
            // time -- an IntersectionObserver elsewhere already handles
            // redrawing it once visible, matching every other tabbed grid
            // in this plugin.
        });

        // Overview and Top Talkers both skip their own per-tick render
        // while hidden (see gwtfIsTabPaneActive() and its call sites) --
        // force one immediate render from whatever's already cached the
        // moment either becomes visible again, so switching tabs doesn't
        // show data that's stale by more than the last ~5s tick.
        $('a[href="#live-overview"]').on('shown.bs.tab', function () {
            renderLiveChart();
            if ($("#live-chart-type").val() === 'graph' && lastRows) {
                renderLiveGraph(lastRows);
            }
        });

        $('a[href="#live-toptalkers"]').on('shown.bs.tab', function () {
            // Confirmed live on nostromo: this grid is constructed while
            // its pane is still hidden (`display:none`, since Overview is
            // the default active tab), and Tabulator lays out a hidden
            // table against a zero-size container -- its own built-in
            // IntersectionObserver (see opnsense_bootgrid.js) calls a
            // plain `redraw()` once the pane becomes visible, but that
            // wasn't enough to fully fix an already-degenerate header
            // (seen as the header nearly collapsed to a few pixels on
            // first view). `redraw(true)` forces Tabulator to fully
            // recompute layout against the now-real dimensions, same as
            // if the table had just been resized.
            topTalkersTable.redraw(true);
            // Same root cause as the header collapse: `initialSort`'s own
            // effect on the header's sort-arrow icon didn't survive
            // construction while hidden either, and renderLiveTopTalkers()
            // only calls setSort() when there's actually data to push --
            // if the tab is shown before the very first tick has delivered
            // any rows yet, the arrow wouldn't otherwise appear until that
            // tick lands (confirmed live: "arrow only appears after the
            // next refresh"). Force it explicitly here too, wrapped
            // defensively since a sort-reapply this early has already
            // been seen to throw inside Tabulator's own Sort.js once
            // (see the .catch() in renderLiveTopTalkers()).
            try {
                const activeSort = topTalkersTable.getSorters().length
                    ? topTalkersTable.getSorters().map(function (s) { return { column: s.field, dir: s.dir }; })
                    : [{ column: "window_bytes_total", dir: "desc" }];
                topTalkersTable.setSort(activeSort);
            } catch (e) {
                console.warn('gowiththeflow: top talkers initial sort-icon set failed (self-heals next tick)', e);
            }
            renderLiveTopTalkers();
        });

        $("#live-filter-clear").on('click', function () {
            clearLiveFilterGWTF();
        });
    });

    // Starts the Worker that owns the Overview chart's poll loop (see
    // LiveController::overviewWorkerAction() for why it's a Worker at all,
    // and why that Worker is served from a real 'self' URL rather than a
    // blob: URL built here). Falls back to a plain same-thread $.ajax poll
    // if Workers aren't available at all -- subject to the same
    // background-tab throttling this was built to avoid, but still better
    // than the chart never updating.
    function gwtfStartLiveOverviewWorker() {
        if (typeof Worker === 'undefined') {
            // Rare fallback (no Worker support at all) -- polls both
            // endpoints itself on the page's own throttleable timer, and
            // doesn't support the resetSeries fast-path on a grouping
            // change (it'll still catch up, just gradually as new ticks
            // arrive, rather than repopulating full history instantly).
            let fallbackSeriesSince = 0;
            (function fallbackPoll() {
                $.ajax({
                    url: '/api/gowiththeflow/live/overview', type: 'POST', dataType: 'json'
                }).done(function (response) {
                    updateLiveOverview(response.rows || []);
                });
                $.ajax({
                    url: '/api/gowiththeflow/live/series', type: 'POST', dataType: 'json',
                    data: { since: fallbackSeriesSince }
                }).done(function (response) {
                    const ticks = response.ticks || [];
                    ticks.forEach(function (row) {
                        fallbackSeriesSince = Math.max(fallbackSeriesSince, row.tick_time);
                    });
                    gwtfAppendSeriesTicks(ticks);
                }).always(function () {
                    setTimeout(fallbackPoll, gwtfCurrentPollIntervalMs());
                });
            })();
            return;
        }
        liveOverviewWorker = new Worker('/ui/gowiththeflow/live/overviewWorker');
        liveOverviewWorker.onmessage = function (e) {
            const msg = e.data || {};
            if (msg.type === 'poll') {
                updateLiveOverview(msg.rows || []);
                gwtfAppendSeriesTicks(msg.ticks || []);
                // Always re-rendered, even on a tick with no new series
                // data -- currently-open connection counts (from `rows`)
                // can change every poll regardless of whether a new
                // live_ticks batch happened to arrive in the same cycle.
                renderLiveTopTalkers();
            }
        };
        liveOverviewWorker.postMessage({ type: 'setInterval', intervalMs: gwtfCurrentPollIntervalMs() });
    }

    // The current session snapshot (Table tab, Graph view) -- no longer
    // computes the chart's deltas at all (see gwtfAppendSeriesTicks()),
    // just caches the raw rows and keeps groupLabels (hostnames) fresh
    // for whichever grouping is currently selected.
    function updateLiveOverview(rows) {
        lastRows = rows;
        const nowMs = Date.now();
        rows.forEach(function (row) {
            const key = window.__gwtfGroupBy === 'peer_port' ? String(row.peer_port) : row.local_ip;
            groupLabels[key] = window.__gwtfGroupBy === 'peer_port' ? String(row.peer_port) : row.local;
            localHostLabels[row.local_ip] = row.local;

            if (!hostConnSeenTimes[row.local_ip]) {
                hostConnSeenTimes[row.local_ip] = new Map();
            }
            hostConnSeenTimes[row.local_ip].set(row.row_id, nowMs);
        });
        // Prune every known host, not just ones in this tick's rows --
        // otherwise a host with zero currently-open connections would
        // never get re-visited here again and its old entries would
        // never age out.
        const cutoffMs = nowMs - LIVE_CHART_RANGE_MINUTES * 60000;
        Object.keys(hostConnSeenTimes).forEach(function (ip) {
            const seen = hostConnSeenTimes[ip];
            seen.forEach(function (lastSeenMs, rowId) {
                if (lastSeenMs < cutoffMs) {
                    seen.delete(rowId);
                }
            });
            if (seen.size === 0) {
                delete hostConnSeenTimes[ip];
            }
        });
        renderLiveGraph(rows);
    }

    // The chart's actual data source: live_ticks rows computed once,
    // server-side (live_ticks.compute_tick_deltas() in gowiththeflowd.py),
    // so every open tab/viewer reads the same recorded history instead of
    // each independently diffing its own poll -- and a reconnecting tab
    // just fetches the real ticks it missed instead of approximating a
    // gap. `ticks` is a flat list (tick_time, local_ip, peer_port,
    // delta_bytes_in, delta_bytes_out); bucketed here by tick_time into
    // chartHistory's existing {time, groups} shape, summed by whichever
    // of local_ip/peer_port is currently selected.
    function gwtfAppendSeriesTicks(ticks) {
        if (!ticks.length) {
            return;
        }
        const buckets = new Map();
        const hostBuckets = new Map();
        ticks.forEach(function (row) {
            if (!buckets.has(row.tick_time)) {
                buckets.set(row.tick_time, { time: new Date(row.tick_time * 1000), groups: {} });
                hostBuckets.set(row.tick_time, { time: new Date(row.tick_time * 1000), hosts: {} });
            }
            const bucket = buckets.get(row.tick_time);
            const key = window.__gwtfGroupBy === 'peer_port' ? String(row.peer_port) : row.local_ip;
            const bytesTotal = (Number(row.delta_bytes_in) || 0) + (Number(row.delta_bytes_out) || 0);
            bucket.groups[key] = (bucket.groups[key] || 0) + bytesTotal;

            // Always by local_ip -- independent of the chart's own
            // grouping toggle above -- since Top Talkers needs a
            // per-local-host breakdown regardless of what the chart is
            // currently showing.
            const hostBucket = hostBuckets.get(row.tick_time).hosts;
            if (!hostBucket[row.local_ip]) {
                hostBucket[row.local_ip] = { bytesIn: 0, bytesOut: 0 };
            }
            hostBucket[row.local_ip].bytesIn += Number(row.delta_bytes_in) || 0;
            hostBucket[row.local_ip].bytesOut += Number(row.delta_bytes_out) || 0;
        });
        const orderedTimes = Array.from(buckets.keys()).sort(function (a, b) { return a - b; });
        orderedTimes.forEach(function (t) {
            chartHistory.push(buckets.get(t));
            hostWindowHistory.push(hostBuckets.get(t));
        });
        gwtfReconcileChartHistoryLength();
        gwtfReconcileHostWindowHistoryLength();
        renderLiveChart();
    }

    // Same trim-from-front shape as gwtfReconcileChartHistoryLength(), but
    // no empty-placeholder padding -- hostWindowHistory is only ever
    // summed over, never rendered as a fixed-width axis, so there's
    // nothing for an empty placeholder bucket to usefully contribute.
    function gwtfReconcileHostWindowHistoryLength() {
        while (hostWindowHistory.length > GWTF_LIVE_MAX_POINTS) {
            hostWindowHistory.shift();
        }
    }

    // Every local host seen either in the current open-session snapshot
    // (for open-conn counts) or anywhere in the retained window (for
    // hosts that were busy but have since gone quiet) gets a row -- a
    // host isn't dropped just because it has no open connections left at
    // this exact instant.
    function renderLiveTopTalkers() {
        const wrapper = $("#grid-live-toptalkers").data('UIBootgrid');
        if (!wrapper) {
            return;
        }
        // Skip entirely while this pane isn't on screen -- re-rendered
        // immediately from the same always-current bookkeeping the
        // moment the user switches to it (see the "shown.bs.tab" handler
        // below), so nothing is lost, just deferred.
        if (!gwtfIsTabPaneActive('live-toptalkers')) {
            return;
        }
        const table = wrapper.getTable();
        const hostIps = new Set();
        (lastRows || []).forEach(function (row) { hostIps.add(row.local_ip); });
        hostWindowHistory.forEach(function (bucket) {
            Object.keys(bucket.hosts).forEach(function (ip) { hostIps.add(ip); });
        });
        Object.keys(hostConnSeenTimes).forEach(function (ip) { hostIps.add(ip); });

        // One pass over lastRows building a per-host count, rather than
        // re-filtering the whole list once per host (O(hosts + rows)
        // instead of O(hosts * rows) -- with enough distinct hosts on a
        // busy network this was a real, growing contributor to the
        // "message handler took Nms" cost as more hosts accumulated).
        const connCountByHost = {};
        (lastRows || []).forEach(function (row) {
            connCountByHost[row.local_ip] = (connCountByHost[row.local_ip] || 0) + 1;
        });

        // "1 min" byte columns: a trailing moving window over
        // hostWindowHistory's real per-tick bucket timestamps, not "sum
        // of whatever ticks arrived since the last poll" -- that varied
        // with the poll interval and made the number swing volatile tick
        // to tick, per real user feedback. Reuses the same bucket history
        // the 30-minute window columns already retain, just with a
        // shorter look-back cutoff.
        const oneMinCutoffMs = Date.now() - GWTF_TOPTALKERS_RATE_WINDOW_MS;
        const oneMinBuckets = hostWindowHistory.filter(function (bucket) {
            return bucket.time.getTime() >= oneMinCutoffMs;
        });

        const rows = Array.from(hostIps).map(function (ip) {
            let min1BytesIn = 0, min1BytesOut = 0;
            oneMinBuckets.forEach(function (bucket) {
                const h = bucket.hosts[ip];
                if (h) {
                    min1BytesIn += h.bytesIn;
                    min1BytesOut += h.bytesOut;
                }
            });

            let windowBytesIn = 0, windowBytesOut = 0;
            hostWindowHistory.forEach(function (bucket) {
                const h = bucket.hosts[ip];
                if (h) {
                    windowBytesIn += h.bytesIn;
                    windowBytesOut += h.bytesOut;
                }
            });
            const windowConnCount = hostConnSeenTimes[ip] ? hostConnSeenTimes[ip].size : 0;

            return {
                row_id: ip,
                local: localHostLabels[ip] || ip,
                local_ip: ip,
                blocked: gwtfBlockedIps.has(ip) ? 1 : 0,
                min1_bytes_in: min1BytesIn,
                min1_bytes_out: min1BytesOut,
                min1_bytes_total: min1BytesIn + min1BytesOut,
                refresh_conn_count: connCountByHost[ip] || 0,
                window_bytes_in: windowBytesIn,
                window_bytes_out: windowBytesOut,
                window_bytes_total: windowBytesIn + windowBytesOut,
                window_conn_count: windowConnCount,
            };
        });

        // Confirmed live (browser Performance panel, real production
        // traffic): Tabulator's own updateOrAddData() does NOT skip a row
        // whose field values are unchanged -- every row it's given still
        // runs the full per-cell height/layout bookkeeping (Row.js/
        // Cell.js's own setCellHeight()/setHeight(), which reads
        // offsetHeight unconditionally, confirmed by reading Tabulator's
        // bundled source), regardless of whether anything actually
        // changed. So the diff is done here instead: only rows that
        // differ from what was last pushed are included at all -- a
        // quiet host that hasn't moved this tick costs nothing.
        const changedRows = [];
        rows.forEach(function (row) {
            const prev = lastToptalkersRowValues[row.row_id];
            const changed = !prev || Object.keys(row).some(function (k) { return prev[k] !== row[k]; });
            if (changed) {
                changedRows.push(row);
                lastToptalkersRowValues[row.row_id] = row;
            }
        });

        // updateOrAddData() never removes rows on its own, so hosts that
        // aged out of every source above are deleted explicitly (and
        // dropped from the diff cache, so a host that reappears later
        // isn't compared against stale numbers from its last visit).
        // deleteRow() accepts an array -- one call for every stale host
        // instead of one call per host, confirmed via Tabulator's own
        // source (`deleteRow(e){...Array.isArray(e)||(e=[e])...}`).
        const currentIds = new Set(table.getData().map(function (r) { return r.row_id; }));
        const newIds = new Set(rows.map(function (r) { return r.row_id; }));
        const staleIds = Array.from(currentIds).filter(function (id) { return !newIds.has(id); });

        // blockRedraw()/restoreRedraw() are real, documented Tabulator
        // methods (confirmed in its bundled source) that defer the
        // actual render pass until explicitly released -- without this,
        // a tick with both stale hosts to remove AND hosts to update
        // would trigger a separate redraw/recalc pass for each deleteRow
        // call plus another for updateOrAddData, instead of exactly one
        // pass covering everything this tick actually changed.
        if (staleIds.length || changedRows.length) {
            table.blockRedraw();
            const pending = [];
            if (staleIds.length) {
                pending.push(table.deleteRow(staleIds));
                staleIds.forEach(function (id) { delete lastToptalkersRowValues[id]; });
            }
            if (changedRows.length) {
                pending.push(table.updateOrAddData(changedRows));
            }
            Promise.all(pending).then(function () {
                table.restoreRedraw();
                if (changedRows.length) {
                    // Tabulator doesn't keep re-applying an active sort
                    // across data updates on its own -- read back
                    // whatever's current (or default to window_bytes_
                    // total/desc) so a user's own manual re-sort
                    // survives ticks too, not just the default. Only
                    // needed when something actually changed this tick.
                    const activeSort = table.getSorters().length
                        ? table.getSorters().map(function (s) { return { column: s.field, dir: s.dir }; })
                        : [{ column: "window_bytes_total", dir: "desc" }];
                    table.setSort(activeSort);
                }
            }).catch(function (e) {
                // Confirmed live on nostromo: restoreRedraw()'s own
                // internal re-sort pass (Tabulator's Sort.js) can throw
                // if this table's very first real redraw after becoming
                // visible races Tabulator's own layout recovery (see the
                // "shown.bs.tab" handler's redraw(true) call, which fixes
                // the root cause) -- self-heals by the very next tick
                // either way, so this is swallowed rather than left as
                // an uncaught rejection in the console.
                console.warn('gowiththeflow: top talkers redraw hiccup (self-heals next tick)', e);
            });
        }
    }

    // Unlike filterLiveTableByGroupGWTF() (which branches on the chart's
    // own local_ip/peer_port grouping toggle), Top Talkers rows are
    // always local hosts, unconditionally.
    function filterLiveTableByLocalHostGWTF(localIp, label) {
        $('a[href="#live-table"]').tab('show');
        setLiveFilterGWTF({ local_ip: localIp }, label || localIp);
    }

    // NOT `|| 2000` -- 0 ("Don't refresh") is falsy in JS, so that would
    // silently fall back to the default and never actually stop
    // refreshing. Only governs the Table tab/Graph view poll rate now --
    // the chart's own cadence is fixed to GWTF_DAEMON_POLL_INTERVAL_MS.
    function gwtfCurrentPollIntervalMs() {
        const parsed = parseInt($("#interval").val(), 10);
        return Number.isNaN(parsed) ? 2000 : parsed;
    }

    // Fixed, not recomputed against a user-chosen interval -- the chart's
    // range (LIVE_CHART_RANGE_MINUTES) and point spacing
    // (GWTF_DAEMON_POLL_INTERVAL_MS, the server's real tick rate) are both
    // constants now.
    const GWTF_LIVE_MAX_POINTS = Math.ceil(
        (LIVE_CHART_RANGE_MINUTES * 60000) / GWTF_DAEMON_POLL_INTERVAL_MS
    );

    // Keeps chartHistory at exactly GWTF_LIVE_MAX_POINTS long, padding
    // with empty placeholder points at the front (stepping backward in
    // time from whatever's already there, or from now if starting from
    // empty) when the server hasn't been running long enough yet to have
    // a full window of real history, and trimming from the front when
    // there's more (the server retains a few minutes more than the
    // display window, see LIVE_TICK_RETENTION_S in gowiththeflowd.py).
    // Empty points contribute 0 to every dataset and are invisible to
    // topGroupKeysGWTF's totals, so they never skew which groups count
    // as "top".
    function gwtfReconcileChartHistoryLength() {
        while (chartHistory.length > GWTF_LIVE_MAX_POINTS) {
            chartHistory.shift();
        }
        while (chartHistory.length < GWTF_LIVE_MAX_POINTS) {
            const oldest = chartHistory[0];
            const t = oldest ? oldest.time.getTime() - GWTF_DAEMON_POLL_INTERVAL_MS : Date.now();
            chartHistory.unshift({ time: new Date(t), groups: {} });
        }
    }

    // liveChartTopN === 0 means "show every group" -- all === top, so
    // the "Other" bucket in renderLiveChart() naturally ends up empty
    // and isn't drawn.
    function topGroupKeysGWTF(groupsHistory) {
        const totals = {};
        groupsHistory.forEach(function (point) {
            Object.keys(point.groups).forEach(function (k) {
                totals[k] = (totals[k] || 0) + point.groups[k];
            });
        });
        const sorted = Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a]; });
        const top = liveChartTopN > 0 ? sorted.slice(0, liveChartTopN) : sorted;
        return { top: top, all: sorted };
    }

    const GWTF_PALETTE = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
                           '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];

    // Reconciles groupColorSlots/usedColorSlots against this tick's full
    // key set (topGroupKeysGWTF's `all`, not just the rendered `top` --
    // a key demoted to "Other" by a busier peer keeps its reserved slot
    // and gets the same color back if it re-enters the top N later).
    // Coloring by array index into a list that gets re-sorted by
    // throughput every render (the original approach) made a host's
    // color change whenever the ranking reshuffled, even while it never
    // stopped being active -- this makes a color stick to its key until
    // that key genuinely has no activity left anywhere in the window.
    function gwtfAssignGroupColors(allKeys) {
        const stillActive = new Set(allKeys);
        groupColorSlots.forEach(function (idx, key) {
            if (!stillActive.has(key)) {
                usedColorSlots.delete(idx);
                groupColorSlots.delete(key);
            }
        });
        allKeys.forEach(function (key) {
            if (groupColorSlots.has(key)) {
                return;
            }
            let idx = 0;
            while (usedColorSlots.has(idx)) {
                idx++;
            }
            groupColorSlots.set(key, idx);
            usedColorSlots.add(idx);
        });
    }

    function gwtfColorForGroup(key) {
        const idx = groupColorSlots.has(key) ? groupColorSlots.get(key) : 0;
        return GWTF_PALETTE[idx % GWTF_PALETTE.length];
    }

    // Shared by both Overview renderers (Line/Bar's canvas wrapper and
    // the Graph view's own wrapper) -- fills the real remaining
    // browser-window height below `el`, not a fixed pixel value that
    // ignores how tall the actual window is. rect.top is relative to
    // the *current scroll position*; adding scrollY back converts it
    // to a scroll-independent distance from the top of the document,
    // which is what actually determines the layout here -- getting
    // this wrong once produced a real runaway-growth bug (scrolling to
    // see clipped content shrank rect.top, which grew the next tick's
    // height, requiring more scroll, compounding forever). Also
    // accounts for OPNsense's own `position: fixed` page footer
    // (`.page-foot`), which overlaps the bottom of the viewport
    // regardless of scroll and isn't reflected in window.innerHeight.
    function gwtfFillTabHeight(el) {
        const documentTop = el.getBoundingClientRect().top + window.scrollY;
        const pageFoot = document.querySelector('.page-foot');
        const footerHeight = pageFoot ? pageFoot.getBoundingClientRect().height : 60;
        const height = Math.max(320, window.innerHeight - documentTop - footerHeight - 16);
        el.style.height = height + 'px';
    }

    // Every ~5s poll tick used to fully re-render whichever of
    // Overview/Top Talkers/Table tab-panes wasn't even the one on
    // screen -- confirmed via the browser's own Performance panel that
    // this was real, wasted style-recalc/layout work (Chart.js's own
    // .update()/.resize(), plus gwtfFillTabHeight()'s layout reads)
    // happening every tick regardless of which tab the user actually
    // had open. Each render function below skips its own DOM work when
    // its pane isn't active; the corresponding "shown.bs.tab" handlers
    // force one immediate re-render from already-cached data on switch,
    // so there's no staleness worse than the last tick's data.
    function gwtfIsTabPaneActive(paneId) {
        const el = document.getElementById(paneId);
        return !!el && el.classList.contains('active');
    }

    function renderLiveChart() {
        const groupBy = $("#live-group-by").val() || 'local_ip';
        const chartType = $("#live-chart-type").val() || 'line';
        window.__gwtfGroupBy = groupBy;
        if (chartType === 'graph') {
            return;
        }
        if (!gwtfIsTabPaneActive('live-overview')) {
            return;
        }

        const { top, all } = topGroupKeysGWTF(chartHistory);
        gwtfAssignGroupColors(all);
        const otherKeys = all.filter(function (k) { return top.indexOf(k) === -1; });
        const labels = chartHistory.map(function (p) { return p.time.toLocaleTimeString(); });

        const datasets = top.map(function (key) {
            return {
                label: groupLabels[key] || key,
                rawKey: key,
                hidden: hiddenGroupKeys.has(key),
                data: chartHistory.map(function (p) { return p.groups[key] || 0; }),
                borderColor: gwtfColorForGroup(key),
                backgroundColor: gwtfColorForGroup(key),
                // Chart.js's own default (3) reads heavy with several
                // lines overlapping -- thinner traces stay legible
                // without one dominant host's line visually burying the
                // smaller ones next to it.
                borderWidth: 1.5,
                fill: chartType === 'bar',
                // 'monotone' smooths the line without letting it
                // overshoot below/above neighboring points the way a
                // plain tension-based Catmull-Rom curve can -- matters
                // here since the y-axis is pinned to a hard floor of 0
                // (a real dip-below-zero-looking artifact between two
                // low points would be actively misleading for a byte
                // count). tension is ignored in this mode; left off.
                cubicInterpolationMode: 'monotone',
            };
        });
        if (otherKeys.length) {
            datasets.push({
                label: '{{ lang._("Other") }}',
                rawKey: null,
                data: chartHistory.map(function (p) {
                    return otherKeys.reduce(function (sum, k) { return sum + (p.groups[k] || 0); }, 0);
                }),
                borderColor: '#999999',
                backgroundColor: '#999999',
                borderWidth: 1.5,
                fill: chartType === 'bar',
                cubicInterpolationMode: 'monotone',
            });
        }

        const config = {
            type: chartType === 'bar' ? 'bar' : 'line',
            data: { labels: labels, datasets: datasets },
            options: {
                maintainAspectRatio: false,
                animation: false,
                scales: {
                    y: {
                        stacked: chartType === 'bar',
                        // Byte counts are never negative -- without this,
                        // Chart.js's auto-range pads symmetrically around 0
                        // when every visible point is still 0 (e.g. right
                        // after load, before real deltas arrive), producing
                        // a nonsensical -1..1 axis instead of sitting at 0.
                        // Not applicable in log mode (0 has no logarithm --
                        // Chart.js's own logarithmic scale picks a sensible
                        // positive floor from the data instead); a 0-byte
                        // point simply doesn't plot a dot on that scale,
                        // same well-known tradeoff any log-axis chart has.
                        type: liveChartLogScale ? 'logarithmic' : 'linear',
                        min: liveChartLogScale ? undefined : 0,
                        ticks: { callback: function (v) { return formatBytesGWTF(v); } }
                    },
                    x: { stacked: chartType === 'bar' }
                },
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                return ctx.dataset.label + ': ' + formatBytesGWTF(ctx.parsed.y);
                            }
                        }
                    },
                    legend: {
                        onClick: function (evt, legendItem, legend) {
                            const ds = legend.chart.data.datasets[legendItem.datasetIndex];
                            const nativeEvt = (evt && evt.native) ? evt.native : evt;
                            // Shift-click toggles that one line on/off in place (so a
                            // dominant host, like the firewall's own admin-plane
                            // traffic, can be hidden without losing sight of everyone
                            // else) -- a plain click keeps jumping to the filtered
                            // Table tab, unchanged. "Other" has no single rawKey to
                            // toggle by, so it's left out of this entirely.
                            if (nativeEvt && nativeEvt.shiftKey && ds.rawKey) {
                                if (hiddenGroupKeys.has(ds.rawKey)) {
                                    hiddenGroupKeys.delete(ds.rawKey);
                                } else {
                                    hiddenGroupKeys.add(ds.rawKey);
                                }
                                renderLiveChart();
                                return;
                            }
                            filterLiveTableByGroupGWTF(ds.rawKey);
                        }
                    }
                },
                onClick: function (evt, elements) {
                    if (elements.length) {
                        const ds = liveChartInstanceGWTF().data.datasets[elements[0].datasetIndex];
                        filterLiveTableByGroupGWTF(ds.rawKey);
                    }
                }
            }
        };

        gwtfFillTabHeight(document.getElementById('live-chart-canvas-wrapper'));

        // Chart.js instantiates a concrete scale object per axis `type`
        // at chart-creation time -- just mutating options.scales.y.type
        // on an already-running chart doesn't reliably switch it between
        // linear and logarithmic. Destroy and recreate when the axis
        // kind actually changes; a plain data/stacked update (the common
        // case, every poll tick) still just mutates in place.
        let existing = liveChartInstanceGWTF();
        if (existing && existing.options.scales.y.type !== config.options.scales.y.type) {
            existing.destroy();
            window.__gwtfLiveChart = null;
            existing = null;
        }

        if (!existing) {
            const ctx = document.getElementById('live-overview-canvas').getContext('2d');
            window.__gwtfLiveChart = new Chart(ctx, config);
        } else {
            existing.config.type = config.type;
            existing.data.labels = config.data.labels;
            existing.data.datasets = config.data.datasets;
            existing.options.scales.y.stacked = config.options.scales.y.stacked;
            existing.options.scales.x.stacked = config.options.scales.x.stacked;
            existing.update();
            existing.resize();
        }
    }

    function liveChartInstanceGWTF() {
        return window.__gwtfLiveChart || null;
    }

    // Server-side filter for the ajax-backed Live grid (Tabulator's own
    // client-side setFilter() only ever filters whatever page of rows is
    // already loaded locally -- it never asked the server for the actual
    // matching set, so a click-through never showed anything). Same
    // requestHandler-param pattern History/Top Talkers already use for
    // their own local_host filter.
    function setLiveFilterGWTF(filter, label) {
        liveFilter = Object.assign({ local_ip: '', peer_ip: '', peer_port: '', host_ip: '' }, filter);
        const active = !!(liveFilter.local_ip || liveFilter.peer_ip || liveFilter.peer_port || liveFilter.host_ip);
        $("#live-filter-label").text(label || '');
        $("#live-filter-indicator").toggle(active);
        $("#grid-live").bootgrid('reload');
    }

    function clearLiveFilterGWTF() {
        setLiveFilterGWTF({}, '');
    }

    function filterLiveTableByGroupGWTF(rawKey) {
        if (!rawKey) {
            return; // "Other" isn't one specific host/port to filter by
        }
        $('a[href="#live-table"]').tab('show');
        if (window.__gwtfGroupBy === 'peer_port') {
            setLiveFilterGWTF({ peer_port: rawKey }, '{{ lang._("Port") }} ' + rawKey);
        } else {
            setLiveFilterGWTF({ local_ip: rawKey }, groupLabels[rawKey] || rawKey);
        }
    }

    // Node click in the Graph view -- a single IP can be local_ip in one
    // session and peer_ip in another, so this matches either side rather
    // than assuming the node is always "the local one."
    function filterLiveTableByHostIpGWTF(ip, label) {
        $('a[href="#live-table"]').tab('show');
        setLiveFilterGWTF({ host_ip: ip }, label || ip);
    }

    // Edge click in the Graph view -- one specific (host, peer,
    // destination port) triple, matching that edge's own granularity.
    function filterLiveTableByTripleGWTF(localIp, peerIp, peerPort, label) {
        $('a[href="#live-table"]').tab('show');
        setLiveFilterGWTF({ local_ip: localIp, peer_ip: peerIp, peer_port: String(peerPort) }, label);
    }

    const GRAPH_NODE_R = 9;
    const GRAPH_RECENCY_FADE_S = 120;      // fully faded once idle this long
    const GRAPH_MIN_EDGE_OPACITY = 0.15;

    // Fixed absolute bands, not a relative-to-current-max gradient --
    // a relative scale would make the same 50KB connection look "red"
    // on a quiet network and "pale blue" on a busy one, which defeats
    // the point of a color cue. Bounds are in bytes (total transferred
    // so far this session); tune freely, this is a first pass.
    const GRAPH_EDGE_BANDS = [
        { maxBytes: 10 * 1024, color: '#9ecae1', label: '< 10 KB' },
        { maxBytes: 100 * 1024, color: '#6baed6', label: '10 KB – 100 KB' },
        { maxBytes: 1024 * 1024, color: '#fd8d3c', label: '100 KB – 1 MB' },
        { maxBytes: 10 * 1024 * 1024, color: '#e6550d', label: '1 MB – 10 MB' },
        { maxBytes: Infinity, color: '#d62728', label: '> 10 MB' },
    ];

    function gwtfEdgeColor(bytes) {
        for (let i = 0; i < GRAPH_EDGE_BANDS.length; i++) {
            if (bytes <= GRAPH_EDGE_BANDS[i].maxBytes) {
                return GRAPH_EDGE_BANDS[i].color;
            }
        }
        return GRAPH_EDGE_BANDS[GRAPH_EDGE_BANDS.length - 1].color;
    }

    // Force-directed layout (Fruchterman-Reingold-ish: all-pairs
    // repulsion + spring-like attraction along edges + a mild pull
    // toward center so the graph doesn't drift off-canvas), not a fixed
    // ring -- real feedback was that a ring reads as "radial," not "a
    // network." Positions persist in forceNodePositions across ticks and
    // are only nudged a little each time (not recomputed from scratch),
    // so the layout gently relaxes as edges/nodes come and go instead of
    // jumping around every poll. O(n^2) per iteration from the repulsion
    // pass is trivial at realistic host counts (a few ms even for a
    // few hundred nodes) -- revisit only if that stops being true.
    function gwtfRelaxForceLayout(nodeIps, edgePairs, width, height, iterations) {
        const centerX = width / 2, centerY = height / 2;
        const nodeIpSet = new Set(nodeIps);
        Object.keys(forceNodePositions).forEach(function (ip) {
            if (!nodeIpSet.has(ip)) { delete forceNodePositions[ip]; }
        });
        // Standard Fruchterman-Reingold heuristic for the "ideal" edge
        // length given the available area and node count.
        const k = Math.sqrt((width * height) / Math.max(nodeIps.length, 1));

        nodeIps.forEach(function (ip) {
            if (!forceNodePositions[ip]) {
                const angle = Math.random() * 2 * Math.PI;
                const r = Math.min(width, height) * 0.2 * Math.random();
                forceNodePositions[ip] = { x: centerX + r * Math.cos(angle), y: centerY + r * Math.sin(angle) };
            }
        });

        for (let iter = 0; iter < iterations; iter++) {
            const disp = {};
            nodeIps.forEach(function (ip) { disp[ip] = { x: 0, y: 0 }; });

            for (let i = 0; i < nodeIps.length; i++) {
                for (let j = i + 1; j < nodeIps.length; j++) {
                    const a = nodeIps[i], b = nodeIps[j];
                    const pa = forceNodePositions[a], pb = forceNodePositions[b];
                    const dx = pa.x - pb.x, dy = pa.y - pb.y;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
                    const force = (k * k) / dist;
                    const fx = (dx / dist) * force, fy = (dy / dist) * force;
                    disp[a].x += fx; disp[a].y += fy;
                    disp[b].x -= fx; disp[b].y -= fy;
                }
            }

            edgePairs.forEach(function (pair) {
                const pa = forceNodePositions[pair[0]], pb = forceNodePositions[pair[1]];
                const dx = pa.x - pb.x, dy = pa.y - pb.y;
                const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
                const force = (dist * dist) / k;
                const fx = (dx / dist) * force, fy = (dy / dist) * force;
                disp[pair[0]].x -= fx; disp[pair[0]].y -= fy;
                disp[pair[1]].x += fx; disp[pair[1]].y += fy;
            });

            const temp = Math.max(1, k * 0.15 * (1 - iter / iterations));
            nodeIps.forEach(function (ip) {
                const dx = disp[ip].x, dy = disp[ip].y;
                const len = Math.sqrt(dx * dx + dy * dy) || 0.01;
                const capped = Math.min(len, temp);
                const pos = forceNodePositions[ip];
                pos.x += (dx / len) * capped;
                pos.y += (dy / len) * capped;
                // Mild pull toward center -- keeps a lightly-connected
                // graph from slowly drifting off-canvas over many ticks.
                pos.x += (centerX - pos.x) * 0.01;
                pos.y += (centerY - pos.y) * 0.01;
            });
        }
    }

    // Experimental "try it and see" renderer -- a force-directed network
    // graph: every unique host (local or peer) is a node, one edge per
    // (local host, peer, destination port) triple -- not collapsed by
    // host+peer alone, since two different ports to the same peer are
    // two genuinely different things to look at. Edge color encodes
    // current throughput (light blue -> red); edge
    // opacity encodes how long since last_activity, so an idle-but-not-
    // yet-closed connection visibly fades even while it's technically
    // still "live" -- directly answering the "what does Last Seen even
    // mean" question this feature grew out of. An arrowhead at each
    // edge's midpoint points local_ip -> peer_ip, i.e. the side pf itself
    // recorded as the connection's source. Clicking a node or edge jumps
    // to the Table tab filtered accordingly. Deliberately uncapped --
    // every host and every edge currently open is shown, not just the
    // busiest N -- so a host doesn't silently vanish just because its
    // own traffic is small next to everyone else's; sorted by bytes only
    // so the busiest edges get first pick of curve-offset slots, not to
    // decide what's shown at all.
    function renderLiveGraph(rows) {
        const wrapper = document.getElementById('live-graph-wrapper');
        if (!wrapper || $("#live-chart-type").val() !== 'graph' || !gwtfIsTabPaneActive('live-overview')) {
            return;
        }

        const nowS = Date.now() / 1000;
        const edgeTotals = {};  // "local|peer|port" -> {localIp, peerIp, peerPort, bytes, lastActivity}
        const labelByIp = {};
        rows.forEach(function (row) {
            const key = row.local_ip + '|' + row.peer_ip + '|' + row.peer_port;
            if (!edgeTotals[key]) {
                edgeTotals[key] = { localIp: row.local_ip, peerIp: row.peer_ip, peerPort: row.peer_port, bytes: 0, lastActivity: 0 };
            }
            edgeTotals[key].bytes += (Number(row.bytes_in) || 0) + (Number(row.bytes_out) || 0);
            edgeTotals[key].lastActivity = Math.max(edgeTotals[key].lastActivity, Number(row.last_activity) || 0);
            labelByIp[row.local_ip] = row.local;
            labelByIp[row.peer_ip] = row.peer;
        });

        let edgeKeys = Object.keys(edgeTotals);
        edgeKeys.sort(function (a, b) { return edgeTotals[b].bytes - edgeTotals[a].bytes; });
        const edges = {};
        edgeKeys.forEach(function (k) { edges[k] = edgeTotals[k]; });

        // Node set: real edges' endpoints, plus any still-fading edge's
        // endpoints too (so a node doesn't vanish before its own edge
        // finishes fading out) -- but the fading edge itself is NOT
        // reprocessed below; its own CSS transition is left alone.
        const nodeIpSet = new Set();
        edgeKeys.forEach(function (k) { nodeIpSet.add(edges[k].localIp); nodeIpSet.add(edges[k].peerIp); });
        Object.keys(graphNodes).forEach(function (k) {
            const n = graphNodes[k];
            if (n.fading) { nodeIpSet.add(n.localIp); nodeIpSet.add(n.peerIp); }
        });
        const nodeIps = Array.from(nodeIpSet);

        let legend = wrapper.querySelector('.gwtf-graph-legend');
        if (!legend) {
            legend = document.createElement('div');
            legend.className = 'gwtf-graph-legend';
            legend.textContent = '{{ lang._("Edge color = total bytes so far") }}: ';
            GRAPH_EDGE_BANDS.forEach(function (band) {
                const swatch = document.createElement('span');
                swatch.className = 'gwtf-graph-legend-swatch';
                swatch.style.backgroundColor = band.color;
                legend.appendChild(swatch);
                const text = document.createElement('span');
                text.className = 'gwtf-graph-legend-text';
                text.textContent = band.label;
                legend.appendChild(text);
            });
            wrapper.appendChild(legend);
        }

        let svg = wrapper.querySelector('svg');
        if (!svg) {
            svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('class', 'gwtf-graph-svg');
            wrapper.insertBefore(svg, legend);
            graphNodes = {};
        }

        gwtfFillTabHeight(wrapper);

        // Let flexbox finish laying out the legend at its natural size
        // and the svg at whatever's left, then read the svg's own
        // resulting box back for the coordinate math below, rather than
        // computing width/height independently in JS and hoping they
        // match what CSS actually renders.
        const svgRect = svg.getBoundingClientRect();
        const width = Math.max(200, Math.round(svgRect.width) || wrapper.clientWidth || 600);
        const height = Math.max(150, Math.round(svgRect.height) || 300);
        const centerX = width / 2, centerY = height / 2;
        svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);

        // One attraction spring per distinct (host, peer) pair, not per
        // edge -- several ports to the same peer pulling on the same
        // pair of nodes N times over would just distort the layout
        // without adding any real information the color/opacity/arrow
        // per individual edge doesn't already carry.
        const pairKeySet = new Set();
        edgeKeys.forEach(function (k) {
            pairKeySet.add([edges[k].localIp, edges[k].peerIp].sort().join('|'));
        });
        const edgePairs = Array.from(pairKeySet).map(function (pk) { return pk.split('|'); });
        gwtfRelaxForceLayout(nodeIps, edgePairs, width, height, 20);
        const nodePos = forceNodePositions;

        // Multiple edges between the same two nodes (different
        // destination ports) fan out via increasing curvature instead of
        // drawing directly on top of each other.
        const curvePairIndex = {};
        function nextCurveOffset(ipA, ipB) {
            const pairKey = [ipA, ipB].sort().join('|');
            const i = curvePairIndex[pairKey] || 0;
            curvePairIndex[pairKey] = i + 1;
            return (i % 2 === 0 ? 1 : -1) * Math.ceil((i + 1) / 2) * 18;
        }

        const now = Date.now();
        edgeKeys.forEach(function (key) {
            const e = edges[key];
            const p1 = nodePos[e.localIp], p2 = nodePos[e.peerIp];
            let node = graphNodes[key];
            if (!node) {
                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('class', 'gwtf-graph-edge-line');
                path.style.cursor = 'pointer';
                path.addEventListener('click', function () {
                    filterLiveTableByTripleGWTF(e.localIp, e.peerIp, e.peerPort,
                        (labelByIp[e.localIp] || e.localIp) + ' → ' + (labelByIp[e.peerIp] || e.peerIp) + ':' + e.peerPort);
                });
                svg.appendChild(path);
                node = {
                    el: path, localIp: e.localIp, peerIp: e.peerIp, peerPort: e.peerPort,
                    curveOffset: nextCurveOffset(e.localIp, e.peerIp),
                };
                graphNodes[key] = node;
            }

            const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2;
            const dx = p2.x - p1.x, dy = p2.y - p1.y;
            const len = Math.sqrt(dx * dx + dy * dy) || 1;
            const cx = mx + (-dy / len) * node.curveOffset;
            const cy = my + (dx / len) * node.curveOffset;
            node.el.setAttribute('d', 'M ' + p1.x + ' ' + p1.y + ' Q ' + cx + ' ' + cy + ' ' + p2.x + ' ' + p2.y);
            const color = gwtfEdgeColor(e.bytes);
            node.el.setAttribute('stroke', color);

            if (!node.arrowEl) {
                const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
                arrow.setAttribute('points', '-5,-4 5,0 -5,4');
                arrow.setAttribute('class', 'gwtf-graph-edge-arrow');
                svg.appendChild(arrow);
                node.arrowEl = arrow;
            }
            // Midpoint of the quadratic bezier at t=0.5 (NOT the straight
            // chord's midpoint -- it sits on the actual curve). The
            // bezier's tangent at t=0.5 is always parallel to p2-p1
            // regardless of curvature, so atan2(dy, dx) is the right
            // rotation even though the curve visibly bows away from that
            // straight line. Direction is local_ip -> peer_ip -- the side
            // pf itself recorded as source for this specific flow.
            const midX = 0.25 * p1.x + 0.5 * cx + 0.25 * p2.x;
            const midY = 0.25 * p1.y + 0.5 * cy + 0.25 * p2.y;
            const angleDeg = (Math.atan2(dy, dx) * 180) / Math.PI;
            node.arrowEl.setAttribute('transform', 'translate(' + midX + ',' + midY + ') rotate(' + angleDeg + ')');
            node.arrowEl.setAttribute('fill', color);

            const ageS = Math.max(0, nowS - e.lastActivity);
            const opacity = Math.max(GRAPH_MIN_EDGE_OPACITY, 1 - ageS / GRAPH_RECENCY_FADE_S);
            node.el.style.transition = '';
            node.el.style.opacity = opacity;
            node.arrowEl.style.transition = '';
            node.arrowEl.style.opacity = opacity;
            node.fading = false;
            node.lastSeen = now;
        });

        // Fade out and remove any edge not seen in this poll -- once an
        // edge starts fading it's left alone here on every later tick
        // until its own setTimeout below actually removes it.
        Object.keys(graphNodes).forEach(function (key) {
            const node = graphNodes[key];
            if (node.lastSeen === now || node.fading) {
                return;
            }
            node.fading = true;
            node.el.style.transition = 'opacity ' + GRAPH_FADE_MS + 'ms';
            node.el.style.opacity = 0;
            if (node.arrowEl) {
                node.arrowEl.style.transition = 'opacity ' + GRAPH_FADE_MS + 'ms';
                node.arrowEl.style.opacity = 0;
            }
            setTimeout(function () {
                if (node.el.parentNode) {
                    node.el.parentNode.removeChild(node.el);
                }
                if (node.arrowEl && node.arrowEl.parentNode) {
                    node.arrowEl.parentNode.removeChild(node.arrowEl);
                }
                delete graphNodes[key];
            }, GRAPH_FADE_MS);
        });

        Array.prototype.forEach.call(svg.querySelectorAll('.gwtf-graph-node, .gwtf-graph-label'), function (el) { el.remove(); });

        nodeIps.forEach(function (ip) {
            const pos = nodePos[ip];
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', pos.x);
            circle.setAttribute('cy', pos.y);
            circle.setAttribute('r', GRAPH_NODE_R);
            circle.setAttribute('class', 'gwtf-graph-node');
            circle.style.cursor = 'pointer';
            circle.addEventListener('click', function () {
                filterLiveTableByHostIpGWTF(ip, labelByIp[ip] || ip);
            });
            svg.appendChild(circle);

            const name = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            const pointsRight = pos.x >= centerX;
            name.setAttribute('x', pos.x + (pointsRight ? 1 : -1) * (GRAPH_NODE_R + 6));
            name.setAttribute('y', pos.y + 4);
            name.setAttribute('text-anchor', pointsRight ? 'start' : 'end');
            name.setAttribute('class', 'gwtf-graph-label gwtf-graph-name-label');
            name.textContent = labelByIp[ip] || ip;
            svg.appendChild(name);
        });
    }

    // Reuses the grid's own action-button row (the same one the built-in
    // reset/maximize buttons live in) rather than a separate ad-hoc button,
    // and Tabulator's own download() so it respects whatever's currently
    // sorted/filtered/loaded rather than us re-serializing the data by hand.
    function addCsvExportButtonGWTF(gridId, filename) {
        $(`
            <button id="${gridId}-export" class="btn btn-default" type="button" data-toggle="tooltip"
                    title="{{ lang._('Export CSV') }}">
                <span class="icon fa-solid fa-download"></span>
            </button>
        `).on('click', function () {
            $("#" + gridId).data('UIBootgrid').getTable().download("csv", filename);
        }).appendTo('#' + gridId + '-actions-group');
    }

    function formatBytesGWTF(bytes) {
        if (bytes === undefined || bytes === null) {
            return "";
        }
        const units = ["B", "KB", "MB", "GB", "TB"];
        let i = 0;
        let value = bytes;
        while (value >= 1024 && i < units.length - 1) {
            value /= 1024;
            i++;
        }
        return value.toFixed(i === 0 ? 0 : 1) + " " + units[i];
    }

    function formatTimestampGWTF(unixSeconds) {
        if (unixSeconds === undefined || unixSeconds === null) {
            return "";
        }
        return new Date(unixSeconds * 1000).toLocaleString();
    }

    function formatDurationGWTF(seconds) {
        if (seconds === undefined || seconds === null) {
            return "";
        }
        let h = Math.floor(seconds / 3600);
        let m = Math.floor((seconds % 3600) / 60);
        let s = Math.floor(seconds % 60);
        let parts = [];
        if (h > 0) { parts.push(h + "h"); }
        if (h > 0 || m > 0) { parts.push(m + "m"); }
        parts.push(s + "s");
        return parts.join(" ");
    }
</script>

<style>
    #live-graph-wrapper {
        display: flex; flex-direction: column; overflow: auto; position: relative;
    }
    .gwtf-graph-svg {
        display: block; width: 100%; flex: 1 1 auto; min-height: 0;
    }
    .gwtf-graph-edge-line {
        fill: none; stroke-width: 2;
    }
    .gwtf-graph-edge-arrow {
        pointer-events: none;
    }
    .gwtf-graph-node {
        fill: #4e79a7; stroke: #fff; stroke-width: 1.5;
    }
    .gwtf-graph-name-label {
        font-size: 10px; fill: currentColor; pointer-events: none;
    }
    .gwtf-graph-legend {
        font-size: 11px; margin-top: 6px; flex: 0 0 auto;
    }
    .gwtf-graph-legend-swatch {
        display: inline-block; width: 10px; height: 10px; border-radius: 2px;
        margin: 0 4px 0 10px; vertical-align: middle;
    }
    .gwtf-graph-legend-text {
        vertical-align: middle;
    }
    /* OPNsense's own theme CSS has a `:hover` rule for Tabulator rows,
       but it's gated on `.tabulator-selectable` (only added when a grid
       has row selection enabled -- none of this plugin's grids do) AND
       re-asserts the exact same background as the resting state either
       way, so it has no visible effect regardless. A translucent overlay
       (rather than a hardcoded color) highlights the hovered row without
       needing to match this theme's exact background hex, and still
       works if OPNsense is ever run under a lighter theme. */
    .tabulator-row:hover:not(.tabulator-selected) {
        background-color: rgba(255, 255, 255, 0.08);
    }
    /* The block icon (command-gwtfblock) only appears on hover -- the
       unblock icon (command-gwtfunblock) is deliberately excluded from
       this rule, since "this host is blocked" is state a user needs to
       see without hunting for it. */
    .tabulator-row:not(:hover) .command-gwtfblock {
        visibility: hidden;
    }
</style>

<div class="content-box col-xs-12 __mb" style="display: flex; align-items: center; justify-content: space-between; padding-bottom: 6px;">
    <div id="live-filter-indicator" style="display: none;">
        {{ lang._('Filtered by') }} <strong id="live-filter-label"></strong>
        <button id="live-filter-clear" type="button" class="btn btn-xs btn-default">{{ lang._('Clear') }}</button>
    </div>
    <div>
        <label for="interval" style="font-weight: normal; margin-right: 4px;">{{ lang._('Refresh every') }}</label>
        <select class="selectpicker" id="interval" data-width="150">
            <option value="500">500 {{ lang._('Milliseconds') }}</option>
            <option value="1000">1 {{ lang._('Second') }}</option>
            <option value="2000" selected="selected">2 {{ lang._('Seconds') }}</option>
            <option value="5000">5 {{ lang._('Seconds') }}</option>
            <option value="10000">10 {{ lang._('Seconds') }}</option>
            <option value="0">{{ lang._("Don't refresh") }}</option>
        </select>
    </div>
</div>
<ul class="nav nav-tabs" data-tabs="tabs" id="livetabs">
    <li class="active"><a data-toggle="tab" href="#live-overview">{{ lang._('Overview') }}</a></li>
    <li><a data-toggle="tab" href="#live-toptalkers">{{ lang._('Top Talkers') }}</a></li>
    <li><a data-toggle="tab" href="#live-table">{{ lang._('Details') }}</a></li>
</ul>
<div class="tab-content content-box col-xs-12 __mb">
    <div id="live-overview" class="tab-pane fade in active">
        <div class="btn-group" style="margin-bottom: 6px;">
            <label style="font-weight: normal; margin-right: 4px;">{{ lang._('Chart') }}</label>
            <select class="selectpicker" id="live-chart-type" data-width="auto">
                <option value="line" selected="selected">{{ lang._('Line') }}</option>
                <option value="bar">{{ lang._('Stacked Bar') }}</option>
                <option value="graph">{{ lang._('Graph (experimental)') }}</option>
            </select>
            <label style="font-weight: normal; margin: 0 4px 0 12px;">{{ lang._('Group by') }}</label>
            <select class="selectpicker" id="live-group-by" data-width="auto">
                <option value="local_ip" selected="selected">{{ lang._('Local Host') }}</option>
                <option value="peer_port">{{ lang._('Peer Port') }}</option>
            </select>
            <span id="live-linebar-controls">
                <label style="font-weight: normal; margin: 0 4px 0 12px;">{{ lang._('Top N') }}</label>
                <select class="selectpicker" id="live-top-n" data-width="auto">
                    <option value="5">5</option>
                    <option value="10" selected="selected">10</option>
                    <option value="20">20</option>
                    <option value="0">{{ lang._('All') }}</option>
                </select>
                <label style="font-weight: normal; margin: 0 4px 0 12px;">{{ lang._('Scale') }}</label>
                <select class="selectpicker" id="live-scale" data-width="auto">
                    <option value="linear" selected="selected">{{ lang._('Linear') }}</option>
                    <option value="log">{{ lang._('Logarithmic') }}</option>
                </select>
            </span>
        </div>
        <div id="live-chart-canvas-wrapper" style="min-height: 320px;">
            <canvas id="live-overview-canvas"></canvas>
        </div>
        <div id="live-graph-wrapper" style="display: none;"></div>
    </div>
    <div id="live-toptalkers" class="tab-pane fade in">
        <p class="help-block">
            {{ lang._('Click a host to see its connections in the Details tab. "1 min" is a trailing moving window (not just the last refresh); "window" covers the same 30-minute range as the Overview chart.') }}
        </p>
        <table id="grid-live-toptalkers" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="commands" data-width="3em" data-searchable="false" data-sortable="false" data-formatter="commands"></th>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                    <th data-column-id="min1_bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('In (1 min)') }}</th>
                    <th data-column-id="min1_bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Out (1 min)') }}</th>
                    <th data-column-id="min1_bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total (1 min)') }}</th>
                    <th data-column-id="refresh_conn_count" data-type="numeric" data-width="8em">{{ lang._('Open Conns') }}</th>
                    <th data-column-id="window_bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('In (30 min)') }}</th>
                    <th data-column-id="window_bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Out (30 min)') }}</th>
                    <th data-column-id="window_bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total (30 min)') }}</th>
                    <th data-column-id="window_conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections (30 min)') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
    <div id="live-table" class="tab-pane fade in">
        <table id="grid-live" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="commands" data-width="3em" data-searchable="false" data-sortable="false" data-formatter="commands"></th>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                    <th data-column-id="peer" data-type="string">{{ lang._('Peer') }}</th>
                    <th data-column-id="category" data-type="string" data-width="10em">{{ lang._('Category') }}</th>
                    <th data-column-id="dpi_protocol" data-type="string" data-width="8em">{{ lang._('Protocol') }}</th>
                    <th data-column-id="proto" data-type="string" data-width="6em">{{ lang._('Proto') }}</th>
                    <th data-column-id="local_port" data-type="numeric" data-width="7em">{{ lang._('Local Port') }}</th>
                    <th data-column-id="peer_port" data-type="numeric" data-width="6em">{{ lang._('Port') }}</th>
                    <th data-column-id="state" data-type="string" data-width="10em">{{ lang._('State') }}</th>
                    <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                    <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                    <th data-column-id="duration" data-type="numeric" data-formatter="durationformatter">{{ lang._('Duration') }}</th>
                    <th data-column-id="last_activity" data-type="numeric" data-formatter="timestampformatter">{{ lang._('Last Activity') }}</th>
                    <th data-column-id="last_seen" data-type="numeric" data-formatter="timestampformatter">{{ lang._('Last Seen') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
</div>
