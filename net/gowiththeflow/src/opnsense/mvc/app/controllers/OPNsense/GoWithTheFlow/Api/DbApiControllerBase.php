<?php

namespace OPNsense\GowiththeFlow\Api;

use OPNsense\Base\ApiControllerBase;

abstract class DbApiControllerBase extends ApiControllerBase
{
    // TODO(Phase B5): move to the Settings model once it exists.
    // Production path will be /var/db/gowiththeflow/flows.db.
    protected const DB_PATH = '/tmp/test_flows.db';

    // Matches rollup.py's default RollupHourlyRetentionDays -- hourly
    // buckets aren't kept past this, so anything asking for a longer
    // window must read the (coarser, longer-retained) daily rollup.
    protected const HOURLY_RETENTION_DAYS = 45;

    protected function openDb(): ?\SQLite3
    {
        if (!file_exists(static::DB_PATH)) {
            return null;
        }
        $db = new \SQLite3(static::DB_PATH, SQLITE3_OPEN_READONLY);
        $db->busyTimeout(5000);
        return $db;
    }

    protected function formatHost($hostname, $ip)
    {
        return !empty($hostname) ? sprintf('%s (%s)', $hostname, $ip) : $ip;
    }

    /**
     * Picks rollup_hourly vs. rollup_daily depending on how far back
     * `days` needs to reach, given hourly rows aren't kept forever.
     */
    protected function rollupTableForDays(int $days): string
    {
        return $days <= self::HOURLY_RETENTION_DAYS ? 'rollup_hourly' : 'rollup_daily';
    }
}
