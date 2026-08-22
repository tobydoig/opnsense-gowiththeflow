<?php

namespace OPNsense\GoWithTheFlow\Api;

class ToptalkersController extends DbApiControllerBase
{
    public function localAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $cutoff = time() - $days * 86400;
        $table = $this->rollupTableForDays($days);

        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            $sql = "
                SELECT
                  r.local_ip,
                  lhi.hostname AS local_hostname,
                  SUM(r.bytes_in) AS bytes_in, SUM(r.bytes_out) AS bytes_out,
                  SUM(r.conn_count) AS conn_count
                FROM $table r
                LEFT JOIN local_host_identity lhi ON lhi.ip = r.local_ip
                WHERE r.bucket_start >= :cutoff
                GROUP BY r.local_ip
            ";
            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['local_ip'];
                $row['local'] = $this->formatHost($row['local_hostname'], $row['local_ip']);
                $row['bytes_total'] = (int)$row['bytes_in'] + (int)$row['bytes_out'];
                $records[] = $row;
            }
        }

        return $this->searchRecordsetBase($records, null, 'bytes_total');
    }

    public function remoteAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $localHost = $this->request->getPost('local_host');
        $cutoff = time() - $days * 86400;
        $table = $this->rollupTableForDays($days);

        $records = [];
        $localHosts = [];
        $db = $this->openDb();
        if ($db !== null) {
            $sql = "
                SELECT
                  r.remote_ip,
                  (SELECT r2.remote_hostname FROM $table r2
                   WHERE r2.remote_ip = r.remote_ip
                     AND r2.bucket_start >= :cutoff AND r2.remote_hostname IS NOT NULL
                   ORDER BY r2.bucket_start DESC LIMIT 1) AS remote_hostname,
                  SUM(r.bytes_in) AS bytes_in, SUM(r.bytes_out) AS bytes_out,
                  SUM(r.conn_count) AS conn_count
                FROM $table r
                WHERE r.bucket_start >= :cutoff
            ";
            if (!empty($localHost)) {
                $sql .= ' AND r.local_ip = :local_ip';
            }
            $sql .= ' GROUP BY r.remote_ip';

            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            if (!empty($localHost)) {
                $stmt->bindValue(':local_ip', $localHost, SQLITE3_TEXT);
            }
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['remote_ip'];
                $row['remote'] = $this->formatHost($row['remote_hostname'], $row['remote_ip']);
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
}
