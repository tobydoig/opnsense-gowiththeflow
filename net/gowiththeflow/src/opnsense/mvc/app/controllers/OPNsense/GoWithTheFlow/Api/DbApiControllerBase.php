<?php

namespace OPNsense\GoWithTheFlow\Api;

use OPNsense\Base\ApiControllerBase;

abstract class DbApiControllerBase extends ApiControllerBase
{
    // Matches gowiththeflowd.py's Config.db_path default -- not a
    // user-configurable setting, just the fixed on-disk convention.
    protected const DB_PATH = '/var/db/gowiththeflow/flows.db';

    // Matches GoWithTheFlow.xml's default rollupHourlyRetentionDays --
    // hourly buckets aren't kept past this, so anything asking for a
    // longer window must read the (coarser, longer-retained) daily
    // rollup instead.
    protected const HOURLY_RETENTION_DAYS = 8;

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
