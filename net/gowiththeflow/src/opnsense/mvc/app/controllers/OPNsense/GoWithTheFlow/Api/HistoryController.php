<?php

namespace OPNsense\GoWithTheFlow\Api;

class HistoryController extends DbApiControllerBase
{
    public function searchAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $localHost = $this->request->getPost('local_host') ?: '';
        $cutoff = time() - $days * 86400;
        $table = $this->rollupTableForDays($days);

        $records = [];
        $localHosts = [];
        $db = $this->openDb();
        if ($db !== null) {
            // peer_is_local=1 rows are canonicalized (local_ip < peer_ip
            // numerically) by rollup.py, so filtering by :local_ip must also
            // match rows where it landed on the peer_ip side -- otherwise a
            // device's internal-pair traffic silently disappears from this
            // filter whenever it's the numerically larger member of the
            // pair. When that's the case, bytes_in/bytes_out (stored
            // relative to r1.local_ip) are swapped so the filtered host's
            // own in/out are never shown backwards.
            $sql = "
                SELECT
                  r1.local_ip, r1.peer_ip, r1.peer_is_local,
                  CASE WHEN r1.peer_is_local = 1
                       THEN (SELECT hostname FROM local_host_identity WHERE ip = r1.peer_ip
                             ORDER BY updated_at DESC LIMIT 1)
                       ELSE (SELECT r2.peer_hostname FROM $table r2
                             WHERE r2.local_ip = r1.local_ip AND r2.peer_ip = r1.peer_ip
                               AND r2.bucket_start >= :cutoff AND r2.peer_hostname IS NOT NULL
                             ORDER BY r2.bucket_start DESC LIMIT 1)
                  END AS peer_hostname,
                  (SELECT r2.category FROM $table r2
                   WHERE r2.local_ip = r1.local_ip AND r2.peer_ip = r1.peer_ip
                     AND r2.bucket_start >= :cutoff AND r2.category IS NOT NULL
                   ORDER BY r2.bucket_start DESC LIMIT 1) AS category,
                  (SELECT r2.dpi_protocol FROM $table r2
                   WHERE r2.local_ip = r1.local_ip AND r2.peer_ip = r1.peer_ip
                     AND r2.bucket_start >= :cutoff AND r2.dpi_protocol IS NOT NULL
                   ORDER BY r2.bucket_start DESC LIMIT 1) AS dpi_protocol,
                  lhi.hostname AS local_hostname,
                  SUM(CASE WHEN :local_ip != '' AND r1.peer_is_local = 1 AND r1.peer_ip = :local_ip
                           THEN r1.bytes_out ELSE r1.bytes_in END) AS bytes_in,
                  SUM(CASE WHEN :local_ip != '' AND r1.peer_is_local = 1 AND r1.peer_ip = :local_ip
                           THEN r1.bytes_in ELSE r1.bytes_out END) AS bytes_out,
                  SUM(r1.conn_count) AS conn_count,
                  MAX(r1.bucket_start) AS last_seen
                FROM $table r1
                LEFT JOIN local_host_identity lhi ON lhi.ip = r1.local_ip
                WHERE r1.bucket_start >= :cutoff
            ";
            if ($localHost !== '') {
                $sql .= ' AND (r1.local_ip = :local_ip OR (r1.peer_is_local = 1 AND r1.peer_ip = :local_ip))';
            }
            $sql .= ' GROUP BY r1.local_ip, r1.peer_ip, r1.peer_is_local';

            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            $stmt->bindValue(':local_ip', $localHost, SQLITE3_TEXT);
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['local_ip'] . '-' . $row['peer_ip'];
                $row['local'] = $this->formatHost($row['local_hostname'], $row['local_ip']);
                $row['peer'] = $this->formatHost($row['peer_hostname'], $row['peer_ip']);
                $row['bytes_total'] = (int)$row['bytes_in'] + (int)$row['bytes_out'];
                $records[] = $row;
            }

