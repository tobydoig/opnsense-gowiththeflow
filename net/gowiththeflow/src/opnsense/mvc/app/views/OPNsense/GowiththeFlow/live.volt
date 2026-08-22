<script>
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
                    }
                }
            }
        });
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

<div class="tab-content content-box col-xs-12 __mb">
    <table id="grid-live" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                <th data-column-id="local" data-type="string">{{ lang._('Local Host') }}</th>
                <th data-column-id="remote" data-type="string">{{ lang._('Remote Host') }}</th>
                <th data-column-id="proto" data-type="string" data-width="6em">{{ lang._('Proto') }}</th>
                <th data-column-id="remote_port" data-type="numeric" data-width="6em">{{ lang._('Port') }}</th>
                <th data-column-id="bytes_in" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes In') }}</th>
                <th data-column-id="bytes_out" data-type="numeric" data-formatter="bytesformatter">{{ lang._('Bytes Out') }}</th>
                <th data-column-id="duration" data-type="numeric" data-formatter="durationformatter">{{ lang._('Duration') }}</th>
            </tr>
        </thead>
        <tbody>
        </tbody>
    </table>
</div>
