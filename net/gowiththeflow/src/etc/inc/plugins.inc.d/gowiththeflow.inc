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

    // --- "only known/reserved devices may reach the WAN" -----------------
    // Off by default and by far the highest-blast-radius thing this
    // plugin does -- a bug here risks the whole LAN's internet access,
    // not one device's, so every step below is deliberate:
    //
    // Gated on the plugin's own enableReservationGate flag, read via a
    // plain model instantiation -- confirmed live against OPNsense
    // core's OWN dnsmasq_firewall() hook (same file location class,
    // /usr/local/etc/inc/plugins.inc.d/dnsmasq.inc) that this exact
    // "new \OPNsense\<Plugin>\<Model>(); if (...->isEmpty()) return;"
    // shape is how core itself gates a plugin's own firewall rules on
    // its own settings -- not a guess.
    $reservationMdl = new \OPNsense\GoWithTheFlow\GoWithTheFlow();
    if ((string)$reservationMdl->general->enableReservationGate !== '1') {
        return; // nothing registered at all -- zero effect on the live ruleset
    }
    // captureInterfaces is reused here as "the LAN-side interface(s)"
    // rather than adding a second, separate settings field -- but it's
    // deliberately chosen for PACKET CAPTURE visibility, which
    // legitimately wants both LAN and WAN interfaces to see both sides
    // of a conversation. Confirmed live this was a real, not just
    // theoretical, gap: this exact dev VM has captureInterfaces set to
    // "lan,wan", and without this exclusion the block-all rule below
    // got registered on the WAN interface too (`block in quick on le1
    // from any to any`) -- harmless on a box with no WAN-inbound
    // services, but on a real box with a VPN endpoint or a port
    // forward, a `quick` rule at this priority could pre-empt and
    // silently break it. The literal interface key "wan" is
    // structurally guaranteed by OPNsense's own config.xml shape
    // (<interfaces><wan>...) for the primary WAN, not merely a naming
    // convention a user could rename away from -- excluding it here is
    // a correctness fix, not a heuristic. Known, accepted, documented
    // gap: a genuine multi-WAN setup with an additional WAN-type
    // interface under some other name (e.g. a second uplink as "opt1")
    // would not be excluded by this -- OPNsense's own interface
    // mapping (Firewall\Plugin::getInterfaceMapping()) doesn't expose
    // a plain "is this WAN" flag to check against instead.
    $lanIfs = array_filter(
        array_map('trim', explode(',', (string)$reservationMdl->general->captureInterfaces)),
        function ($ifn) {
            return strtolower($ifn) !== 'wan';
        }
    );
    if (empty($lanIfs)) {
        return; // refuse to register with an undefined scope rather than guess one
    }

    // Filtering happens on the LAN-side interface(s), inbound direction --
    // NOT on WAN outbound. pf is stateful: a connection is evaluated once,
    // at the point it's first seen (a LAN client's outbound connection is
    // first evaluated as it enters on the LAN interface, before routing);
    // once ANY pre-existing rule passes that first packet and creates a
    // state, the WAN-egress leg of that same connection is matched against
    // the state table and never independently filtered again. A rule
    // placed on WAN-out would very likely have zero real effect, since
    // this box's own existing LAN pass rule already creates that state.
    $allowedFile = '/var/db/gowiththeflow/allowed_hosts.tbl';
    $protectedFile = '/var/db/gowiththeflow/protected_dests.tbl';
    $fw->registerTable('gowiththeflow_allowed', true, is_readable($allowedFile) ? $allowedFile : null);
    $fw->registerTable('gowiththeflow_protected_dests', true, is_readable($protectedFile) ? $protectedFile : null);

    $gateDefaults = ['direction' => 'in', 'interface' => implode(',', $lanIfs), 'quick' => true];

    // Rule 1: unconditionally allow traffic to the firewall itself, for
    // EVERY device, known or not. '(self)' is core's own built-in alias
    // for every address currently configured on this box (confirmed live
    // via dnsmasq_firewall()'s own identical use) -- always correct at
    // ruleset-compile time, no table and no dependency on local_subnets
    // being populated correctly. This is what keeps GUI/SSH reachable
    // regardless of allowlist membership.
    $fw->registerFilterRule(1, array_merge($gateDefaults, [
        'type' => 'pass', 'from' => 'any', 'to' => '(self)',
        'descr' => 'Go With The Flow: allow traffic to the firewall itself',
    ]));
    // Rule 2: unconditionally allow LAN-to-LAN traffic (local_subnets),
    // for EVERY device, known or not.
    $fw->registerFilterRule(1, array_merge($gateDefaults, [
        'type' => 'pass', 'from' => 'any', 'to' => '<gowiththeflow_protected_dests>',
        'descr' => 'Go With The Flow: allow LAN-to-LAN traffic',
    ]));
    // Rule 3: a known/reserved device may reach anywhere, including the WAN.
    $fw->registerFilterRule(1, array_merge($gateDefaults, [
        'type' => 'pass', 'from' => '<gowiththeflow_allowed>', 'to' => 'any',
        'descr' => 'Go With The Flow: allow known/reserved devices to reach the WAN',
    ]));
    // Rule 4: default-deny catch-all -- only reachable by an unregistered
    // device talking to a genuinely non-local destination (rules 1-3
    // above already quick-matched everything else, including this same
    // device's own DHCP/DNS/GUI/LAN traffic).
    $fw->registerFilterRule(1, array_merge($gateDefaults, [
        'type' => 'block', 'log' => true, 'disablereplyto' => 1, 'from' => 'any', 'to' => 'any',
        'descr' => 'Go With The Flow: default-deny unregistered devices from the WAN',
    ]));
}