            $lhResult = $db->query('SELECT DISTINCT ip, hostname FROM local_host_identity WHERE ip IS NOT NULL');
            while ($lhRow = $lhResult->fetchArray(SQLITE3_ASSOC)) {
                $localHosts[$lhRow['ip']] = $this->formatHost($lhRow['hostname'], $lhRow['ip']);
            }
        }

        $response = $this->searchRecordsetBase($records, null, 'bytes_total');
        $response['local_hosts'] = $localHosts;
        return $response;
    }

    public function timeseriesAction()
    {
        // Plain data endpoint for the History Overview chart -- not a
        // Bootgrid search. One line per local host (combined bytes
        // in+out, internet + internal traffic together, matching Top
        // Talkers' "include everything" ranking), bucketed hourly or
        // daily directly from the already-aggregated rollup tables (no
        // per-minute resolution -- considered and dropped as overkill).
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $bucketParam = $this->request->getPost('bucket') ?: 'hour';
        $bucket = $bucketParam === 'day' ? 'day' : 'hour';
        $localHost = $this->request->getPost('local_host') ?: '';
        $cutoff = time() - $days * 86400;
        $table = $bucket === 'day' ? 'rollup_daily' : 'rollup_hourly';

        $bucketsSet = [];
        $perHost = [];
        $totalsByHost = [];
        $localHosts = [];

        $db = $this->openDb();
        if ($db !== null) {
            $sql = "
                SELECT bucket_start, local_ip, SUM(bytes_in + bytes_out) AS bytes
                FROM $table
                WHERE bucket_start >= :cutoff
            ";
            if ($localHost !== '') {
                $sql .= ' AND local_ip = :local_ip';
            }
            $sql .= ' GROUP BY bucket_start, local_ip ORDER BY bucket_start';

            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            if ($localHost !== '') {
                $stmt->bindValue(':local_ip', $localHost, SQLITE3_TEXT);
            }
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $bucketStart = (int)$row['bucket_start'];
                $ip = $row['local_ip'];
                $bytes = (int)$row['bytes'];
                $bucketsSet[$bucketStart] = true;
                $perHost[$ip][$bucketStart] = $bytes;
                $totalsByHost[$ip] = ($totalsByHost[$ip] ?? 0) + $bytes;
            }

            $lhResult = $db->query('SELECT DISTINCT ip, hostname FROM local_host_identity WHERE ip IS NOT NULL');
            while ($lhRow = $lhResult->fetchArray(SQLITE3_ASSOC)) {
                $localHosts[$lhRow['ip']] = $this->formatHost($lhRow['hostname'], $lhRow['ip']);
            }
        }

        $buckets = array_keys($bucketsSet);
        sort($buckets);

        // Cap to the busiest 10 hosts, lumping the rest into "Other" --
        // same rule as Live's Overview chart, avoids an unreadable chart
        // when there are many local devices.
        arsort($totalsByHost);
        $hostsByTotal = array_keys($totalsByHost);
        $topHosts = array_slice($hostsByTotal, 0, 10);
        $otherHosts = array_slice($hostsByTotal, 10);

        $series = [];
        foreach ($topHosts as $ip) {
            $series[$ip] = [];
            foreach ($buckets as $b) {
                $series[$ip][] = $perHost[$ip][$b] ?? 0;
            }
            if (!isset($localHosts[$ip])) {
                $localHosts[$ip] = $ip;
            }
        }
        if (!empty($otherHosts)) {
            $otherSeries = [];
            foreach ($buckets as $b) {
                $sum = 0;
                foreach ($otherHosts as $ip) {
                    $sum += $perHost[$ip][$b] ?? 0;
                }
                $otherSeries[] = $sum;
            }
            $series['Other'] = $otherSeries;
        }

        return [
            'buckets' => $buckets,
            'series' => $series,
            'local_hosts' => $localHosts,
        ];
    }
}
