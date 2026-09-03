<?php

/*
 * Applies (or removes) one of this plugin's own domain-block rules in
 * OPNsense's native Unbound DNSBL feature (Services > Unbound DNS >
 * Blocklist) -- confirmed live on the test VM that a dnsbl.blocklist
 * row's `wildcards` (domain list) + `source_nets` (client scoping)
 * fields do real per-client domain blocking, including automatic
 * subdomain coverage, with no custom Unbound config of our own needed.
 *
 * Invoked directly by block_rules_engine.py via subprocess (Unbound's
 * config is PHP-model-owned; Python has no other way to touch it) --
 * not via configd, so the "always exit 0" discipline block_host.py
 * needs for configdpRun()'s own empty-stdout-on-nonzero-exit gotcha
 * doesn't strictly apply here (subprocess.run() always returns real
 * stdout regardless of exit code), but kept anyway for consistency, and
 * in case a configd action ever calls this directly too (e.g. a future
 * manual "resync domain blocks" button).
 *
 * Only ever touches its OWN row, found by the stable --description key
 * (block_rules.unbound_description, e.g. "gowiththeflow:rule:12") --
 * never a user's own manually-configured blocklists.
 *
 * Actions:
 *   enable   -- find-or-create this rule's row, set enabled=1 with the
 *               given domains/source IP.
 *   disable  -- a schedule-driven "not right now" -- the row persists
 *               (just enabled=0) so re-enabling moments later isn't a
 *               full add/remove cycle. A no-op if the row doesn't exist.
 *   remove   -- the block_rules row itself is being deleted -- actually
 *               removes this plugin's row rather than leaving a
 *               disabled one behind forever.
 */

require_once("config.inc");

use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Unbound\Unbound;

function parse_args(array $argv): array
{
    $args = [];
    for ($i = 1; $i < count($argv); $i += 2) {
        $args[ltrim($argv[$i], '-')] = $argv[$i + 1] ?? null;
    }
    return $args;
}

/** Finds this plugin's own row by its stable description key. Returns
 * [uuid, node] or [null, null] if it doesn't exist yet. */
function find_row($blocklist, string $description): array
{
    foreach ($blocklist->iterateItems() as $uuid => $node) {
        if ((string)$node->description === $description) {
            return [$uuid, $node];
        }
    }
    return [null, null];
}

function main(array $argv): array
{
    $args = parse_args($argv);
    $action = $args['action'] ?? null;
    $description = $args['description'] ?? null;
    $domains = $args['domains'] ?? '';
    $sourceIp = $args['source-ip'] ?? null;

    if (!in_array($action, ['enable', 'disable', 'remove'], true) || !$description || !$sourceIp) {
        return ['status' => 'error', 'error' => 'missing or invalid arguments'];
    }

    // Locks and reloads from disk first -- this runs on a ~60s reconcile
    // tick and must not race a concurrent GUI save of Unbound's own
    // settings (or of a different rule's own dnsbl_apply.php run).
    Config::getInstance()->lock();
    try {
        $mdl = new Unbound();
        [$uuid, $node] = find_row($mdl->dnsbl->blocklist, $description);

        if ($action === 'remove') {
            if ($uuid !== null) {
                $mdl->dnsbl->blocklist->del($uuid);
            }
        } elseif ($action === 'disable') {
            if ($node !== null) {
                $node->enabled = '0';
                $node->wildcards = $domains;
                $node->source_nets = $sourceIp;
            }
        } else { // enable
            if ($node === null) {
                $node = $mdl->dnsbl->blocklist->Add();
                $node->description = $description;
            }
            $node->enabled = '1';
            $node->wildcards = $domains;
            $node->source_nets = $sourceIp;
            $node->nxdomain = '1';
        }

        foreach ($mdl->performValidation() as $msg) {
            return ['status' => 'error', 'error' => (string)$msg];
        }

        $mdl->serializeToConfig();
        Config::getInstance()->save();
    } finally {
        Config::getInstance()->unlock();
    }

    $backend = new Backend();
    // Two steps, both confirmed live this session as genuinely required:
    // the template render alone doesn't apply anything to Unbound's
    // actual running state, and running the dnsbl action alone would
    // apply against stale already-rendered config.
    $backend->configdRun('template reload OPNsense/Unbound/core');
    $backend->configdRun('unbound dnsbl');

    return ['status' => 'ok', 'action' => $action];
}

echo json_encode(main($argv)) . "\n";
