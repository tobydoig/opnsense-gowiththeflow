<?php

/*
 * Declares this plugin's own pf table + block-rule pair, dispatched by
 * OPNsense's plugins_firewall() (in plugins.inc) for every
 * /usr/local/etc/inc/plugins.inc.d/*.inc file that defines a
 * "<basename>_firewall($fw)" function -- confirmed directly from
 * OPNsense core source (plugins_scan()/plugins_firewall()) that this is
 * a plain glob()+function_exists() dispatch, no separate registration
 * needed beyond this file existing with the right name.
 *
 * A pf table with no rule referencing it blocks nothing, and an
 * independently pfctl-loaded anchor is never actually evaluated unless
 * the main ruleset has a call point for it (confirmed by reading the
 * live compiled ruleset: zero `anchor` lines anywhere) -- so
 * registerTable() alone would not have worked. registerFilterRule()
 * is the piece that makes this real: OPNsense core's own "overload
 * table" feature (a rate-limited filter rule's matching block rule,
 * see filter.lib.inc's virusprot/sshlockout rules) is live proof this
 * exact "table + quick block rule, membership managed independently
 * of the rule text" shape already works natively in this OPNsense
 * version.
 */

function gowiththeflow_firewall($fw)
{
    // gowiththeflow_blocked's membership is entirely owned by this
    // plugin (blocklist.py/block_host.py), not by OPNsense's own Alias
    // system -- deliberately, so an unrelated Firewall > Apply can
    // never wipe it the way update_tables.py's Alias-replay logic can
    // for a real OPNsense\Firewall\Alias (confirmed: that replay logic
    // only ever touches tables it derives from config.xml aliases).
    // The `file "..."` form means blocks are live from the very first
    // ruleset load at boot -- before the daemon even starts -- rather
    // than starting empty every reboot the way a bare `persist` table
    // would. pfctl fails the WHOLE ruleset load on an unreadable table
    // file, so this only points at the file once it actually exists
    // (same "warn and continue on an unmet precondition" posture rc.d/
    // gowiththeflow already takes for kern.racct.enable) -- a fresh
    // install's post-install script creates an empty file before ever
    // triggering the first filter reload, so this gap is only relevant
    // if something touches the ruleset before that runs.
    $tblfile = '/var/db/gowiththeflow/blocked_hosts.tbl';
    $fw->registerTable('gowiththeflow_blocked', true, is_readable($tblfile) ? $tblfile : null);

    // Priority 1 -- deliberately above OPNsense's own anti-lockout
    // allow rule (priority 5, confirmed in the live compiled ruleset),
    // per an explicit user decision: blocking a host is meant to be
    // total, including that host's access to this firewall's own
    // GUI/SSH, not just its access to the wider network. This does NOT
    // affect DHCP: core's own DHCP-server pass rules are also
    // registered at priority 1, but by core itself, before this hook
    // ever runs -- same-priority rules keep their registration order,
    // so DHCP still wins. That's intentional too: letting a blocked
    // host's lease lapse would let it reappear on a new, unblocked IP,
    // since the block is keyed on the address, not the device.
    $blockdefaults = [
        'type' => 'block',
        'log' => true,
        'disablereplyto' => 1,
        'quick' => true,
    ];

    // 'direction' => 'any' covers both directions in one rule each
    // (FilterRule renders 'any' as no in/out keyword at all) -- one
    // rule for the blocked host as source, one as destination, since a
    // single rule can't match both sides of a table membership test at
    // once.
    $fw->registerFilterRule(1, array_merge($blockdefaults, [
        'direction' => 'any',
        'from' => '<gowiththeflow_blocked>',
        'to' => 'any',
        'descr' => 'Go With The Flow: traffic from a blocked host',
    ]));
    $fw->registerFilterRule(1, array_merge($blockdefaults, [
        'direction' => 'any',
        'from' => 'any',
        'to' => '<gowiththeflow_blocked>',
        'descr' => 'Go With The Flow: traffic to a blocked host',
    ]));
}
