<?php

namespace OPNsense\GoWithTheFlow\Api;

class BlockrulesController extends DbApiControllerBase
{
    /**
     * Bootgrid-shaped, for the unified Block Rules page -- one row per
     * rule, host-only or host+domain, each with an optional schedule.
     * Replaces History > Blocked's old blocked_hosts-only view.
     */
    public function searchAction()
    {
        $records = [];
        $localHosts = [];
        $db = $this->openDb();
        if ($db !== null) {
            $result = $db->query('SELECT * FROM block_rules ORDER BY created_at DESC');
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['id'];
                // hostname/mac are snapshotted at rule-creation time, same
                // rationale as blocked_hosts' own columns (see db.py) --
                // deliberately not live-joined against local_host_identity.
                $row['device'] = $this->formatHost($row['hostname'], $row['local_ip']);
                $row['type_label'] = $row['rule_type'] === 'host' ? 'Host' : 'Host + Domain';
                $row['schedule_label'] = $this->formatSchedule($row['schedule_json']);
                $row['status_label'] = $this->formatStatus($row);
                $records[] = $row;
            }

            // For the Add/Edit dialog's device autocomplete -- every
            // locally-known device, not just ones already in block_rules,
            // same "DnsqueriesController-style" local_hosts map this
            // project's other filter dropdowns already return alongside
            // their own search results. Ships the bare hostname
            // separately from the display label so the dialog can fill
            // the device field with the *name* (stable across a DHCP
            // lease change) rather than the IP snapshot when a suggestion
            // is picked -- resolveDeviceIp() already accepts either.
            $lhResult = $db->query('SELECT DISTINCT ip, hostname FROM local_host_identity WHERE ip IS NOT NULL');
            while ($lhRow = $lhResult->fetchArray(SQLITE3_ASSOC)) {
                $localHosts[$lhRow['ip']] = [
                    'hostname' => $lhRow['hostname'],
                    'label' => $this->formatHost($lhRow['hostname'], $lhRow['ip']),
                ];
            }
        }

