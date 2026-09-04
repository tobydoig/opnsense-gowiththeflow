<script>
    $( document ).ready(function() {
        let data_get_map = {'frm_settings': "/api/gowiththeflow/settings/get"};
        mapDataToFormUI(data_get_map).done(function(data){
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
            updateServiceControlUI('gowiththeflow');
        });

        $("#reconfigureAct").SimpleActionButton({
            onPreAction: function() {
                const dfObj = new $.Deferred();
                saveFormToEndpoint("/api/gowiththeflow/settings/set", 'frm_settings', function () { dfObj.resolve(); }, true, function () { dfObj.reject(); });
                return dfObj;
            },
            onAction: function(data, status) {
                updateServiceControlUI('gowiththeflow');
            }
        });

        $("#reconfigureAct").after($("#act-clear-data").detach().show());
        $("#act-clear-data").after($("#act-reset-hostname-cache").detach().show());
        $("#act-reset-hostname-cache").after($("#act-recategorize").detach().show());

        $("#act-clear-data").click(function(e) {
            stdDialogRemoveItem(
                "{{ lang._('Do you really want to clear all collected connection data? This cannot be undone.') }}",
                () => { ajaxCall("/api/gowiththeflow/settings/clearData"); }
            );
        });

        $("#act-reset-hostname-cache").click(function(e) {
            stdDialogRemoveItem(
                "{{ lang._('Do you really want to reset the hostname cache? Hostnames will need to be re-learned.') }}",
                () => { ajaxCall("/api/gowiththeflow/settings/resetHostnameCache"); }
            );
        });

        $("#act-recategorize").click(function(e) {
            stdDialogRemoveItem(
                "{{ lang._('Re-check every already-logged hostname against the current category rules and update any that have changed? This can take a moment on a large history.') }}",
                () => { ajaxCall("/api/gowiththeflow/settings/recategorize", {}, function (data) {
                    stdDialogInform(
                        "{{ lang._('Recategorize History') }}",
                        (data && data.status === 'ok')
                            ? "{{ lang._('Done.') }} " + data.hostnames_changed + " {{ lang._('of') }} " +
                                data.hostnames_checked + " {{ lang._('hostnames changed category (') }}" +
                                data.rows_updated + " {{ lang._('rows updated).') }}"
                            : ((data && data.error) || "{{ lang._('Unknown error') }}"),
                        "{{ lang._('Close') }}"
                    );
                }); }
            );
        });
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#general">{{ lang._('General') }}</a></li>
</ul>
<div class="tab-content content-box">
    <div id="general" class="tab-pane fade in active">
        {{ partial("layout_partials/base_form",['fields':generalForm,'id':'frm_settings'])}}
    </div>
</div>

{{ partial('layout_partials/base_apply_button', {'data_endpoint': '/api/gowiththeflow/service/reconfigure'}) }}
<button id="act-clear-data" class="btn btn-default __mr" style="display: none;">{{ lang._('Clear All Data') }}</button>
<button id="act-reset-hostname-cache" class="btn btn-default __mr" style="display: none;">{{ lang._('Reset Hostname Cache') }}</button>
<button id="act-recategorize" class="btn btn-default __mr" style="display: none;">{{ lang._('Recategorize History') }}</button>
