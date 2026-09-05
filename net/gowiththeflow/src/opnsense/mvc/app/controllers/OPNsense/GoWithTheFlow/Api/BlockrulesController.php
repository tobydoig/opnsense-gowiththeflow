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
                // hostname/mac per device are snapshotted at rule-creation
                // time, same rationale as blocked_hosts' own columns (see
                // db.py) -- deliberately not live-joined against
                // local_host_identity.
                $row['device'] = $this->formatDeviceList($row['devices']);
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
     * "quest3s, ps5, iphone-max" -- every device in a rule's group,
     * joined for display; a plain IP for any device with no known
     * hostname. No truncation at this rule count; revisit only if a
     * group with many devices turns out to need it.
     */
    private function formatDeviceList(?string $devicesJson): string
    {
        $devices = json_decode((string)$devicesJson, true);
        if (!is_array($devices) || count($devices) === 0) {
            return '';
        }
        return implode(', ', array_map(
            fn($d) => $this->formatHost($d['hostname'] ?? null, $d['ip'] ?? ''),
            $devices
        ));
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
     * honestly-labeled tradeoff (see DESIGN.md). Checked before
     * last_effective_state, not after -- set_enabled()'s disable path
     * unwinds enforcement directly rather than through apply_rule(), so
     * it never updates last_effective_state; without this a paused rule
     * would keep showing whatever "Blocked"/"Not blocked" it last had
     * while still enabled, which is actively misleading now that
     * pause/resume (gwtftoggle) is reachable from the grid at all.
     */
    private function formatStatus(array $row): string
    {
        if (empty($row['enabled'])) {
            return 'Paused';
        }
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

    /**
     * `$raw` is a JSON-encoded array of raw device strings as typed by
     * the user -- IP or known hostname, one per repeatable device-list
     * row -- same acceptance resolveDeviceIp() already gave a single
     * device before groups existed, just looped and JSON-decoded first
     * since a group needs an actual list, not one POST field. Each
     * array entry is itself split on commas too (not just trimmed) --
     * the client already does this before ever sending the request, but
     * a comma-separated list typed into one row (the same convention the
     * Domains field already uses) must not silently become one bogus
     * compound "device" if that client-side split were ever bypassed or
     * out of sync; confirmed live this was the actual failure mode
     * before the client-side fix landed. Returns [resolved IPs, null] or
     * [null, error naming the offending token].
     */
    private function resolveDevices(string $raw): array
    {
        $tokens = json_decode($raw, true);
        if (!is_array($tokens) || count($tokens) === 0) {
            return [null, 'at least one device is required'];
        }
        $ips = [];
        foreach ($tokens as $token) {
            foreach (explode(',', (string)$token) as $piece) {
                $piece = trim($piece);
                if ($piece === '') {
                    continue;
                }
                $ip = $this->resolveDeviceIp($piece);
                if ($ip === null) {
                    return [null, "not a valid IP address, and no known device is named '{$piece}'"];
                }
                $ips[] = $ip;
            }
        }
        if (count($ips) === 0) {
            return [null, 'at least one device is required'];
        }
        return [$ips, null];
    }

    /**
     * Per-device guards for a rule's group, run once per device so the
     * *first* violation names the specific device rather than failing
     * generically -- host type refuses the firewall's-own-address the
     * same way addAction() always has; domain type requires every
     * device to already have a static DHCP reservation, since Unbound's
     * dnsbl.blocklist match is by source IP and a device without one
     * could silently drift to match nothing (or something else) after
     * its next lease renewal.
     */
    private function validateDevicesForType(string $type, array $ips, string $domains): ?string
    {
        if ($type === 'host') {
            $clientIp = $this->request->getClientAddress();
            foreach ($ips as $ip) {
                if ($ip === $clientIp) {
                    return 'refusing to block the address you\'re currently connected from';
                }
            }
            return null;
        }
        if (trim($domains) === '') {
            return 'at least one domain is required';
        }
        foreach ($ips as $ip) {
            [, $mac] = $this->lookupIdentity($ip);
            if (empty($mac)) {
                return "{$ip}: no known MAC address for this device yet -- has it been seen on the network?";
            }
            $reservedIp = $this->findDhcpReservationIp($mac);
            if ($reservedIp === null) {
                return "{$ip}: this device has no static DHCP reservation -- add one in " .
                    'Services > Dnsmasq DNS & DHCP > Hosts before creating a domain block';
            }
            if ($reservedIp !== $ip) {
                return "{$ip}: this device's DHCP reservation is for {$reservedIp} -- " .
                    'its current lease may not have renewed to the reserved address yet';
            }
        }
        return null;
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
        $name = trim((string)$this->request->getPost('name'));
        $devicesRaw = (string)($this->request->getPost('devices') ?: '[]');
        $domains = $this->request->getPost('domains') ?: '';
        $schedule = $this->request->getPost('schedule') ?: '';
        $reason = $this->request->getPost('reason') ?: '';

        if (!in_array($type, ['host', 'domain'], true)) {
            return ['status' => 'failed', 'error' => 'invalid rule type'];
        }
        if ($name === '') {
            return ['status' => 'failed', 'error' => 'a name is required'];
        }
        [$ips, $error] = $this->resolveDevices($devicesRaw);
        if ($error !== null) {
            return ['status' => 'failed', 'error' => $error];
        }
        $error = $this->validateDevicesForType($type, $ips, $domains);
        if ($error !== null) {
            return ['status' => 'failed', 'error' => $error];
        }

        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_create', [
            $type, $name, implode(',', $ips), $domains, $schedule, $this->logged_in_user ?: 'unknown', $reason,
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
        $name = trim((string)$this->request->getPost('name'));
        $devicesRaw = (string)($this->request->getPost('devices') ?: '[]');
        $domains = $this->request->getPost('domains') ?: '';
        $schedule = $this->request->getPost('schedule') ?: '';

        if ($name === '') {
            return ['status' => 'failed', 'error' => 'a name is required'];
        }
        [$ips, $error] = $this->resolveDevices($devicesRaw);
        if ($error !== null) {
            return ['status' => 'failed', 'error' => $error];
        }

        // editAction() never changes a rule's type -- but the per-device
        // guard needs to know which one applies, and trusting a hidden
        // form field for that is trivially spoofable, so read the type
        // back from the rule itself instead.
        $db = $this->openDb();
        $type = null;
        if ($db !== null) {
            $stmt = $db->prepare('SELECT rule_type FROM block_rules WHERE id = :id');
            $stmt->bindValue(':id', (int)$id, SQLITE3_INTEGER);
            $row = $stmt->execute()->fetchArray(SQLITE3_ASSOC);
            $type = $row !== false ? $row['rule_type'] : null;
        }
        if ($type === null) {
            return ['status' => 'failed', 'error' => "no such rule: {$id}"];
        }
        $error = $this->validateDevicesForType($type, $ips, $domains);
        if ($error !== null) {
            return ['status' => 'failed', 'error' => $error];
        }

        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_edit', [
            $id, $name, implode(',', $ips), $domains, $schedule,
        ]), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the edit action'];
        }
        return $result;
    }

    public function duplicateAction($id)
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow rule_duplicate', [$id]), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the duplicate action'];
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
