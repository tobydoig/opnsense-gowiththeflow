<?php

namespace OPNsense\GowiththeFlow\Api;

use OPNsense\Base\ApiControllerBase;

class LiveController extends ApiControllerBase
{
    // TODO(Phase B5): move to the Settings model once it exists.
    // Production path will be /var/db/gowiththeflow/flows.db.
    private const DB_PATH = '/tmp/test_flows.db';

    public function searchAction()
    {
        $records = [];
        if (file_exists(self::DB_PATH)) {
            $db = new \SQLite3(self::DB_PATH, SQLITE3_OPEN_READONLY);
            $db->busyTimeout(5000);
            $result = $db->query(
                'SELECT ls.proto, ls.local_ip, ls.local_port, ls.remote_ip, ls.remote_port,
                        ls.remote_hostname, ls.hostname_source,
                        ls.first_seen, ls.last_seen,
                        ls.bytes_in, ls.bytes_out, ls.pkts_in, ls.pkts_out,
                        lhi.hostname AS local_hostname
                 FROM live_sessions ls
                 LEFT JOIN local_host_identity lhi ON lhi.ip = ls.local_ip'
            );
            $now = time();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = sprintf(
                    '%s-%s:%d-%s:%d',
                    $row['proto'],
                    $row['local_ip'],
                    $row['local_port'],
                    $row['remote_ip'],
                    $row['remote_port']
                );
                $row['local'] = $this->formatHost($row['local_hostname'], $row['local_ip']);
                $row['remote'] = $this->formatHost($row['remote_hostname'], $row['remote_ip']);
                $row['duration'] = max($now - (int)$row['first_seen'], 0);
                $records[] = $row;
            }
        }

        return $this->searchRecordsetBase($records, null, 'last_seen');
    }

    private function formatHost($hostname, $ip)
    {
        return !empty($hostname) ? sprintf('%s (%s)', $hostname, $ip) : $ip;
    }
}
