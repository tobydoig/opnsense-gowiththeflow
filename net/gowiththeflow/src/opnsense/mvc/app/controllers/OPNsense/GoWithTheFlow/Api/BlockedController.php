<?php

namespace OPNsense\GoWithTheFlow\Api;

class BlockedController extends DbApiControllerBase
{
    /**
     * Flat {"blocked": ["10.0.0.5", ...]} -- for the Live page's per-row
     * block-icon state. searchAction()'s paginated Bootgrid envelope
     * can't tell the Live grids about a blocked host that happens to be
     * on some other page of the Blocked list, so this is a separate,
     * unpaginated endpoint rather than reusing searchAction() there.
     */
    public function listAction()
    {
        $ips = [];
        $db = $this->openDb();
        if ($db !== null) {
            $result = $db->query('SELECT local_ip FROM blocked_hosts');
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $ips[] = $row['local_ip'];
            }
        }
        return ['blocked' => $ips];
    }

    /**
     * PHP never touches pf or writes to this plugin's own database
     * directly -- it only reads (via the inherited openDb(), opened
     * read-only) and mutates through configd, the same "PHP reads,
     * Python writes" split this project already follows everywhere
     * else (block_host.py does the actual DB write + pf table sync +
     * state kill, all as one operation under root).
     */
    public function blockAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $ip = $this->request->getPost('local_ip');
        if (!filter_var($ip, FILTER_VALIDATE_IP)) {
            return ['status' => 'failed', 'error' => 'not a valid IP address'];
        }
        // Our block rules land at priority 1, above OPNsense's own
        // anti-lockout allow rule (priority 5) -- refusing to block the
        // browsing client's own current address is the one guard against
        // an immediate, total self-lockout that's possible to enforce
        // here; blocking some *other* device the admin also happens to
        // use for management is a real, accepted residual risk (see
        // DESIGN.md).
        if ($ip === $this->request->getClientAddress()) {
            return ['status' => 'failed', 'error' => 'refusing to block the address you\'re currently connected from'];
        }

        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow block', [$ip, $this->logged_in_user ?: 'unknown']), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the block action'];
        }
        return $result;
    }

    public function unblockAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $ip = $this->request->getPost('local_ip');
        if (!filter_var($ip, FILTER_VALIDATE_IP)) {
            return ['status' => 'failed', 'error' => 'not a valid IP address'];
        }

        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdpRun('gowiththeflow unblock', [$ip]), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the unblock action'];
        }
        return $result;
    }
}
