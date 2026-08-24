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
            // peer_is_local=1 rows are canonicalized (local_ip < peer_ip
            // numerically) by rollup.py -- without this UNION ALL, a local
            // device would be undercounted here whenever it's the
            // numerically larger member of one of its internal-pair
            // relationships (its traffic landed in peer_ip, never
            // local_ip, for those rows). The second branch adds every
            // internal pair's peer_ip side back in as a "local_ip"
            // contribution (bytes swapped, since they're stored relative
            // to the row's own local_ip) so every local host's ranking
            // reflects its total traffic -- internet + internal combined.
            $sql = "
                SELECT
                  c.ip AS local_ip,
                  lhi.hostname AS local_hostname,
                  SUM(c.bytes_in) AS bytes_in, SUM(c.bytes_out) AS bytes_out,
                  SUM(c.conn_count) AS conn_count,
                  COUNT(DISTINCT c.other_ip) AS unique_peer_hosts
                FROM (
                  SELECT local_ip AS ip, peer_ip AS other_ip, bytes_in, bytes_out, conn_count
                  FROM $table WHERE bucket_start >= :cutoff
                  UNION ALL
                  SELECT peer_ip AS ip, local_ip AS other_ip, bytes_out AS bytes_in, bytes_in AS bytes_out, conn_count
                  FROM $table WHERE bucket_start >= :cutoff AND peer_is_local = 1
                ) c
                LEFT JOIN local_host_identity lhi ON lhi.ip = c.ip
                GROUP BY c.ip
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

    public function peerAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $localHost = $this->request->getPost('local_host') ?: '';
        $cutoff = time() - $days * 86400;
        $table = $this->rollupTableForDays($days);

        $records = [];
        $localHosts = [];
        $db = $this->openDb();
        if ($db !== null) {
            // UNION ALL: bullet 1 is every row's genuine peer -- a real
            // remote host, or (since peer_is_local rows aren't filtered
            // out here) the numerically-larger member of an internal pair,
            // which is always canonical peer_ip. Bullet 2 adds the
            // numerically-smaller member of every internal pair (always
            // canonical local_ip), which would otherwise never appear in
            // this ranking at all -- this is what lets a low, often-
            // statically-reserved IP (exactly the kind an NVR would have)
            // show up here, the whole motivating case for this fix.
            $sql = "
                SELECT
                  c.ip AS peer_ip,
                  CASE WHEN c.is_local = 1
                       THEN (SELECT hostname FROM local_host_identity WHERE ip = c.ip
                             ORDER BY updated_at DESC LIMIT 1)
                       ELSE (SELECT r2.peer_hostname FROM $table r2
                             WHERE r2.peer_ip = c.ip AND r2.bucket_start >= :cutoff
                               AND r2.peer_hostname IS NOT NULL
                             ORDER BY r2.bucket_start DESC LIMIT 1)
                  END AS peer_hostname,
                  CASE WHEN c.is_local = 1 THEN 'Internal'
                       ELSE (SELECT r2.category FROM $table r2
                             WHERE r2.peer_ip = c.ip AND r2.bucket_start >= :cutoff
                               AND r2.category IS NOT NULL
                             ORDER BY r2.bucket_start DESC LIMIT 1)
                  END AS category,
                  SUM(c.bytes_in) AS bytes_in, SUM(c.bytes_out) AS bytes_out,
                  SUM(c.conn_count) AS conn_count,
                  COUNT(DISTINCT c.other_ip) AS unique_local_hosts
                FROM (
                  SELECT peer_ip AS ip, local_ip AS other_ip, bytes_in, bytes_out, conn_count,
                         peer_is_local AS is_local
                  FROM $table WHERE bucket_start >= :cutoff
            ";
            if ($localHost !== '') {
                $sql .= ' AND local_ip = :local_ip';
            }
            $sql .= "
                  UNION ALL
                  SELECT local_ip AS ip, peer_ip AS other_ip, bytes_out AS bytes_in, bytes_in AS bytes_out,
                         conn_count, 1 AS is_local
                  FROM $table WHERE bucket_start >= :cutoff AND peer_is_local = 1
            ";
            if ($localHost !== '') {
                $sql .= ' AND peer_ip = :local_ip';
            }
            $sql .= "
                ) c
                GROUP BY c.ip, c.is_local
            ";

            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            if ($localHost !== '') {
                $stmt->bindValue(':local_ip', $localHost, SQLITE3_TEXT);
            }
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['peer_ip'];
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

    public function categoryAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $cutoff = time() - $days * 86400;
        $table = $this->rollupTableForDays($days);

        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            // Uncategorized isn't dropped -- it's the honest majority
            // bucket until more of CATEGORY_SOURCES's upstream lists are
            // resolved for a given host, and hiding it would make the
            // total look smaller than what Top Talkers reports. 'Internal'
            // needs no special handling here -- it's just another non-NULL
            // category string, so it becomes its own bucket for free.
            $sql = "
                SELECT
                  COALESCE(r.category, :uncategorized) AS category,
                  SUM(r.bytes_in) AS bytes_in, SUM(r.bytes_out) AS bytes_out,
                  SUM(r.conn_count) AS conn_count,
                  COUNT(DISTINCT r.peer_ip) AS unique_peer_hosts
                FROM $table r
                WHERE r.bucket_start >= :cutoff
                GROUP BY category
            ";
            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            $stmt->bindValue(':uncategorized', 'Uncategorized', SQLITE3_TEXT);
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['category'];
                $row['bytes_total'] = (int)$row['bytes_in'] + (int)$row['bytes_out'];
                $records[] = $row;
            }
        }

        return $this->searchRecordsetBase($records, null, 'bytes_total');
    }

    public function uncategorizedAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $cutoff = time() - $days * 86400;
        $table = $this->rollupTableForDays($days);

        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            // A hostname's category can vary across older/newer buckets
            // (e.g. it predates a categories.py fix) -- HAVING checks
            // only the *most recent* bucket's category, same correlated-
            // subquery pattern as peer_hostname/category elsewhere, so an
            // already-fixed hostname doesn't linger on this list just
            // because some of its older buckets are still uncategorized.
            // peer_is_local=1 rows never appear here at all -- their
            // peer_hostname is always NULL (never resolved via DNS/SNI),
            // so the WHERE clause below already excludes them without
            // needing to also check the category sentinel.
            $sql = "
                SELECT
                  r1.peer_hostname,
                  SUM(r1.bytes_in) AS bytes_in, SUM(r1.bytes_out) AS bytes_out,
                  SUM(r1.conn_count) AS conn_count
                FROM $table r1
                WHERE r1.bucket_start >= :cutoff AND r1.peer_hostname IS NOT NULL
                GROUP BY r1.peer_hostname
                HAVING (
                  SELECT r2.category FROM $table r2
                  WHERE r2.peer_hostname = r1.peer_hostname AND r2.bucket_start >= :cutoff
                  ORDER BY r2.bucket_start DESC LIMIT 1
                ) IS NULL
            ";
            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = $row['peer_hostname'];
                $row['bytes_total'] = (int)$row['bytes_in'] + (int)$row['bytes_out'];
                $records[] = $row;
            }
        }

        return $this->searchRecordsetBase($records, null, 'bytes_total');
    }
}
