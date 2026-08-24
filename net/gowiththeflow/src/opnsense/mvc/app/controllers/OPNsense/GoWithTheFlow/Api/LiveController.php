<?php

namespace OPNsense\GoWithTheFlow\Api;

class LiveController extends DbApiControllerBase
{
    public function searchAction()
    {
        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            // peer_hostname is only ever resolved (via correlator.py) for a
            // genuine internet peer -- when peer_is_local=1 (the peer is
            // also a local host), name it via the same local_host_identity
            // lookup local_ip already uses, rather than the stored (always
            // NULL in that case) column.
            $result = $db->query(
                'SELECT ls.proto, ls.local_ip, ls.local_port, ls.peer_ip, ls.peer_port,
                        ls.peer_is_local, ls.hostname_source, ls.category, ls.state,
                        ls.first_seen, ls.last_seen,
                        ls.bytes_in, ls.bytes_out, ls.pkts_in, ls.pkts_out,
                        lhi.hostname AS local_hostname,
                        CASE WHEN ls.peer_is_local = 1
                             THEN (SELECT hostname FROM local_host_identity WHERE ip = ls.peer_ip
                                   ORDER BY updated_at DESC LIMIT 1)
                             ELSE ls.peer_hostname
                        END AS peer_hostname
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
                    $row['peer_ip'],
                    $row['peer_port']
                );
                $row['local'] = $this->formatHost($row['local_hostname'], $row['local_ip']);
                $row['peer'] = $this->formatHost($row['peer_hostname'], $row['peer_ip']);
                $row['duration'] = max($now - (int)$row['first_seen'], 0);
                $records[] = $row;
            }
        }

        return $this->searchRecordsetBase($records, null, 'last_seen');
    }
}
