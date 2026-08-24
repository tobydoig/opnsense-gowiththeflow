<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script>
    $( document ).ready(function() {
        let selected_days = "7";
        let selected_local_host = "";
        let selected_bucket = "hour";
        let selected_chart_type = "line";
        let historyChart = null;

        function reloadAll() {
            $("#grid-history").bootgrid('reload');
            loadHistoryChart();
        }

        $("#days-selection").change(function () {
            selected_days = $(this).val();
            reloadAll();
        });

        $("#local-host-selection").on("changed.bs.select", function () {
            selected_local_host = $(this).val() || "";
            reloadAll();
        });

        $("#history-bucket").on("changed.bs.select", function () {
            selected_bucket = $(this).val() || "hour";
            loadHistoryChart();
        });

        $("#history-chart-type").on("changed.bs.select", function () {
            selected_chart_type = $(this).val() || "line";
            loadHistoryChart();
        });

        $("#grid-history").UIBootgrid({
            search:'/api/gowiththeflow/history/search/',
            options: {
                selection: false,
                multiSelect: false,
                requestHandler: function(request) {
                    request['days'] = selected_days;
                    request['local_host'] = selected_local_host;
                    return request;
                },
                responseHandler: function (response) {
                    if (response.local_hosts !== undefined) {
                        populateLocalHostOptionsGWTF(response.local_hosts);
                    }
                    return response;
                },
                formatters: {
                    "bytesformatter": function (column, row) {
                        return formatBytesGWTF(row[column.id]);
                    }
                }
            }
        });

        addCsvExportButtonGWTF('grid-history', 'gowiththeflow-history.csv');

        loadHistoryChart();

        function populateLocalHostOptionsGWTF(localHosts) {
            let known = $("#local-host-selection > option").map(function () {
                return $(this).val();
            }).get();
            for ([ip, label] of Object.entries(localHosts)) {
                if (!known.includes(ip)) {
                    $("#local-host-selection").append($('<option>', { value: ip, text: label }));
                }
            }
            $("#local-host-selection").selectpicker('refresh');
        }

        function loadHistoryChart() {
            $.ajax({
                url: '/api/gowiththeflow/history/timeseries',
                type: 'POST',
                data: { days: selected_days, bucket: selected_bucket, local_host: selected_local_host },
                dataType: 'json'
            }).done(function (response) {
                if (response.local_hosts) {
                    populateLocalHostOptionsGWTF(response.local_hosts);
                }
                renderHistoryChart(response);
            });
        }

        const GWTF_PALETTE = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
                              '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];

        function renderHistoryChart(response) {
            const buckets = response.buckets || [];
            const series = response.series || {};
            const localHosts = response.local_hosts || {};
            const labels = buckets.map(function (ts) {
                return selected_bucket === 'day'
                    ? new Date(ts * 1000).toLocaleDateString()
                    : new Date(ts * 1000).toLocaleString();
            });

            const datasets = Object.keys(series).map(function (ip, i) {
                return {
                    label: ip === 'Other' ? '{{ lang._("Other") }}' : (localHosts[ip] || ip),
                    data: series[ip],
                    borderColor: ip === 'Other' ? '#999999' : GWTF_PALETTE[i % GWTF_PALETTE.length],
                    backgroundColor: ip === 'Other' ? '#999999' : GWTF_PALETTE[i % GWTF_PALETTE.length],
                    fill: selected_chart_type === 'bar',
                    tension: 0.2,
                };
            });

            const config = {
                type: selected_chart_type === 'bar' ? 'bar' : 'line',
                data: { labels: labels, datasets: datasets },
                options: {
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            stacked: selected_chart_type === 'bar',
                            ticks: { callback: function (v) { return formatBytesGWTF(v); } }
                        },
                        x: { stacked: selected_chart_type === 'bar' }
                    },
                    plugins: {
                        tooltip: {
                            callbacks: {
                                label: function (ctx) {
                                    return ctx.dataset.label + ': ' + formatBytesGWTF(ctx.parsed.y);
                                }
                            }
                        }
                    }
                }
            };

            if (!historyChart) {
                const ctx = document.getElementById('history-overview-canvas').getContext('2d');
                historyChart = new Chart(ctx, config);
            } else {
                historyChart.config.type = config.type;
                historyChart.data.labels = config.data.labels;
                historyChart.data.datasets = config.data.datasets;
                historyChart.options.scales.y.stacked = config.options.scales.y.stacked;
                historyChart.options.scales.x.stacked = config.options.scales.x.stacked;
                historyChart.update();
            }
        }
    });

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
</script>

<div class="content-box col-xs-12 __mb">
    <div class="btn-group" id="days-selection-wrapper">
        <select class="selectpicker" id="days-selection" data-width="auto">
            <option value="1">{{ lang._('Last day') }}</option>
            <option value="7" selected="selected">{{ lang._('Last 7 days') }}</option>
            <option value="14">{{ lang._('Last 14 days') }}</option>
            <option value="30">{{ lang._('Last 30 days') }}</option>
            <option value="90">{{ lang._('Last 90 days') }}</option>
        </select>
    </div>
    <div class="btn-group" id="local-host-selection-wrapper">
        <select class="selectpicker" data-live-search="true" id="local-host-selection" data-width="auto" title="{{ lang._('All Local Hosts') }}">
        </select>
    </div>
</div>
<ul class="nav nav-tabs" data-tabs="tabs" id="historytabs">
    <li class="active"><a data-toggle="tab" href="#history-overview">{{ lang._('Overview') }}</a></li>
    <li><a data-toggle="tab" href="#history-table">{{ lang._('Table') }}</a></li>
</ul>
<div class="tab-content content-box col-xs-12 __mb">
    <div id="history-overview" class="tab-pane fade in active">
        <div class="btn-group" style="margin-bottom: 6px;">
            <label style="font-weight: normal; margin-right: 4px;">{{ lang._('Resolution') }}</label>
            <select class="selectpicker" id="history-bucket" data-width="auto">
                <option value="hour" selected="selected">{{ lang._('1 hour') }}</option>
                <option value="day">{{ lang._('1 day') }}</option>
            </select>
            <label style="font-weight: normal; margin: 0 4px 0 12px;">{{ lang._('Chart') }}</label>
            <select class="selectpicker" id="history-chart-type" data-width="auto">
                <option value="line" selected="selected">{{ lang._('Line') }}</option>
                <option value="bar">{{ lang._('Stacked Bar') }}</option>
            </select>
        </div>
        <div style="height: 360px;">
            <canvas id="history-overview-canvas"></canvas>
        </div>
    </div>
    <div id="history-table" class="tab-pane fade in">
        <table id="grid-history" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                    <th data-column-id="peer" data-type="string">{{ lang._('Peer') }}</th>
                    <th data-column-id="category" data-type="string" data-width="10em">{{ lang._('Category') }}</th>
                    <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                    <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                    <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                    <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
</div>
