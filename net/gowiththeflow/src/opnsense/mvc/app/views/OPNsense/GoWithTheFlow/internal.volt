<script>
    $( document ).ready(function() {
        let selected_days = "7";

        $("#days-selection").change(function () {
            selected_days = $(this).val();
            $("#grid-internal-history").bootgrid('reload');
        });

        $("#grid-internal-live").UIBootgrid({
            search:'/api/gowiththeflow/internal/search',
            options: {
                selection: false,
                multiSelect: false,
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

        $("#grid-internal-history").UIBootgrid({
            search:'/api/gowiththeflow/internal/history',
            options: {
                selection: false,
                multiSelect: false,
                requestHandler: function(request) {
                    request['days'] = selected_days;
                    return request;
                },
                formatters: {
                    "bytesformatter": function (column, row) {
                        return formatBytesGWTF(row[column.id]);
                    }
                }
            }
        });

        addCsvExportButtonGWTF('grid-internal-live', 'gowiththeflow-internal-live.csv');
        addCsvExportButtonGWTF('grid-internal-history', 'gowiththeflow-internal-history.csv');

        // data-sort="desc" is dead markup -- not read anywhere in
        // opnsense_bootgrid.js. setSort(), called once the table is
        // actually built, is the real mechanism.
        let liveTable = $("#grid-internal-live").data('UIBootgrid').getTable();
        liveTable.on("tableBuilt", function () {
            liveTable.setSort("last_seen", "desc");
        });
        let historyTable = $("#grid-internal-history").data('UIBootgrid').getTable();
        historyTable.on("tableBuilt", function () {
            historyTable.setSort("bytes_total", "desc");
        });

        $("#days-selection-wrapper").detach().insertAfter('#grid-internal-history-header .search');
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

<ul class="nav nav-tabs" data-tabs="tabs" id="internaltabs">
    <li class="active"><a data-toggle="tab" href="#internal-live">{{ lang._('Live') }}</a></li>
    <li><a data-toggle="tab" href="#internal-history">{{ lang._('History') }}</a></li>
</ul>
<div class="tab-content content-box col-xs-12 __mb">
    <div id="internal-live" class="tab-pane fade in active">
        <p class="help-block">
            {{ lang._('Traffic between two local hosts that still routes through the firewall (e.g. separate VLANs/subnets) -- devices on the same subnet switch traffic directly and never reach the firewall at all, so that traffic cannot appear here.') }}
        </p>
        <table id="grid-internal-live" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="host_a" data-type="string">{{ lang._('Host A') }}</th>
                    <th data-column-id="host_b" data-type="string">{{ lang._('Host B') }}</th>
                    <th data-column-id="proto" data-type="string" data-width="6em">{{ lang._('Proto') }}</th>
                    <th data-column-id="port_a" data-type="numeric" data-width="7em">{{ lang._('Port A') }}</th>
                    <th data-column-id="port_b" data-type="numeric" data-width="7em">{{ lang._('Port B') }}</th>
                    <th data-column-id="bytes_a_to_b" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes A→B') }}</th>
                    <th data-column-id="bytes_b_to_a" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes B→A') }}</th>
                    <th data-column-id="duration" data-type="numeric" data-formatter="durationformatter">{{ lang._('Duration') }}</th>
                    <th data-column-id="last_seen" data-type="numeric" data-formatter="timestampformatter">{{ lang._('Last Seen') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
    <div id="internal-history" class="tab-pane fade in">
        <div class="btn-group" id="days-selection-wrapper">
            <select class="selectpicker" id="days-selection" data-width="auto">
                <option value="1">{{ lang._('Last day') }}</option>
                <option value="7" selected="selected">{{ lang._('Last 7 days') }}</option>
                <option value="14">{{ lang._('Last 14 days') }}</option>
                <option value="30">{{ lang._('Last 30 days') }}</option>
                <option value="90">{{ lang._('Last 90 days') }}</option>
            </select>
        </div>
        <table id="grid-internal-history" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="host_a" data-type="string">{{ lang._('Host A') }}</th>
                    <th data-column-id="host_b" data-type="string">{{ lang._('Host B') }}</th>
                    <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                    <th data-column-id="bytes_a_to_b" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes A→B') }}</th>
                    <th data-column-id="bytes_b_to_a" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes B→A') }}</th>
                    <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
</div>
