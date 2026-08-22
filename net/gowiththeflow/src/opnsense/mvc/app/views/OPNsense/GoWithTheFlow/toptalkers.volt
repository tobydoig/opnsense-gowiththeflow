<script>
    $( document ).ready(function() {
        let selected_days = "7";
        let selected_local_host = "";

        $("#days-selection").change(function () {
            selected_days = $(this).val();
            $("#grid-toptalkers-local").bootgrid('reload');
            $("#grid-toptalkers-remote").bootgrid('reload');
        });

        $("#local-host-selection").on("changed.bs.select", function () {
            selected_local_host = $(this).val() || "";
            $("#grid-toptalkers-remote").bootgrid('reload');
        });

        $("#grid-toptalkers-local").UIBootgrid({
            search:'/api/gowiththeflow/toptalkers/local',
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

        $("#grid-toptalkers-remote").UIBootgrid({
            search:'/api/gowiththeflow/toptalkers/remote',
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

        $("#days-selection-wrapper").detach().insertAfter('#grid-toptalkers-local-header .search');
        $("#local-host-selection-wrapper").detach().insertAfter('#grid-toptalkers-remote-header .search');
    });

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

    <h4>{{ lang._('Top Local Hosts') }}</h4>
    <table id="grid-toptalkers-local" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter" data-sort="desc">{{ lang._('Total') }}</th>
            </tr>
        </thead>
        <tbody>
        </tbody>
    </table>

    <h4>{{ lang._('Top Remote Hosts') }}</h4>
    <div class="btn-group" id="local-host-selection-wrapper">
        <select class="selectpicker" data-live-search="true" id="local-host-selection" data-width="auto" title="{{ lang._('All Local Hosts') }}">
        </select>
    </div>
    <table id="grid-toptalkers-remote" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
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
