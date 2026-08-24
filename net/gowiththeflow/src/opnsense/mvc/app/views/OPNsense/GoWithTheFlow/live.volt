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
    let previousSnapshot = new Map();  // row_id -> bytes_in+bytes_out
    let chartHistory = [];             // [{time, groups: {key: bytesDelta}}]
    let groupLabels = {};              // raw key -> display label (hostname where known)
    let hiddenGroupKeys = new Set();   // raw keys shift-clicked out of the Line/Bar chart
    let liveChartRangeMinutes = 5;     // how much history the Line/Bar chart shows, user-configurable
    let liveChartTopN = 10;            // Line/Bar chart's line cap, user-configurable -- 0 means "all"
    const GRAPH_FADE_MS = 4000;
    let graphNodes = {};                // "local_ip|peer_ip|peer_port" -> {el, lastSeen, fading}
    let forceNodePositions = {};        // ip -> {x, y} -- persists across ticks so the force layout
                                         // gently relaxes as data changes instead of jumping around
    let lastRows = null;                // most recent live/search rows, cached so switching
    let lastDeltasByGroup = null;       // to Graph mode can render immediately, not wait a tick
    let liveFilter = { local_ip: '', peer_ip: '', peer_port: '', host_ip: '' };  // server-side, via requestHandler

    $( document ).ready(function() {
        $("#grid-live").UIBootgrid({
            search:'/api/gowiththeflow/live/search/',
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

        // The `data-sort="desc"` header attribute (also used, equally
        // ineffectively, on Top Talkers' bytes_total column) isn't actually
        // read anywhere in opnsense_bootgrid.js -- only `data-sorter` is
        // (which picks a sort *function*, not a direction). The real
        // mechanism is Tabulator's own setSort(), called once the table
        // is actually built so it doesn't race the wrapper's own init.
        let liveTable = $("#grid-live").data('UIBootgrid').getTable();
        liveTable.on("tableBuilt", function () {
            liveTable.setSort("last_seen", "desc");
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
            let storedRange = window.localStorage.getItem('gowiththeflow.live.rangeMinutes');
            if (storedRange) {
                $("#live-range").val(storedRange).selectpicker('refresh');
                liveChartRangeMinutes = parseInt(storedRange, 10) || liveChartRangeMinutes;
            }
            let storedTopN = window.localStorage.getItem('gowiththeflow.live.topN');
            if (storedTopN !== null) {
                $("#live-top-n").val(storedTopN).selectpicker('refresh');
                liveChartTopN = parseInt(storedTopN, 10) || 0;
            }
        }

        $("#interval").change(function () {
            if (window.localStorage) {
                window.localStorage.setItem(storageKey, $(this).val());
            }
            // The interval is also the chart's tick width, so changing it
            // changes how many points are needed for the same real-time
            // range -- reconcile immediately rather than waiting for the
            // buffer to slowly grow/shrink one poll at a time.
            gwtfReconcileChartHistoryLength();
            renderLiveChart();
        });

        $("#live-range").on("changed.bs.select", function () {
            liveChartRangeMinutes = parseInt($(this).val(), 10) || 5;
            if (window.localStorage) {
                window.localStorage.setItem('gowiththeflow.live.rangeMinutes', String(liveChartRangeMinutes));
            }
            gwtfReconcileChartHistoryLength();
            renderLiveChart();
        });

        $("#live-top-n").on("changed.bs.select", function () {
            liveChartTopN = parseInt($(this).val(), 10) || 0;
            if (window.localStorage) {
                window.localStorage.setItem('gowiththeflow.live.topN', String(liveChartTopN));
            }
            renderLiveChart();
        });

        // Deliberately its own poll, not fed from the table's own
        // responseHandler -- the table's response is one Bootgrid page
        // (default 50 rows) of a result that can easily be larger on a
        // busy network, and last_seen bumping on every still-open
        // session every tick means which sessions land on that one page
        // is essentially arbitrary. A dominant real host's traffic
        // (confirmed with a phone running speedtest.net) could silently
        // never appear on the chart/graph at all if its rows just
        // weren't on the page the table happened to be showing.
        gwtfPollLiveOverview();

        (function livePoller() {
            const interval = gwtfCurrentPollIntervalMs();
            if (interval <= 0) {
                // "Don't refresh" -- do nothing, but keep checking in case
                // the user changes the dropdown again later.
                setTimeout(livePoller, 2000);
                return;
            }
            setTimeout(function () {
                $("#grid-live").bootgrid('reload');
                gwtfPollLiveOverview();
                livePoller();
            }, interval);
        })();

        $("#live-group-by").on("changed.bs.select", function () {
            chartHistory = [];
            previousSnapshot = new Map();
            hiddenGroupKeys = new Set();
            renderLiveChart();
        });

        $("#live-chart-type").on("changed.bs.select", function () {
            const chartType = $(this).val() || 'line';
            $("#live-chart-canvas-wrapper").toggle(chartType !== 'graph');
            $("#live-graph-wrapper").toggle(chartType === 'graph');
            // Range/Top N only mean anything for Line/Bar -- Graph shows
            // every host/edge currently open, uncapped, unconditionally.
            $("#live-linebar-controls").toggle(chartType !== 'graph');
            renderLiveChart();
            // Graph mode is only ever driven from updateLiveOverview() on a
            // poll tick -- without this, switching to it shows nothing at
            // all until the next tick happens to land.
            if (chartType === 'graph' && lastRows) {
                renderLiveGraph(lastRows, lastDeltasByGroup);
            }
        });

        $('a[href="#live-table"]').on('shown.bs.tab', function () {
            // The grid lives in a tab that may have been hidden at load
            // time -- an IntersectionObserver elsewhere already handles
            // redrawing it once visible, matching every other tabbed grid
            // in this plugin.
        });

        $("#live-filter-clear").on('click', function () {
            clearLiveFilterGWTF();
        });
    });

    // A dedicated poll against the unpaginated overview endpoint, kept
    // deliberately separate from the Table tab's own Bootgrid ajax call
    // (see the comment where this is scheduled) -- costs one extra
    // request per tick, but a busy real network with more concurrent
    // sessions than one Bootgrid page can miss a genuinely dominant
    // host's traffic entirely otherwise, which is worse than the extra
    // request.
    function gwtfPollLiveOverview() {
        $.ajax({
            url: '/api/gowiththeflow/live/overview',
            type: 'POST',
            dataType: 'json'
        }).done(function (response) {
            updateLiveOverview(response.rows || []);
        });
    }

    // Delta-per-tick, computed client-side from gwtfPollLiveOverview()'s
    // own poll -- pf's own byte counters are cumulative, so each tick's
    // contribution is this row's current total minus its own value last
    // tick (0 for a brand-new row -- a reasonable approximation for a
    // just-opened connection). A connection that closes between two
    // polls has no "current" entry to diff against, so its last partial
    // interval is dropped from the chart -- accepted tradeoff for a
    // live glance chart, not a metering system; the Table tab and
    // History are unaffected.
    function updateLiveOverview(rows) {
        const currentSnapshot = new Map();
        const deltasByGroup = {};

        rows.forEach(function (row) {
            const bytesTotal = (Number(row.bytes_in) || 0) + (Number(row.bytes_out) || 0);
            currentSnapshot.set(row.row_id, bytesTotal);
            const prev = previousSnapshot.has(row.row_id) ? previousSnapshot.get(row.row_id) : bytesTotal;
            const delta = Math.max(bytesTotal - prev, 0);

            const key = window.__gwtfGroupBy === 'peer_port' ? String(row.peer_port) : row.local_ip;
            deltasByGroup[key] = (deltasByGroup[key] || 0) + delta;
            groupLabels[key] = window.__gwtfGroupBy === 'peer_port' ? String(row.peer_port) : row.local;
        });

        previousSnapshot = currentSnapshot;
        chartHistory.push({ time: new Date(), groups: deltasByGroup });
        gwtfReconcileChartHistoryLength();

        lastRows = rows;
        lastDeltasByGroup = deltasByGroup;

        renderLiveChart();
        renderLiveGraph(rows, deltasByGroup);
    }

    // NOT `|| 2000` -- 0 ("Don't refresh") is falsy in JS, so that would
    // silently fall back to the default and never actually stop
    // refreshing.
    function gwtfCurrentPollIntervalMs() {
        const parsed = parseInt($("#interval").val(), 10);
        return Number.isNaN(parsed) ? 2000 : parsed;
    }

    // How many chartHistory points are needed to cover
    // liveChartRangeMinutes of real time at the current poll interval --
    // recomputed on demand rather than cached, since either input can
    // change independently (the range selector, or the interval
    // dropdown, which doubles as the chart's own tick width).
    function gwtfLiveMaxPoints() {
        const intervalMs = Math.max(gwtfCurrentPollIntervalMs(), 250);
        return Math.max(10, Math.ceil((liveChartRangeMinutes * 60000) / intervalMs));
    }

    // Keeps chartHistory at exactly gwtfLiveMaxPoints() long, padding
    // with empty placeholder points at the front (stepping backward in
    // time from whatever's already there, or from now if starting from
    // empty) when it needs to grow, and trimming from the front when it
    // needs to shrink. Without padding, the chart would start at 1
    // point and slowly grow into the requested range instead of showing
    // a fixed window from the very first draw; empty points contribute
    // 0 to every dataset and are invisible to topGroupKeysGWTF's totals,
    // so they never skew which groups count as "top". Called every tick
    // (self-correcting, idempotent) and immediately on a range/interval
    // change so the resize is instant rather than waiting on the next poll.
    function gwtfReconcileChartHistoryLength() {
        const target = gwtfLiveMaxPoints();
        const intervalMs = gwtfCurrentPollIntervalMs();
        while (chartHistory.length > target) {
            chartHistory.shift();
        }
        while (chartHistory.length < target) {
            const oldest = chartHistory[0];
            const t = oldest ? oldest.time.getTime() - intervalMs : Date.now();
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

    function renderLiveChart() {
        const groupBy = $("#live-group-by").val() || 'local_ip';
        const chartType = $("#live-chart-type").val() || 'line';
        window.__gwtfGroupBy = groupBy;
        if (chartType === 'graph') {
            return;
        }

        const { top, all } = topGroupKeysGWTF(chartHistory);
        const otherKeys = all.filter(function (k) { return top.indexOf(k) === -1; });
        const labels = chartHistory.map(function (p) { return p.time.toLocaleTimeString(); });

        const datasets = top.map(function (key, i) {
            return {
                label: groupLabels[key] || key,
                rawKey: key,
                hidden: hiddenGroupKeys.has(key),
                data: chartHistory.map(function (p) { return p.groups[key] || 0; }),
                borderColor: GWTF_PALETTE[i % GWTF_PALETTE.length],
                backgroundColor: GWTF_PALETTE[i % GWTF_PALETTE.length],
                fill: chartType === 'bar',
                tension: 0.2,
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
                fill: chartType === 'bar',
                tension: 0.2,
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
                        min: 0,
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

        const existing = liveChartInstanceGWTF();
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
    function renderLiveGraph(rows, deltasByGroup) {
        const wrapper = document.getElementById('live-graph-wrapper');
        if (!wrapper || $("#live-chart-type").val() !== 'graph') {
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
    <li><a data-toggle="tab" href="#live-table">{{ lang._('Table') }}</a></li>
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
                <label style="font-weight: normal; margin: 0 4px 0 12px;">{{ lang._('Range') }}</label>
                <select class="selectpicker" id="live-range" data-width="auto">
                    <option value="2">2 {{ lang._('minutes') }}</option>
                    <option value="5" selected="selected">5 {{ lang._('minutes') }}</option>
                    <option value="10">10 {{ lang._('minutes') }}</option>
                    <option value="30">30 {{ lang._('minutes') }}</option>
                </select>
                <label style="font-weight: normal; margin: 0 4px 0 12px;">{{ lang._('Top N') }}</label>
                <select class="selectpicker" id="live-top-n" data-width="auto">
                    <option value="5">5</option>
                    <option value="10" selected="selected">10</option>
                    <option value="20">20</option>
                    <option value="0">{{ lang._('All') }}</option>
                </select>
            </span>
        </div>
        <div id="live-chart-canvas-wrapper" style="min-height: 320px;">
            <canvas id="live-overview-canvas"></canvas>
        </div>
        <div id="live-graph-wrapper" style="display: none;"></div>
    </div>
    <div id="live-table" class="tab-pane fade in">
        <table id="grid-live" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                    <th data-column-id="peer" data-type="string">{{ lang._('Peer') }}</th>
                    <th data-column-id="category" data-type="string" data-width="10em">{{ lang._('Category') }}</th>
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
