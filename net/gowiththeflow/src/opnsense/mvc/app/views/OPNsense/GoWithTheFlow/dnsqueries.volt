<style>
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
</style>

<script>
    $( document ).ready(function() {
        let selected_days = "7";
        let selected_local_host = "";

        $("#days-selection").change(function () {
            selected_days = $(this).val();
            $("#grid-dnsqueries").bootgrid('reload');
        });

        $("#local-host-selection").on("changed.bs.select", function () {
            selected_local_host = $(this).val() || "";
            $("#grid-dnsqueries").bootgrid('reload');
        });

        $("#grid-dnsqueries").UIBootgrid({
            search:'/api/gowiththeflow/dnsqueries/search/',
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
                    "timestampformatter": function (column, row) {
                        return formatTimestampGWTF(row[column.id]);
                    }
                }
            }
        });

        addCsvExportButtonGWTF('grid-dnsqueries', 'gowiththeflow-dnsqueries.csv');

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

    function formatTimestampGWTF(unixSeconds) {
        if (unixSeconds === undefined || unixSeconds === null) {
            return "";
        }
        return new Date(unixSeconds * 1000).toLocaleString();
    }
</script>

<div class="content-box col-xs-12 __mb">
    <p class="help-block">
        {{ lang._('What\'s actually being queried over DNS and what came back, including failed lookups. Repeat lookups within the same hour are counted, not logged one row per query.') }}
    </p>
    <div class="btn-group" id="days-selection-wrapper">
        <select class="selectpicker" id="days-selection" data-width="auto">
            <option value="1">{{ lang._('Last day') }}</option>
            <option value="7" selected="selected">{{ lang._('Last 7 days') }}</option>
            <option value="14">{{ lang._('Last 14 days') }}</option>
            <option value="30">{{ lang._('Last 30 days') }}</option>
        </select>
    </div>
    <div class="btn-group" id="local-host-selection-wrapper">
        <select class="selectpicker" data-live-search="true" id="local-host-selection" data-width="auto" title="{{ lang._('All Local Hosts') }}">
        </select>
    </div>
</div>
<div class="content-box col-xs-12 __mb">
    <table id="grid-dnsqueries" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                <th data-column-id="last_seen" data-type="numeric" data-formatter="timestampformatter">{{ lang._('Last Seen') }}</th>
                <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                <th data-column-id="query_name" data-type="string">{{ lang._('Query') }}</th>
                <th data-column-id="query_type" data-type="string" data-width="6em">{{ lang._('Type') }}</th>
                <th data-column-id="rcode" data-type="string" data-width="8em">{{ lang._('Result') }}</th>
                <th data-column-id="answers" data-type="string">{{ lang._('Answers') }}</th>
                <th data-column-id="count" data-type="numeric" data-width="8em">{{ lang._('Count') }}</th>
            </tr>
        </thead>
        <tbody>
        </tbody>
    </table>
</div>
