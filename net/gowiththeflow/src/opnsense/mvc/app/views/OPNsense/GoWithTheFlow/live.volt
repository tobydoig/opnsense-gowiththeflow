<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script>
    'use strict';

    $( document ).ready(function() {
        let previousSnapshot = new Map();  // row_id -> bytes_in+bytes_out
        let chartHistory = [];             // [{time, groups: {key: bytesDelta}}]
        let groupLabels = {};              // raw key -> display label (hostname where known)
        const MAX_POINTS = 60;
        const TOP_N = 10;
        const GRAPH_FADE_MS = 4000;
        let graphNodes = {};                // raw peer/host key -> {el, lastSeen}

        $("#grid-live").UIBootgrid({
            search:'/api/gowiththeflow/live/search/',
            options: {
                selection: false,
                multiSelect: false,
                responseHandler: function (response) {
                    updateLiveOverview(response.rows || []);
                    return response;
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
        }

        $("#interval").change(function () {
            if (window.localStorage) {
                window.localStorage.setItem(storageKey, $(this).val());
            }
        });

        (function livePoller() {
            // NOT `|| 2000` -- 0 ("Don't refresh") is falsy in JS, so that
            // would silently fall back to the default and never actually
            // stop refreshing.
            let parsed = parseInt($("#interval").val(), 10);
            let interval = Number.isNaN(parsed) ? 2000 : parsed;
            if (interval <= 0) {
                // "Don't refresh" -- do nothing, but keep checking in case
                // the user changes the dropdown again later.
                setTimeout(livePoller, 2000);
                return;
            }
            setTimeout(function () {
                $("#grid-live").bootgrid('reload');
                livePoller();
            }, interval);
        })();

        $("#live-group-by").on("changed.bs.select", function () {
            chartHistory = [];
            previousSnapshot = new Map();
            renderLiveChart();
        });

        $("#live-chart-type").on("changed.bs.select", function () {
            const chartType = $(this).val() || 'line';
            $("#live-chart-canvas-wrapper").toggle(chartType !== 'graph');
            $("#live-graph-wrapper").toggle(chartType === 'graph');
            renderLiveChart();
        });

        $('a[href="#live-table"]').on('shown.bs.tab', function () {
            // The grid lives in a tab that may have been hidden at load
            // time -- an IntersectionObserver elsewhere already handles
            // redrawing it once visible, matching every other tabbed grid
            // in this plugin.
        });
    });

    // Delta-per-tick, computed entirely client-side from the same poll the
    // table already does (via responseHandler, so no extra AJAX call) --
    // pf's own byte counters are cumulative, so each tick's contribution is
    // this row's current total minus its own value last tick (0 for a
    // brand-new row -- a reasonable approximation for a just-opened
    // connection). A connection that closes between two polls has no
    // "current" entry to diff against, so its last partial interval is
    // dropped from the chart -- accepted tradeoff for a live glance chart,
    // not a metering system; the Table tab and History are unaffected.
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
        if (chartHistory.length > MAX_POINTS) {
            chartHistory.shift();
        }

        renderLiveChart();
        renderLiveGraph(rows, deltasByGroup);
    }

    function topGroupKeysGWTF(groupsHistory) {
        const totals = {};
        groupsHistory.forEach(function (point) {
            Object.keys(point.groups).forEach(function (k) {
                totals[k] = (totals[k] || 0) + point.groups[k];
            });
        });
        const sorted = Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a]; });
        return { top: sorted.slice(0, TOP_N), all: sorted };
    }

    const GWTF_PALETTE = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
                           '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];

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
        }
    }

    function liveChartInstanceGWTF() {
        return window.__gwtfLiveChart || null;
    }

    function filterLiveTableByGroupGWTF(rawKey) {
        if (!rawKey) {
            return; // "Other" isn't one specific host/port to filter by
        }
        $('a[href="#live-table"]').tab('show');
        const table = $("#grid-live").data('UIBootgrid').getTable();
        const field = window.__gwtfGroupBy === 'peer_port' ? 'peer_port' : 'local_ip';
        table.setFilter(field, "=", rawKey);
    }

    // Experimental "try it and see" renderer -- local hosts on the left,
    // their current peers on the right (capped to the busiest, the rest
    // lumped into "Other"), edge thickness by current throughput. A peer
    // that drops out of the latest poll fades out over a few seconds
    // rather than disappearing instantly.
    function renderLiveGraph(rows, deltasByGroup) {
        const wrapper = document.getElementById('live-graph-wrapper');
        if (!wrapper || $("#live-chart-type").val() !== 'graph') {
            return;
        }

        const pairTotals = {};
        const hostSet = {};
        const peerSet = {};
        rows.forEach(function (row) {
            const key = row.local_ip + '|' + row.peer_ip;
            pairTotals[key] = (pairTotals[key] || 0) + (Number(row.bytes_in) || 0) + (Number(row.bytes_out) || 0);
            hostSet[row.local_ip] = row.local;
            peerSet[row.peer_ip] = row.peer;
        });

        const sortedPairs = Object.keys(pairTotals).sort(function (a, b) { return pairTotals[b] - pairTotals[a]; });
        const shown = sortedPairs.slice(0, TOP_N);
        const now = Date.now();
        shown.forEach(function (key) {
            let node = graphNodes[key];
            if (!node) {
                node = { el: $('<div class="gwtf-graph-edge"></div>').appendTo(wrapper) };
                graphNodes[key] = node;
            }
            const parts = key.split('|');
            const localLabel = hostSet[parts[0]] || parts[0];
            const peerLabel = peerSet[parts[1]] || parts[1];
            const maxTotal = pairTotals[shown[0]] || 1;
            const widthPct = Math.max(5, Math.round((pairTotals[key] / maxTotal) * 100));
            node.el.css({ opacity: 1 }).html(
                '<span class="gwtf-graph-host">' + $('<div>').text(localLabel).html() + '</span>'
                + '<span class="gwtf-graph-bar" style="width:' + widthPct + '%"></span>'
                + '<span class="gwtf-graph-peer">' + $('<div>').text(peerLabel).html() + '</span>'
            );
            node.el.off('click').on('click', function () {
                $('a[href="#live-table"]').tab('show');
                const table = $("#grid-live").data('UIBootgrid').getTable();
                table.setFilter('local_ip', '=', parts[0]);
            });
            node.lastSeen = now;
        });

        // Fade out and remove anything not seen in this poll.
        Object.keys(graphNodes).forEach(function (key) {
            const node = graphNodes[key];
            if (node.lastSeen === now) {
                return;
            }
            if (!node.fading) {
                node.fading = true;
                node.el.css({ transition: 'opacity ' + GRAPH_FADE_MS + 'ms', opacity: 0 });
                setTimeout(function () {
                    node.el.remove();
                    delete graphNodes[key];
                }, GRAPH_FADE_MS);
            }
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
    .gwtf-graph-edge {
        display: flex; align-items: center; gap: 8px;
        padding: 4px 0; cursor: pointer;
    }
    .gwtf-graph-host, .gwtf-graph-peer {
        flex: 0 0 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .gwtf-graph-peer { text-align: right; }
    .gwtf-graph-bar {
        flex: 1 1 auto; height: 8px; background: #4e79a7; border-radius: 4px; min-width: 5%;
    }
</style>

<div class="content-box col-xs-12 __mb" style="text-align: right; padding-bottom: 6px;">
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
        </div>
        <div id="live-chart-canvas-wrapper" style="height: 320px;">
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
                    <th data-column-id="last_seen" data-type="numeric" data-formatter="timestampformatter">{{ lang._('Last Seen') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
</div>
