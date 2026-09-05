<style>
    .tabulator-row:hover:not(.tabulator-selected) {
        background-color: rgba(255, 255, 255, 0.08);
    }
    .gwtf-schedule-window {
        display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap;
    }
    .gwtf-schedule-window .gwtf-day-toggle { margin-right: 6px; }
    .gwtf-device-row {
        display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
    }
    .gwtf-device-row input { flex: 1; }
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
    // either /add/ (a fresh rule) or /edit/<id>/ (name/devices/domains/
    // schedule -- a rule's type itself can't be changed once created,
    // see BlockrulesController::editAction()).
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

    // One rule now covers a *group* of devices -- mirrors the schedule-
    // window repeater above (gwtfAddScheduleWindow/#gwtf-schedule-windows)
    // rather than a single input, so the dialog can hold as many devices
    // as the rule needs, each still resolved server-side by
    // BlockrulesController::resolveDeviceIp() (IP or known hostname).
    function gwtfAddDeviceRow(value) {
        $(
            '<div class="gwtf-device-row">' +
                '<input type="text" class="form-control gwtf-device-value" list="gwtf-device-options" ' +
                    'placeholder="192.168.1.50 or ipad-alice.internal (comma-separated for several)" value="' + (value ? $('<div>').text(value).html() : '') + '">' +
                '<button type="button" class="btn btn-xs btn-default gwtf-remove-device"><span class="fa fa-trash-o"></span></button>' +
            '</div>'
        ).appendTo("#gwtf-devices-list");
    }

    // Each row also accepts a comma-separated list on its own, not just
    // one device -- someone typing several names into the single row the
    // dialog starts with (the same convention the Domains field already
    // uses) must not silently become one bogus compound "device" that
    // fails to resolve as a whole; splitting here means both that and
    // one-per-row (or any mix) work the same way.
    function gwtfCollectDevices() {
        const values = [];
        $("#gwtf-devices-list .gwtf-device-value").each(function () {
            $(this).val().split(",").forEach(function (v) {
                v = v.trim();
                if (v !== "") {
                    values.push(v);
                }
            });
        });
        return values;
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
        $("#gwtf-rule-name").val("");
        $("#gwtf-devices-list").empty();
        gwtfAddDeviceRow("");
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
        $("#gwtf-rule-name").val(data.name || "");
        $("#gwtf-devices-list").empty();
        try {
            const devices = JSON.parse(data.devices);
            (devices || []).forEach(function (d) {
                gwtfAddDeviceRow(d.hostname || d.ip);
            });
        } catch (e) {
            gwtfAddDeviceRow("");
        }
        if ($("#gwtf-devices-list .gwtf-device-row").length === 0) {
            gwtfAddDeviceRow("");
        }
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
                // the dev VM. `del` isn't a reserved name, so it gets a
                // clean `requires: []` and always worked fine.
                gwtfedit: {
                    title: "{{ lang._('Edit') }}",
                    classname: 'fa fa-pencil fa-fw',
                    sequence: 1,
                    method: function (event, cell) {
                        gwtfOpenEditDialog(cell.getData());
                    },
                },
                // Named gwtfduplicate, not duplicate -- "duplicate" isn't
                // itself one of opnsense_bootgrid.js's reserved built-in
                // command names (add/edit/delete/copy/info/toggle/
                // enable-selected/disable-selected/delete-selected,
                // confirmed directly against the vendored source, the
                // same way gwtfedit's own reserved-name collision was
                // found), but every custom command here keeps the
                // `gwtf`-prefix convention regardless, so a future
                // bootgrid version reserving a plain word can never
                // silently break this grid's buttons again.
                gwtfduplicate: {
                    title: "{{ lang._('Duplicate this rule') }}",
                    classname: 'fa fa-clone fa-fw',
                    sequence: 2,
                    method: function (event, cell) {
                        ajaxCall('/api/gowiththeflow/blockrules/duplicate/' + cell.getData().id + '/', {}, function (data) {
                            if (data && data.status !== 'ok') {
                                stdDialogInform(
                                    "{{ lang._('Could not duplicate this rule') }}",
                                    (data && data.error) || "{{ lang._('Unknown error') }}",
                                    "{{ lang._('Close') }}", undefined, 'danger'
                                );
                                return;
                            }
                            $("#grid-blockrules").bootgrid('reload');
                        });
                    },
                },
                // Enable/disable had no UI control at all before this --
                // the backend (setEnabledAction/set_enabled) existed
                // from the start, but nothing ever called it. That gap
                // only became visible once Duplicate shipped: a
                // duplicated rule starts disabled on purpose (so it
                // can't instantly double-block the original's own
                // devices), but there was then no way to turn it back on
                // from the GUI at all. Named gwtftoggle, not toggle --
                // "toggle" is a RESERVED command name too (same
                // `requires: [...]`-against-`crud` collision as "edit"),
                // per this grid's own established convention.
                gwtftoggle: {
                    title: function (cell) {
                        return cell.getData().enabled == 1
                            ? "{{ lang._('Pause this rule (temporarily stop enforcing it)') }}"
                            : "{{ lang._('Resume this rule') }}";
                    },
                    classname: function (cell) {
                        return cell.getData().enabled == 1 ? 'fa fa-pause fa-fw' : 'fa fa-play fa-fw text-success';
                    },
                    sequence: 3,
                    method: function (event, cell) {
                        const d = cell.getData();
                        const newEnabled = d.enabled == 1 ? '0' : '1';
                        ajaxCall('/api/gowiththeflow/blockrules/setEnabled/' + d.id + '/' + newEnabled + '/', {}, function (data) {
                            if (data && data.status !== 'ok') {
                                stdDialogInform(
                                    "{{ lang._('Could not update this rule') }}",
                                    (data && data.error) || "{{ lang._('Unknown error') }}",
                                    "{{ lang._('Close') }}", undefined, 'danger'
                                );
                                return;
                            }
                            $("#grid-blockrules").bootgrid('reload');
                        });
                    },
                },
                // The two schedule-override buttons (temporarily force
                // blocked/unblocked mid-window, without touching the
                // rule's enabled state or schedule) were removed here --
                // both conditionally appeared/disappeared based on rule
                // state at the same moment gwtftoggle's own icon flips
                // between pause/play, and override_unblock's icon was
                // ALSO a green play arrow, so pausing a rule visually
                // looked like two different things were happening to two
                // different buttons for confusingly similar reasons.
                // Pause/resume alone covers the real-world need this was
                // trying to serve. The backend (rule_override / set_override
                // / RuleDecision's manual_override_state+override_until)
                // is untouched -- still reachable via the CLI/API if ever
                // needed again, just not exposed in this grid any more.
                del: {
                    title: "{{ lang._('Delete this rule') }}",
                    classname: 'fa fa-trash-o fa-fw text-danger',
                    sequence: 4,
                    method: function (event, cell) {
                        const d = cell.getData();
                        stdDialogConfirm(
                            "{{ lang._('Confirm delete') }}",
                            "{{ lang._('Remove the block rule') }} \"" + d.name + "\" (" + d.device + ")? " +
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
        $("#gwtf-devices-add").on("click", function () {
            gwtfAddDeviceRow("");
        });
        $("#gwtf-devices-list").on("click", ".gwtf-remove-device", function () {
            // Always leave at least one row -- an empty group is refused
            // server-side anyway, but an empty dialog with no way to add
            // a first device back would be a dead end.
            if ($("#gwtf-devices-list .gwtf-device-row").length > 1) {
                $(this).closest(".gwtf-device-row").remove();
            }
        });

        $("#gwtf-rule-save").on("click", function () {
            const id = $("#gwtf-rule-id").val();
            const scheduleJson = gwtfCollectScheduleJson();
            const isEdit = !!id;
            const payload = isEdit
                ? {
                    name: $("#gwtf-rule-name").val(),
                    devices: JSON.stringify(gwtfCollectDevices()),
                    domains: $("#gwtf-rule-domains").val(),
                    schedule: scheduleJson,
                }
                : {
                    rule_type: $("#gwtf-rule-type-domain").is(":checked") ? "domain" : "host",
                    name: $("#gwtf-rule-name").val(),
                    devices: JSON.stringify(gwtfCollectDevices()),
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
        {{ lang._('Block a group of devices entirely, or just a set of domains for them, with an optional weekly schedule. A rule with no schedule stays blocked until you unblock it, exactly like a plain block always has. Duplicate a rule to reuse it as a starting point for a similar one.') }}
    </p>
    <table id="grid-blockrules" class="table table-condensed table-hover table-striped table-responsive">
        <thead>
            <tr>
                <!-- Wide enough for every command that always appears on
                     one row: edit, duplicate, pause/resume, delete --
                     exactly 4 icons now that the two schedule-override
                     buttons are gone (see the removed-commands comment
                     near "del" below). Too narrow here silently
                     collapses the overflow into a "..." menu (not a
                     resize the user can do from the grid itself) rather
                     than erroring.

                     A bare number here (not "Nem") is a deliberate,
                     precise choice, not an oversight -- opnsense_
                     bootgrid.js's own column-width parsing only takes an
                     em/CSS-unit value at face value for a hidden probe
                     element's *rendered* outerWidth() (padding/border
                     included) plus a flat +5px margin, so the actual
                     pixel result depends on this page's ambient
                     font-size and isn't the same number of px you'd get
                     doing the em math yourself -- confirmed live via
                     devtools this was rendering at 215px for what was
                     meant to be a much narrower column. A bare number
                     skips all of that and is used as the literal pixel
                     width directly. 152 was the value confirmed live to
                     exactly fit 5 icons with no leftover gap; 122 here
                     is that same per-icon measurement (152/5, x4) for
                     the 4 that remain now -- not yet re-confirmed live
                     the way 152 was, since removing 2 commands and
                     re-measuring needs the user's own screen again. -->
                <th data-column-id="commands" data-width="122" data-searchable="false" data-sortable="false" data-formatter="commands"></th>
                <th data-column-id="row_id" data-identifier="true" data-visible="false">id</th>
                <th data-column-id="name" data-type="string">{{ lang._('Name') }}</th>
                <th data-column-id="device" data-type="string">{{ lang._('Devices') }}</th>
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
                    <label>{{ lang._('Name') }}</label>
                    <input type="text" class="form-control" id="gwtf-rule-name" placeholder="{{ lang._('e.g. Kids devices, bedtime') }}">
                </div>
                <div class="form-group">
                    <label>{{ lang._('Devices (IP address or known hostname, one or more)') }}</label>
                    <div id="gwtf-devices-list"></div>
                    <datalist id="gwtf-device-options"></datalist>
                    <button type="button" class="btn btn-xs btn-default" id="gwtf-devices-add">
                        <span class="fa fa-plus"></span> {{ lang._('Add another device') }}
                    </button>
                </div>
                <div class="form-group">
                    <label>
                        <input type="radio" name="gwtf-rule-type" id="gwtf-rule-type-host" checked> {{ lang._('Block these devices entirely') }}
                    </label><br>
                    <label>
                        <input type="radio" name="gwtf-rule-type" id="gwtf-rule-type-domain"> {{ lang._('Block only specific domains for these devices') }}
                    </label>
                </div>
                <div class="form-group" id="gwtf-rule-domains-group" style="display:none;">
                    <label>{{ lang._('Domains (comma-separated -- each one and all its subdomains)') }}</label>
                    <input type="text" class="form-control" id="gwtf-rule-domains" placeholder="youtube.com, tiktok.com">
                    <p class="help-block">
                        {{ lang._('Every device needs a static DHCP reservation already -- see Services > Dnsmasq DNS & DHCP > Hosts.') }}
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
