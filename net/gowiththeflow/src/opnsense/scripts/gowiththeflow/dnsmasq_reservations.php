<?php

/*
 * Dumps OPNsense's Dnsmasq DHCP reservation list as JSON -- one row per
 * (mac, ip) pair, plus that reservation's own configured `host` name
 * (the "Host" field in Services > Dnsmasq DNS & DHCP > Hosts -- a
 * human-supplied label, independent of whatever hostname the DHCP
 * client itself reports at lease time, and often set even for devices
 * that report none at all, e.g. a VR headset with no DHCP option-12
 * support). Invoked directly by reservation_gate.py and
 * localhost_identity.py via subprocess (Dnsmasq's config is
 * PHP-model-owned; Python has no other way to read it -- confirmed the
 * same way dnsbl_apply.php already established this project's "Python
 * has no other way to touch a PHP-model-owned thing" pattern for
 * Unbound), not via configd -- this is a plain read with no config lock
 * needed, so none of block_host.py's own "always exit 0" reasoning for
 * configd's stdout-swallowing applies here; a genuine parse/PHP failure
 * is fine to signal via a non-zero exit, which both callers already
 * treat as "don't touch anything from this cycle" rather than "there is
 * nothing to know."
 *
 * A `hosts` row counts as a reservation only when BOTH `hwaddr` and `ip`
 * are populated -- the exact same rule already established by
 * BlockrulesController::findDhcpReservationIp() for the domain-block
 * feature's own DHCP-reservation check, reused here rather than
 * re-derived. `hwaddr` can be a comma-separated list of several MACs
 * bound to the one ip (e.g. a laptop's wifi + ethernet) -- each is
 * emitted as its own row (with the same `host` value repeated), since
 * reservation_gate.py's own pf-table allowlist is ip-only regardless of
 * which of those MACs is actually active, while its ARP-pinning layer
 * needs every candidate MAC to decide which one to pin (see
 * choose_pin_target()). `host` itself can be blank (a reservation only
 * needs hwaddr+ip to function) -- emitted as "" rather than omitted, so
 * every row has the same shape; localhost_identity.py's own
 * fetch_reservation_hostnames() is what filters those back out.
 */

require_once("config.inc");

use OPNsense\Dnsmasq\Dnsmasq;

function main(): array
{
    $mdl = new Dnsmasq();
    $rows = [];
    foreach ($mdl->hosts->iterateItems() as $node) {
        $ip = (string)$node->ip;
        if ($ip === '') {
            continue;
        }
        $macs = array_filter(array_map('trim', explode(',', (string)$node->hwaddr)));
        $host = (string)$node->host;
        foreach ($macs as $mac) {
            $rows[] = ['mac' => strtolower($mac), 'ip' => $ip, 'host' => $host];
        }
    }
    return $rows;
}

echo json_encode(main()) . "\n";
