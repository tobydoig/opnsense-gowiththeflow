<script>
    $( document ).ready(function() {
        let selected_days = "7";
        let selected_local_host = "";

        $("#days-selection").change(function () {
            selected_days = $(this).val();
            $("#grid-toptalkers-local").bootgrid('reload');
            $("#grid-toptalkers-peer").bootgrid('reload');
            $("#grid-toptalkers-category").bootgrid('reload');
            $("#grid-toptalkers-protocol").bootgrid('reload');
            $("#grid-toptalkers-uncategorized").bootgrid('reload');
        });

        $("#local-host-selection").on("changed.bs.select", function () {
            selected_local_host = $(this).val() || "";
            $("#grid-toptalkers-peer").bootgrid('reload');
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

        $("#grid-toptalkers-peer").UIBootgrid({
            search:'/api/gowiththeflow/toptalkers/peer',
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

        $("#grid-toptalkers-category").UIBootgrid({
            search:'/api/gowiththeflow/toptalkers/category',
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

        $("#grid-toptalkers-protocol").UIBootgrid({
            search:'/api/gowiththeflow/toptalkers/protocol',
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

        $("#grid-toptalkers-uncategorized").UIBootgrid({
            search:'/api/gowiththeflow/toptalkers/uncategorized',
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

        addCsvExportButtonGWTF('grid-toptalkers-local', 'gowiththeflow-toptalkers-local.csv');
        addCsvExportButtonGWTF('grid-toptalkers-peer', 'gowiththeflow-toptalkers-peer.csv');
        addCsvExportButtonGWTF('grid-toptalkers-category', 'gowiththeflow-toptalkers-category.csv');
        addCsvExportButtonGWTF('grid-toptalkers-protocol', 'gowiththeflow-toptalkers-protocol.csv');
        addCsvExportButtonGWTF('grid-toptalkers-uncategorized', 'gowiththeflow-toptalkers-uncategorized.csv');

        // The `data-sort="desc"` header attributes below aren't actually
        // read anywhere in opnsense_bootgrid.js -- only `data-sorter` is
        // (which picks a sort *function*, not a direction), so despite
        // looking configured, Total has never actually defaulted to
        // descending. The real mechanism is Tabulator's own setSort(),
        // called once each table is actually built.
        let localTable = $("#grid-toptalkers-local").data('UIBootgrid').getTable();
        localTable.on("tableBuilt", function () {
            localTable.setSort("bytes_total", "desc");
        });
        let peerTable = $("#grid-toptalkers-peer").data('UIBootgrid').getTable();
        peerTable.on("tableBuilt", function () {
            peerTable.setSort("bytes_total", "desc");
        });
        let categoryTable = $("#grid-toptalkers-category").data('UIBootgrid').getTable();
        categoryTable.on("tableBuilt", function () {
            categoryTable.setSort("bytes_total", "desc");
        });
        let protocolTable = $("#grid-toptalkers-protocol").data('UIBootgrid').getTable();
        protocolTable.on("tableBuilt", function () {
            protocolTable.setSort("bytes_total", "desc");
        });
        let uncategorizedTable = $("#grid-toptalkers-uncategorized").data('UIBootgrid').getTable();
        uncategorizedTable.on("tableBuilt", function () {
            uncategorizedTable.setSort("bytes_total", "desc");
        });

        $("#days-selection-wrapper").detach().insertAfter('#grid-toptalkers-local-header .search');
        $("#local-host-selection-wrapper").detach().insertAfter('#grid-toptalkers-peer-header .search');
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

<ul class="nav nav-tabs" data-tabs="tabs" id="toptalkerstabs">
    <li class="active"><a data-toggle="tab" href="#toptalkers-local">{{ lang._('Top Local Hosts') }}</a></li>
    <li><a data-toggle="tab" href="#toptalkers-peer">{{ lang._('Top Peers') }}</a></li>
    <li><a data-toggle="tab" href="#toptalkers-category">{{ lang._('By Category') }}</a></li>
    <li><a data-toggle="tab" href="#toptalkers-protocol">{{ lang._('By Protocol') }}</a></li>
    <li><a data-toggle="tab" href="#toptalkers-uncategorized">{{ lang._('Uncategorized Hosts') }}</a></li>
</ul>
<div class="tab-content content-box col-xs-12 __mb">
    <div id="toptalkers-local" class="tab-pane fade in active">
        <div class="btn-group" id="days-selection-wrapper">
            <select class="selectpicker" id="days-selection" data-width="auto">
                <option value="1">{{ lang._('Last day') }}</option>
                <option value="7" selected="selected">{{ lang._('Last 7 days') }}</option>
                <option value="14">{{ lang._('Last 14 days') }}</option>
                <option value="30">{{ lang._('Last 30 days') }}</option>
                <option value="90">{{ lang._('Last 90 days') }}</option>
            </select>
        </div>
        <table id="grid-toptalkers-local" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                    <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                    <th data-column-id="unique_peer_hosts" data-type="numeric" data-width="8em">{{ lang._('Unique Peer Hosts') }}</th>
                    <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                    <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                    <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
    <div id="toptalkers-peer" class="tab-pane fade in">
        <div class="btn-group" id="local-host-selection-wrapper">
            <select class="selectpicker" data-live-search="true" id="local-host-selection" data-width="auto" title="{{ lang._('All Local Hosts') }}">
            </select>
        </div>
        <table id="grid-toptalkers-peer" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="peer" data-type="string">{{ lang._('Peer') }}</th>
                    <th data-column-id="category" data-type="string" data-width="10em">{{ lang._('Category') }}</th>
                    <th data-column-id="dpi_protocol" data-type="string" data-width="8em">{{ lang._('Protocol') }}</th>
                    <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                    <th data-column-id="unique_local_hosts" data-type="numeric" data-width="8em">{{ lang._('Unique Local Hosts') }}</th>
                    <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                    <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                    <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
    <div id="toptalkers-category" class="tab-pane fade in">
        <table id="grid-toptalkers-category" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="category" data-type="string">{{ lang._('Category') }}</th>
                    <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                    <th data-column-id="unique_peer_hosts" data-type="numeric" data-width="8em">{{ lang._('Unique Peer Hosts') }}</th>
                    <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                    <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                    <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
    <div id="toptalkers-protocol" class="tab-pane fade in">
        <table id="grid-toptalkers-protocol" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="dpi_protocol" data-type="string">{{ lang._('Protocol') }}</th>
                    <th data-column-id="conn_count" data-type="numeric" data-width="8em">{{ lang._('Connections') }}</th>
                    <th data-column-id="unique_peer_hosts" data-type="numeric" data-width="8em">{{ lang._('Unique Peer Hosts') }}</th>
                    <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                    <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                    <th data-column-id="bytes_total" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Total') }}</th>
                </tr>
            </thead>
            <tbody>
            </tbody>
        </table>
    </div>
    <div id="toptalkers-uncategorized" class="tab-pane fade in">
        <p class="help-block">
            {{ lang._('Hostnames seen recently that no category rule matches -- export this list to help decide what to add next.') }}
        </p>
        <table id="grid-toptalkers-uncategorized" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
                <tr>
                    <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                    <th data-column-id="peer_hostname" data-type="string">{{ lang._('Hostname') }}</th>
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
