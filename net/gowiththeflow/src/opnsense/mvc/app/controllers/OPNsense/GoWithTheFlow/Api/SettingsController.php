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
}
