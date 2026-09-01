<?php

namespace OPNsense\GoWithTheFlow\Api;

class DnsqueriesController extends DbApiControllerBase
{
    // Unlike every sibling controller's backing table (all GROUP BY/SUM
    // aggregates bounded by distinct host/category count), dns_query_log
    // is a raw activity log with no such bound -- and nothing in this
    // codebase pushes pagination to SQL (searchRecordsetBase() always
    // operates on an already-fully-materialized PHP array). This hard
    // cap is what keeps a wide `days` selection on a busy network from
    // pulling an unbounded array into memory on every search.
    private const MAX_ROWS = 2000;

    public function searchAction()
    {
        $days = max(1, (int)($this->request->getPost('days') ?: 7));
        $localHost = $this->request->getPost('local_host') ?: '';
        $cutoff = time() - $days * 86400;

        $records = [];
        $localHosts = [];
        $db = $this->openDb();
        if ($db !== null) {
            // dns_query_log is stored hourly-bucketed (keeps write volume
            // down -- see its own schema comment), but showing one row
            // per bucket here looked like duplicates for anything queried
            // repeatedly across many hours (confirmed live: the same
            // host/query/type recurring every hour for a `days`-wide
            // window produced a visible run of near-identical rows).
            // Aggregating across every bucket in the window collapses
            // that back into one row per (host, query, type), with a
            // combined count and the most recent rcode/answers -- SQLite
            // specifically guarantees non-aggregated columns in a query
            // with MAX() come from the same row that produced the max
            // value, so `rcode`/`answers` here are genuinely the latest
            // ones, not an arbitrary bucket's.
            $sql = "
                SELECT
                  d.local_ip, d.query_name, d.query_type, d.rcode, d.answers,
                  SUM(d.count) AS count, MAX(d.last_seen) AS last_seen,
                  lhi.hostname AS local_hostname
                FROM dns_query_log d
                LEFT JOIN local_host_identity lhi ON lhi.ip = d.local_ip
                WHERE d.bucket_start >= :cutoff
            ";
            if ($localHost !== '') {
                $sql .= ' AND d.local_ip = :local_ip';
            }
            $sql .= '
                GROUP BY d.local_ip, d.query_name, d.query_type
                ORDER BY last_seen DESC LIMIT ' . self::MAX_ROWS;

            $stmt = $db->prepare($sql);
            $stmt->bindValue(':cutoff', $cutoff, SQLITE3_INTEGER);
            if ($localHost !== '') {
                $stmt->bindValue(':local_ip', $localHost, SQLITE3_TEXT);
            }
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $row['row_id'] = implode('-', [$row['local_ip'], $row['query_name'], $row['query_type']]);
                $row['local'] = $this->formatHost($row['local_hostname'], $row['local_ip']);
                $records[] = $row;
            }

            $lhResult = $db->query('SELECT DISTINCT ip, hostname FROM local_host_identity WHERE ip IS NOT NULL');
            while ($lhRow = $lhResult->fetchArray(SQLITE3_ASSOC)) {
                $localHosts[$lhRow['ip']] = $this->formatHost($lhRow['hostname'], $lhRow['ip']);
            }
        }

        $response = $this->searchRecordsetBase($records, null, 'last_seen');
        $response['local_hosts'] = $localHosts;
        return $response;
    }
}
