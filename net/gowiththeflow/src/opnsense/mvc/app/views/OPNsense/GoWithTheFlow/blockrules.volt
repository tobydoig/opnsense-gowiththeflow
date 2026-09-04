<style>
    .tabulator-row:hover:not(.tabulator-selected) {
        background-color: rgba(255, 255, 255, 0.08);
    }
    .gwtf-schedule-window {
        display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap;
    }
    .gwtf-schedule-window .gwtf-day-toggle { margin-right: 6px; }
    /* Widened, not just re-padded -- a schedule row (7 day toggles + two
       time inputs + a delete button) genuinely doesn't fit the default
       Bootstrap modal width without wrapping, regardless of padding. */
    #gwtfRuleDialog .modal-dialog {
        width: 650px;
    }
    #gwtfRuleDialog .modal-body {
        padding-left: 24px;
        padding-right: 24px;
    }
</style>

<script>
    // Shared by both the add-rule and edit-rule flows -- one dialog, one
    // JS-side model of "what's currently in the form", submitted to
    // either /add/ (a fresh rule) or /edit/<id>/ (domains+schedule only,
    // per BlockrulesController::editAction()).
    const GWTF_DAYS = [
        ["mon", "{{ lang._('Mon') }}"], ["tue", "{{ lang._('Tue') }}"], ["wed", "{{ lang._('Wed') }}"],
        ["thu", "{{ lang._('Thu') }}"], ["fri", "{{ lang._('Fri') }}"], ["sat", "{{ lang._('Sat') }}"],
        ["sun", "{{ lang._('Sun') }}"],
    ];

    function gwtfRenderScheduleWindowRow(days, start, end) {
        days = days || [];
        const dayInputs = GWTF_DAYS.map(function (d) {
            const checked = days.indexOf(d[0]) !== -1 ? "checked" : "";
            return '<label class="gwtf-day-toggle"><input type="checkbox" class="gwtf-window-day" value="' + d[0] + '" ' + checked + '> ' + d[1] + '</label>';
        }).join("");
        return $(
            '<div class="gwtf-schedule-window">' +
                '<span>' + dayInputs + '</span>' +
                '<input type="time" class="form-control gwtf-window-start" style="width:auto;" value="' + (start || "20:00") + '">' +
                '<span>{{ lang._("to") }}</span>' +
                '<input type="time" class="form-control gwtf-window-end" style="width:auto;" value="' + (end || "08:00") + '">' +
                '<button type="button" class="btn btn-xs btn-default gwtf-remove-window"><span class="fa fa-trash-o"></span></button>' +
            '</div>'
        );
    }

    // Populates the device field's autocomplete with every locally-known
    // device -- picking a suggestion fills in its *hostname* (falling
    // back to the IP only for a device with no known name), so a rule
    // stays correctly attached to the device if its DHCP lease later
    // hands out a different IP (BlockrulesController::resolveDeviceIp()
    // looks up either form). The field also accepts either typed
    // directly, so this is a convenience, not the only way to specify a
    // device.
    function gwtfPopulateDeviceOptions(localHosts) {
        const list = $("#gwtf-device-options");
        list.empty();
        Object.keys(localHosts).forEach(function (ip) {
            const entry = localHosts[ip];
            $('<option>', { value: entry.hostname || ip, label: entry.label }).appendTo(list);
        });
    }

    function gwtfAddScheduleWindow(days, start, end) {
        gwtfRenderScheduleWindowRow(days, start, end).appendTo("#gwtf-schedule-windows");
    }

    // Reads the dialog's own state (Always checkbox + type radio +
    // window rows) back into the exact shapes the API/CLI expect --
    // schedule as a JSON string (or "" for "Always"), domains as a
    // plain CSV string.
    function gwtfCollectScheduleJson() {
        if ($("#gwtf-schedule-mode-always").is(":checked")) {
            return "";
        }
        const windows = [];
        $("#gwtf-schedule-windows .gwtf-schedule-window").each(function () {
            const days = $(this).find(".gwtf-window-day:checked").map(function () { return $(this).val(); }).get();
            if (days.length === 0) {
                return;
            }
            windows.push({
                days: days,
                start: $(this).find(".gwtf-window-start").val() || "00:00",
                end: $(this).find(".gwtf-window-end").val() || "00:00",
            });
        });
        return windows.length ? JSON.stringify({ windows: windows }) : "";
    }

    function gwtfResetRuleDialog() {
        $("#gwtf-rule-id").val("");
        $("#gwtf-rule-ip").val("").prop("disabled", false);
        $("#gwtf-rule-type-host").prop("checked", true);
        $("#gwtf-rule-domains").val("");
        $("#gwtf-rule-reason").val("");
        $("#gwtf-schedule-mode-always").prop("checked", true);
        $("#gwtf-schedule-windows").empty();
        gwtfAddScheduleWindow(["mon", "tue", "wed", "thu", "fri"], "20:00", "08:00");
        $("#gwtfRuleDialog .modal-title").text("{{ lang._('Add block rule') }}");
        gwtfToggleRuleTypeFields();
        gwtfToggleScheduleFields();
    }

    function gwtfToggleRuleTypeFields() {
        const isDomain = $("#gwtf-rule-type-domain").is(":checked");
        $("#gwtf-rule-domains-group").toggle(isDomain);
    }

    function gwtfToggleScheduleFields() {
        $("#gwtf-schedule-editor").toggle($("#gwtf-schedule-mode-scheduled").is(":checked"));
    }

    function gwtfOpenEditDialog(data) {
        gwtfResetRuleDialog();
        $("#gwtfRuleDialog .modal-title").text("{{ lang._('Edit block rule') }}");
        $("#gwtf-rule-id").val(data.id);
        $("#gwtf-rule-ip").val(data.local_ip).prop("disabled", true);
        if (data.rule_type === "domain") {
            $("#gwtf-rule-type-domain").prop("checked", true);
        } else {
            $("#gwtf-rule-type-host").prop("checked", true);
        }
        $("#gwtf-rule-domains").val(data.domains || "");
        $("#gwtf-rule-reason").val(data.reason || "");
        if (data.schedule_json) {
            $("#gwtf-schedule-mode-scheduled").prop("checked", true);
            $("#gwtf-schedule-windows").empty();
            try {
                const parsed = JSON.parse(data.schedule_json);
                (parsed.windows || []).forEach(function (w) {
                    gwtfAddScheduleWindow(w.days, w.start, w.end);
                });
            } catch (e) {
                gwtfAddScheduleWindow(["mon"], "20:00", "08:00");
            }
        }
        gwtfToggleRuleTypeFields();
        gwtfToggleScheduleFields();
        $("#gwtfRuleDialog").modal("show");
    }

    $(document).ready(function () {
        $("#grid-blockrules").UIBootgrid({
            search: '/api/gowiththeflow/blockrules/search/',
            commands: {
                // Named gwtfedit, not edit -- "edit" is a RESERVED command
                // name in opnsense_bootgrid.js's own built-in command set
                // (requires: ['get', 'set'], checked against a `crud`
                // config this plugin never provides since it uses its own
                // hand-built modal, not getForm() scaffolding). A custom
                // command sharing that name gets shallow-merged onto the
                // built-in's stale `requires: ['get', 'set']` (only
                // title/classname/sequence/method get overwritten), so
                // the visibility check fails and the button silently
                // never renders -- confirmed live: this was the entire
                // "no edit/pencil icon at all" bug, on both nostromo and
                // the dev VM. del/override_* aren't reserved names, so
                // they get a clean `requires: []` and always worked fine.
                gwtfedit: {
                    title: "{{ lang._('Edit') }}",
                    classname: 'fa fa-pencil fa-fw',
                    sequence: 1,
                    method: function (event, cell) {
                        gwtfOpenEditDialog(cell.getData());
                    },
                },
                override_unblock: {
                    title: "{{ lang._('Unblock now (until this window ends)') }}",
                    classname: 'fa fa-play fa-fw text-success',
                    sequence: 2,
                    filter: function (cell) {
                        const d = cell.getData();
                        return !!d.schedule_json && d.last_effective_state === 'blocked';
                    },
                    method: function (event, cell) {
                        ajaxCall('/api/gowiththeflow/blockrules/override/' + cell.getData().id + '/', { state: 'unblocked' }, function () {
                            $("#grid-blockrules").bootgrid('reload');
                        });
                    },
                },
                override_block: {
                    title: "{{ lang._('Block now (until the next window starts)') }}",
                    classname: 'fa fa-ban fa-fw',
                    sequence: 2,
                    filter: function (cell) {
                        const d = cell.getData();
                        return !!d.schedule_json && d.last_effective_state !== 'blocked';
                    },
                    method: function (event, cell) {
                        ajaxCall('/api/gowiththeflow/blockrules/override/' + cell.getData().id + '/', { state: 'blocked' }, function () {
                            $("#grid-blockrules").bootgrid('reload');
                        });
                    },
                },
                del: {
                    title: "{{ lang._('Delete this rule') }}",
                    classname: 'fa fa-trash-o fa-fw text-danger',
                    sequence: 3,
                    method: function (event, cell) {
                        const d = cell.getData();
                        stdDialogConfirm(
                            "{{ lang._('Confirm delete') }}",
                            "{{ lang._('Remove this block rule for') }} " + d.device + "? " +
                                "{{ lang._('Any traffic it was currently blocking will immediately be allowed again.') }}",
                            "{{ lang._('Delete') }}", "{{ lang._('Cancel') }}",
                            function () {
                                ajaxCall('/api/gowiththeflow/blockrules/del/' + d.id + '/', {}, function () {
                                    $("#grid-blockrules").bootgrid('reload');
                                });
                            },
                            'danger'
                        );
                    },
                },
            },
            options: {
                selection: false,
                multiSelect: false,
                // Feeds the Add/Edit dialog's device autocomplete
                // (#gwtf-device-options) -- every locally-known device,
                // not just ones already in block_rules, same shape
                // BlockrulesController::searchAction() already returns
                // this project's other filter dropdowns from.
                responseHandler: function (response) {
                    if (response.local_hosts !== undefined) {
                        gwtfPopulateDeviceOptions(response.local_hosts);
                    }
                    return response;
                },
            },
        });
        addCsvExportButtonGWTF('grid-blockrules', 'gowiththeflow-blockrules.csv');

        $('<button id="grid-blockrules-add" class="btn btn-primary" type="button">' +
            '<span class="fa fa-plus"></span> {{ lang._("Add block rule") }}' +
          '</button>')
            .on('click', function () {
                gwtfResetRuleDialog();
                $("#gwtfRuleDialog").modal("show");
            })
            .prependTo('#grid-blockrules-actions-group');

        $("#gwtf-rule-type-host, #gwtf-rule-type-domain").on("change", gwtfToggleRuleTypeFields);
        $("#gwtf-schedule-mode-always, #gwtf-schedule-mode-scheduled").on("change", gwtfToggleScheduleFields);
        $("#gwtf-schedule-add-window").on("click", function () {
            gwtfAddScheduleWindow(["mon", "tue", "wed", "thu", "fri"], "20:00", "08:00");
        });
        $("#gwtf-schedule-windows").on("click", ".gwtf-remove-window", function () {
            $(this).closest(".gwtf-schedule-window").remove();
        });

        $("#gwtf-rule-save").on("click", function () {
            const id = $("#gwtf-rule-id").val();
            const scheduleJson = gwtfCollectScheduleJson();
            const isEdit = !!id;
            const payload = isEdit
                ? { domains: $("#gwtf-rule-domains").val(), schedule: scheduleJson }
                : {
                    rule_type: $("#gwtf-rule-type-domain").is(":checked") ? "domain" : "host",
                    local_ip: $("#gwtf-rule-ip").val(),
                    domains: $("#gwtf-rule-domains").val(),
                    schedule: scheduleJson,
                    reason: $("#gwtf-rule-reason").val(),
                };
            const url = isEdit ? '/api/gowiththeflow/blockrules/edit/' + id + '/' : '/api/gowiththeflow/blockrules/add/';
            ajaxCall(url, payload, function (data) {
                if (data && data.status !== 'ok') {
                    stdDialogInform(
                        "{{ lang._('Could not save this rule') }}",
                        (data && data.error) || "{{ lang._('Unknown error') }}",
                        "{{ lang._('Close') }}", undefined, 'danger'
                    );
                    return;
                }
                $("#gwtfRuleDialog").modal("hide");
                $("#grid-blockrules").bootgrid('reload');
            });
        });
    });

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
</script>

