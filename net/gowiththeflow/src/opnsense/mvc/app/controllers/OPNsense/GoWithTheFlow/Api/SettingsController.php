<?php

namespace OPNsense\GoWithTheFlow\Api;

use OPNsense\Base\ApiMutableModelControllerBase;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'gowiththeflow';
    protected static $internalModelClass = '\OPNsense\GoWithTheFlow\GoWithTheFlow';

    // Matches gowiththeflowd.py's Config.db_path default -- not a
    // user-configurable setting, just the fixed on-disk convention.
    private const DB_PATH = '/var/db/gowiththeflow/flows.db';

    public function clearDataAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        if (!file_exists(self::DB_PATH)) {
            return ['status' => 'ok'];
        }
        $db = new \SQLite3(self::DB_PATH);
        $db->exec('DELETE FROM connections_raw');
        $db->exec('DELETE FROM rollup_hourly');
        $db->exec('DELETE FROM rollup_daily');
        $db->exec('DELETE FROM live_sessions');
        return ['status' => 'ok'];
    }

    public function resetHostnameCacheAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        if (!file_exists(self::DB_PATH)) {
            return ['status' => 'ok'];
        }
        $db = new \SQLite3(self::DB_PATH);
        $db->exec('DELETE FROM ip_hostname_cache');
        return ['status' => 'ok'];
    }

    /**
     * Re-applies today's category logic (manual overrides, then the
     * v2fly-based matcher) across already-recorded history -- a
     * domain_categories/ addition only affects newly-observed traffic
     * otherwise, since category is stamped once when a connection is
     * first written and never revisited. Needs Python-side matching
     * logic (recategorize.py, shared with gowiththeflowd.py via
     * categories.resolve_category()), so unlike the two actions above
     * this shells out via configd rather than a direct SQLite delete.
     */
    public function recategorizeAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new \OPNsense\Core\Backend();
        $result = json_decode($backend->configdRun('gowiththeflow recategorize'), true);
        if (!is_array($result)) {
            return ['status' => 'failed', 'error' => 'no response from the recategorize action'];
        }
        return $result;
    }
}