        $response = $this->searchRecordsetBase($records, null, 'created_at');
        $response['local_hosts'] = $localHosts;
        return $response;
    }

    /**
     * "Mon,Tue,Wed,Thu,Fri 20:00-08:00; Sat,Sun 21:00-07:30", or "Always"
     * for a schedule-less rule -- a display-only summary. The real
     * schedule predicate lives in block_schedule.py; duplicating that
     * logic here would risk it drifting out of sync, so this only ever
     * needs to be good enough to read, not authoritative.
     */
    private function formatSchedule(?string $scheduleJson): string
    {
        if (empty($scheduleJson)) {
            return 'Always';
        }
        $data = json_decode($scheduleJson, true);
        if (!is_array($data) || empty($data['windows'])) {
            return 'Always';
        }
        $parts = [];
        foreach ($data['windows'] as $window) {
            $days = implode(',', array_map('ucfirst', $window['days'] ?? []));
            $parts[] = sprintf('%s %s-%s', $days, $window['start'] ?? '?', $window['end'] ?? '?');
        }
        return implode('; ', $parts);
    }

    /**
     * Reads block_rules.last_effective_state/last_evaluated_at, written
     * by block_rules_engine.py's own reconcile tick, rather than
     * re-implementing the schedule predicate here -- up to one reconcile
     * interval's staleness right after a boundary is an accepted,
     * honestly-labeled tradeoff (see DESIGN.md).
     */
    private function formatStatus(array $row): string
    {
        if (empty($row['last_effective_state'])) {
            return 'Pending';
        }
        $label = $row['last_effective_state'] === 'blocked' ? 'Blocked' : 'Not blocked';
        if (!empty($row['manual_override_state']) && !empty($row['override_until'])) {
            $label .= sprintf(' (override until %s)', date('H:i', (int)$row['override_until']));
        }
        return $label;
    }

    /**
     * Accepts either a raw IP address or a known hostname for the "Device"
     * field -- resolved against local_host_identity (the same "most
     * recent wins" idiom block_host.py's own _lookup_identity() already
     * uses, since a hostname can be reassigned a different IP over time)
     * so the user doesn't need to go look up a device's current IP by
     * hand before creating a rule. Returns null if it's neither a valid
     * IP nor a recognized hostname.
     */
    private function resolveDeviceIp(string $input): ?string
    {
        $input = trim($input);
        if ($input === '') {
            return null;
        }
        if (filter_var($input, FILTER_VALIDATE_IP)) {
            return $input;
        }
        $db = $this->openDb();
        if ($db === null) {
            return null;
        }
        $stmt = $db->prepare(
            'SELECT ip FROM local_host_identity WHERE LOWER(hostname) = LOWER(:hostname) ORDER BY updated_at DESC LIMIT 1'
        );
        $stmt->bindValue(':hostname', $input, SQLITE3_TEXT);
        $row = $stmt->execute()->fetchArray(SQLITE3_ASSOC);
        return $row !== false ? $row['ip'] : null;
    }

    private function lookupIdentity(string $ip): array
    {
        $db = $this->openDb();
        if ($db === null) {
            return [null, null];
        }
        $stmt = $db->prepare(
            'SELECT hostname, mac FROM local_host_identity WHERE ip = :ip ORDER BY updated_at DESC LIMIT 1'
        );
        $stmt->bindValue(':ip', $ip, SQLITE3_TEXT);
        $row = $stmt->execute()->fetchArray(SQLITE3_ASSOC);
        return $row !== false ? [$row['hostname'], $row['mac']] : [null, null];
    }

    /**
     * Static DHCP reservation lookup for a domain rule's target device --
     * confirmed live this session that Dnsmasq.xml's `hosts` ArrayField
     * (Services > Dnsmasq DNS & DHCP > Hosts) combines DNS host overrides
     * and DHCP static reservations in one array; a row with both
     * `hwaddr` and `ip` populated is a reservation. This project's
     * DHCP/local-DNS backend is always Dnsmasq (see DESIGN.md), never
     * Kea or ISC dhcpd, so no other backend needs checking here.
     */
    private function findDhcpReservationIp(string $mac): ?string
    {
        $mdl = new \OPNsense\Dnsmasq\Dnsmasq();
        $mac = strtolower($mac);
        foreach ($mdl->hosts->iterateItems() as $node) {
            $macs = array_map('strtolower', array_filter(array_map('trim', explode(',', (string)$node->hwaddr))));
            if (in_array($mac, $macs, true) && (string)$node->ip !== '') {
                return (string)$node->ip;
            }
        }
        return null;
    }

    /**
     * PHP never touches pf, Unbound's model, or this plugin's own
     * database directly -- it only reads (via openDb(), read-only) and
     * mutates through configd, the same "PHP reads, Python writes"
     * split this project already follows everywhere else.
     */
    public function addAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $type = $this->request->getPost('rule_type');
        $rawDevice = (string)$this->request->getPost('local_ip');
        $domains = $this->request->getPost('domains') ?: '';
        $schedule = $this->request->getPost('schedule') ?: '';
        $reason = $this->request->getPost('reason') ?: '';

        if (!in_array($type, ['host', 'domain'], true)) {
            return ['status' => 'failed', 'error' => 'invalid rule type'];
        }
        $ip = $this->resolveDeviceIp($rawDevice);
        if ($ip === null) {
            return [
                'status' => 'failed',
                'error' => "not a valid IP address, and no known device is named '{$rawDevice}'",
            ];
        }

        if ($type === 'host') {
            // Same total-block lockout guard blockAction() already uses --
            // see its own comment for why this is the one guard enforced
            // here rather than at the pf-rule-priority level.
            if ($ip === $this->request->getClientAddress()) {
                return ['status' => 'failed', 'error' => 'refusing to block the address you\'re currently connected from'];
            }
        } else {
            if (trim($domains) === '') {
                return ['status' => 'failed', 'error' => 'at least one domain is required'];
            }
            [, $mac] = $this->lookupIdentity($ip);
            if (empty($mac)) {
                return ['status' => 'failed', 'error' => 'no known MAC address for this device yet -- has it been seen on the network?'];
            }
            $reservedIp = $this->findDhcpReservationIp($mac);
            if ($reservedIp === null) {
                return [
                    'status' => 'failed',
                    'error' => 'this device has no static DHCP reservation -- add one in ' .
                        'Services > Dnsmasq DNS & DHCP > Hosts before creating a domain block',
                ];
            }
            if ($reservedIp !== $ip) {
                return [
                    'status' => 'failed',
                    'error' => "this device's DHCP reservation is for {$reservedIp}, not {$ip} -- " .
                        'its current lease may not have renewed to the reserved address yet',
                ];
            }
        }

        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_create', [
            $type, $ip, $domains, $schedule, $this->logged_in_user ?: 'unknown', $reason,
        ]), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the create action'];
        }
        return $result;
    }

    public function editAction($id)
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $domains = $this->request->getPost('domains') ?: '';
        $schedule = $this->request->getPost('schedule') ?: '';

        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_edit', [$id, $domains, $schedule]), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the edit action'];
        }
        return $result;
    }

    public function delAction($id)
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_delete', [$id]), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the delete action'];
        }
        return $result;
    }

    public function setEnabledAction($id, $enabled)
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_set_enabled', [$id, $enabled ? '1' : '0']), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the set_enabled action'];
        }
        return $result;
    }

    public function overrideAction($id)
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $state = $this->request->getPost('state');
        if (!in_array($state, ['blocked', 'unblocked'], true)) {
            return ['status' => 'failed', 'error' => 'invalid override state'];
        }
        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_override', [$id, $state]), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the override action'];
        }
        return $result;
    }
}