<div class="content-box col-xs-12 __mb">
    <p class="help-block">
        {{ lang._('Block a device entirely, or just a set of domains for one device, with an optional weekly schedule. A rule with no schedule stays blocked until you unblock it, exactly like a plain block always has.') }}
    </p>
    <table id="grid-blockrules" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <th data-column-id="commands" data-width="8em" data-searchable="false" data-sortable="false" data-formatter="commands"></th>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                <th data-column-id="device" data-type="string">{{ lang._('Device') }}</th>
                <th data-column-id="type_label" data-type="string" data-width="10em">{{ lang._('Type') }}</th>
                <th data-column-id="domains" data-type="string">{{ lang._('Domains') }}</th>
                <th data-column-id="schedule_label" data-type="string">{{ lang._('Schedule') }}</th>
                <th data-column-id="status_label" data-type="string" data-width="16em">{{ lang._('Status') }}</th>
                <th data-column-id="created_by" data-type="string" data-width="10em">{{ lang._('Created By') }}</th>
                <th data-column-id="reason" data-type="string">{{ lang._('Reason') }}</th>
            </tr>
        </thead>
        <tbody>
        </tbody>
    </table>
</div>

<div class="modal fade" id="gwtfRuleDialog" tabindex="-1" role="dialog">
    <div class="modal-dialog" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal"><span>&times;</span></button>
                <h4 class="modal-title">{{ lang._('Add block rule') }}</h4>
            </div>
            <div class="modal-body">
                <input type="hidden" id="gwtf-rule-id">
                <div class="form-group">
                    <label>{{ lang._('Device (IP address or known hostname)') }}</label>
                    <input type="text" class="form-control" id="gwtf-rule-ip" list="gwtf-device-options" placeholder="192.168.1.50 or ipad-alice.internal">
                    <datalist id="gwtf-device-options"></datalist>
                </div>
                <div class="form-group">
                    <label>
                        <input type="radio" name="gwtf-rule-type" id="gwtf-rule-type-host" checked> {{ lang._('Block this device entirely') }}
                    </label><br>
                    <label>
                        <input type="radio" name="gwtf-rule-type" id="gwtf-rule-type-domain"> {{ lang._('Block only specific domains for this device') }}
                    </label>
                </div>
                <div class="form-group" id="gwtf-rule-domains-group" style="display:none;">
                    <label>{{ lang._('Domains (comma-separated -- each one and all its subdomains)') }}</label>
                    <input type="text" class="form-control" id="gwtf-rule-domains" placeholder="youtube.com, tiktok.com">
                    <p class="help-block">
                        {{ lang._('The device needs a static DHCP reservation already -- see Services > Dnsmasq DNS & DHCP > Hosts.') }}
                    </p>
                </div>
                <div class="form-group">
                    <label>
                        <input type="radio" name="gwtf-schedule-mode" id="gwtf-schedule-mode-always" checked> {{ lang._('Always (block until manually unblocked)') }}
                    </label><br>
                    <label>
                        <input type="radio" name="gwtf-schedule-mode" id="gwtf-schedule-mode-scheduled"> {{ lang._('Scheduled (block only during set windows each week)') }}
                    </label>
                </div>
                <div id="gwtf-schedule-editor" style="display:none;">
                    <label>{{ lang._('Blocked during these windows each week:') }}</label>
                    <div id="gwtf-schedule-windows"></div>
                    <button type="button" class="btn btn-xs btn-default" id="gwtf-schedule-add-window">
                        <span class="fa fa-plus"></span> {{ lang._('Add another window') }}
                    </button>
                </div>
                <div class="form-group" style="margin-top: 12px;">
                    <label>{{ lang._('Reason (optional)') }}</label>
                    <input type="text" class="form-control" id="gwtf-rule-reason">
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-default" data-dismiss="modal">{{ lang._('Cancel') }}</button>
                <button type="button" class="btn btn-primary" id="gwtf-rule-save">{{ lang._('Save') }}</button>
            </div>
        </div>
    </div>
</div>
