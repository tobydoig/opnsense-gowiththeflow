<script>
    $( document ).ready(function() {
        let selected_days = "7";
        let selected_local_host = "";

        $("#days-selection").change(function () {
            selected_days = $(this).val();
            $("#grid-history").bootgrid('reload');
        });

        $("#local-host-selection").on("changed.bs.select", function () {
            selected_local_host = $(this).val() || "";
            $("#grid-history").bootgrid('reload');
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
                        let known = $("#local-host-selection > option").map(function () {
                            return $(this).val();
                        }).get();
                        for ([ip, label] of Object.entries(response['local_hosts'])) {
                            if (!known.includes(ip)) {
                                $("#local-host-selection").append($('<option>', {
                                    value: ip,
                                    text: label
                                }));
                            }
                        }
                        $("#local-host-selection").selectpicker('refresh');
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

        $("#days-selection-wrapper").detach().insertAfter('#grid-history-header .search');
        $("#local-host-selection-wrapper").detach().insertAfter("#days-selection-wrapper");
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

<div class="tab-content content-box col-xs-12 __mb">
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
    <table id="grid-history" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                <th data-column-id="remote" data-type="string">{{ lang._('Remote Host') }}</th>
                <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter" data-sort="desc">{{ lang._('Total') }}</th>
            </tr>
        </thead>
        <tbody>
        </tbody>
    </table>
</div>
