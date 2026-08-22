<?php

namespace OPNsense\GoWithTheFlow\Api;

class LiveController extends DbApiControllerBase
{
    public function searchAction()
    {
        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
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
}
