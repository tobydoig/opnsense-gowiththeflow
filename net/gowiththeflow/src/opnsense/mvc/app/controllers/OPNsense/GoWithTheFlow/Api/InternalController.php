<?php

namespace OPNsense\GoWithTheFlow\Api;

class InternalController extends DbApiControllerBase
{
    public function searchAction()
    {
        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            // Two correlated subqueries rather than two LEFT JOINs --
            // local_host_identity is keyed by MAC with only a non-unique
            // index on ip, so joining it twice in one query would risk a
            // 2x2 row fan-out if either side ever has a duplicate-ip row
            // (e.g. a stale ARP entry during a lease handoff).
            $result = $db->query(
                'SELECT ils.proto, ils.ip_a, ils.port_a, ils.ip_b, ils.port_b,
                        ils.first_seen, ils.last_seen,
                        ils.bytes_a_to_b, ils.bytes_b_to_a, ils.pkts_a_to_b, ils.pkts_b_to_a,
                        (SELECT hostname FROM local_host_identity WHERE ip = ils.ip_a
                         ORDER BY updated_at DESC LIMIT 1) AS ip_a_hostname,
                        (SELECT hostname FROM local_host_identity WHERE ip = ils.ip_b
                         ORDER BY updated_at DESC LIMIT 1) AS ip_b_hostname
                 FROM internal_live_sessions ils'
            );
            $now = time();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = sprintf(
                    '%s-%s:%d-%s:%d',
                    $row['proto'],
                    $row['ip_a'],
                    $row['port_a'],
                    $row['ip_b'],
                    $row['port_b']
                );
                $row['host_a'] = $this->formatHost($row['ip_a_hostname'], $row['ip_a']);
                $row['host_b'] = $this->formatHost($row['ip_b_hostname'], $row['ip_b']);
                $row['duration'] = max($now - (int)$row['first_seen'], 0);
                $records[] = $row;
            }
        }

        return $this->searchRecordsetBase($records, null, 'last_seen');
    }

    public function historyAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $cutoff = time() - $days * 86400;
        // rollupTableForDays() returns the remote-traffic table names --
        // this pipeline's tables are the same shape, just "internal_"
        // prefixed, so compose rather than duplicating that logic.
        $table = 'internal_' . $this->rollupTableForDays($days);

        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            // ip_a/ip_b are already canonicalized (lower IP first) by
            // rollup.rollup_internal_hourly() -- no extra normalization
            // needed here, unlike the live tab's raw, per-flow rows.
            $sql = "
                SELECT
                  r.ip_a, r.ip_b,
                  (SELECT hostname FROM local_host_identity WHERE ip = r.ip_a
                   ORDER BY updated_at DESC LIMIT 1) AS ip_a_hostname,
                  (SELECT hostname FROM local_host_identity WHERE ip = r.ip_b
                   ORDER BY updated_at DESC LIMIT 1) AS ip_b_hostname,
                  SUM(r.bytes_a_to_b) AS bytes_a_to_b, SUM(r.bytes_b_to_a) AS bytes_b_to_a,
                  SUM(r.conn_count) AS conn_count
                FROM $table r
                WHERE r.bucket_start >= :cutoff
                GROUP BY r.ip_a, r.ip_b
            ";
            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['ip_a'] . '-' . $row['ip_b'];
                $row['host_a'] = $this->formatHost($row['ip_a_hostname'], $row['ip_a']);
                $row['host_b'] = $this->formatHost($row['ip_b_hostname'], $row['ip_b']);
                $row['bytes_total'] = (int)$row['bytes_a_to_b'] + (int)$row['bytes_b_to_a'];
                $records[] = $row;
            }
        }

        return $this->searchRecordsetBase($records, null, 'bytes_total');
    }
}
