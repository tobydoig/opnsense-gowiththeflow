<script>
    'use strict';

    $( document ).ready(function() {
        $("#grid-live").UIBootgrid({
            search:'/api/gowiththeflow/live/search/',
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
        // needing to tear down and rebuild anything.
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
<div class="tab-content content-box col-xs-12 __mb">
    <table id="grid-live" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                <th data-column-id="remote" data-type="string">{{ lang._('Remote Host') }}</th>
                <th data-column-id="proto" data-type="string" data-width="6em">{{ lang._('Proto') }}</th>
                <th data-column-id="local_port" data-type="numeric" data-width="7em">{{ lang._('Local Port') }}</th>
                <th data-column-id="remote_port" data-type="numeric" data-width="6em">{{ lang._('Port') }}</th>
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
