<?php

namespace OPNsense\GoWithTheFlow\Api;

class LiveController extends DbApiControllerBase
{
    public function searchAction()
    {
        // Optional exact-match filters -- driven by the Overview chart's
        // click-through (Live's grid is ajax-backed, so a client-side
        // Tabulator setFilter() only ever filters whatever page of rows
        // already happens to be loaded locally; it doesn't request the
        // server for the full matching set the way this does, same
        // pattern History/Top Talkers already use for their own filters).
        $filterLocalIp = $this->request->getPost('local_ip') ?: '';
        $filterPeerIp = $this->request->getPost('peer_ip') ?: '';
        $filterPeerPort = $this->request->getPost('peer_port') ?: '';
        // Matches either side of a session -- for the Graph view's node
        // click, where a single IP can appear as local_ip in one session
        // and peer_ip in another (live_sessions is never canonicalized by
        // role for a peer_is_local pair, unlike the rollup tables).
        $filterHostIp = $this->request->getPost('host_ip') ?: '';

        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            // peer_hostname is only ever resolved (via correlator.py) for a
            // genuine internet peer -- when peer_is_local=1 (the peer is
            // also a local host), name it via the same local_host_identity
            // lookup local_ip already uses, rather than the stored (always
            // NULL in that case) column.
            $sql = 'SELECT ls.proto, ls.local_ip, ls.local_port, ls.peer_ip, ls.peer_port,
                        ls.peer_is_local, ls.hostname_source, ls.category, ls.dpi_protocol, ls.state,
                        ls.first_seen, ls.last_seen, ls.last_activity,
                        ls.bytes_in, ls.bytes_out, ls.pkts_in, ls.pkts_out,
                        lhi.hostname AS local_hostname,
                        CASE WHEN ls.peer_is_local = 1
                             THEN (SELECT hostname FROM local_host_identity WHERE ip = ls.peer_ip
                                   ORDER BY updated_at DESC LIMIT 1)
                             ELSE ls.peer_hostname
                        END AS peer_hostname
                 FROM live_sessions ls
                 LEFT JOIN local_host_identity lhi ON lhi.ip = ls.local_ip
                 WHERE 1=1';
            if ($filterLocalIp !== '') {
                $sql .= ' AND ls.local_ip = :local_ip';
            }
            if ($filterPeerIp !== '') {
                $sql .= ' AND ls.peer_ip = :peer_ip';
            }
            if ($filterPeerPort !== '') {
                $sql .= ' AND ls.peer_port = :peer_port';
            }
            if ($filterHostIp !== '') {
                $sql .= ' AND (ls.local_ip = :host_ip OR ls.peer_ip = :host_ip)';
            }

            $stmt = $db->prepare($sql);
            if ($filterLocalIp !== '') {
                $stmt->bindValue(':local_ip', $filterLocalIp, SQLITE3_TEXT);
            }
            if ($filterPeerIp !== '') {
                $stmt->bindValue(':peer_ip', $filterPeerIp, SQLITE3_TEXT);
            }
            if ($filterPeerPort !== '') {
                $stmt->bindValue(':peer_port', (int)$filterPeerPort, SQLITE3_INTEGER);
            }
            if ($filterHostIp !== '') {
                $stmt->bindValue(':host_ip', $filterHostIp, SQLITE3_TEXT);
            }
            $result = $stmt->execute();
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

    public function overviewAction()
    {
        // Deliberately NOT a slice of searchAction()'s data -- that's a
        // Bootgrid-paginated response (default page size 50), and
        // last_seen bumps on *every* poll for *every* still-open
        // session regardless of actual traffic, so which sessions land
        // on page 1 vs. page 2+ is essentially arbitrary once there are
        // more than one page's worth open at once. A real-box test
        // confirmed the failure mode directly: several rounds of a
        // phone running speedtest.net (large, genuinely dominant
        // transfers) never appeared on the Overview chart at all,
        // because their rows simply weren't on the page the table
        // happened to be showing -- even though the same traffic was
        // immediately obvious on OPNsense's own Reporting > Traffic
        // graph. This endpoint returns every currently open session,
        // unpaginated, with only the handful of fields the chart/graph
        // renderers actually read.
        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            $result = $db->query(
                'SELECT ls.proto, ls.local_ip, ls.local_port, ls.peer_ip, ls.peer_port,
                        ls.bytes_in, ls.bytes_out, ls.last_activity,
                        lhi.hostname AS local_hostname,
                        CASE WHEN ls.peer_is_local = 1
                             THEN (SELECT hostname FROM local_host_identity WHERE ip = ls.peer_ip
                                   ORDER BY updated_at DESC LIMIT 1)
                             ELSE ls.peer_hostname
                        END AS peer_hostname
                 FROM live_sessions ls
                 LEFT JOIN local_host_identity lhi ON lhi.ip = ls.local_ip'
            );
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
                $records[] = $row;
            }
        }

        return ['rows' => $records];
    }

    /**
     * Serves the Live Overview chart's per-tick throughput history --
     * computed once, server-side, by live_ticks.compute_tick_deltas()
     * (gowiththeflowd.py) into the live_ticks table, rather than each
     * browser tab independently diffing its own poll of overviewAction()
     * (the old design -- see DESIGN.md for why that meant N open tabs
     * each redundantly re-derived the same numbers, and a reconnecting
     * tab had no real history to recover after being backgrounded).
     *
     * `since` is a tick_time watermark -- 0 (or omitted) returns
     * everything currently retained (live_ticks is itself continuously
     * pruned to a short rolling window, so no additional cap is needed
     * here), a real prior value returns only the new ticks since then.
     */
    public function seriesAction()
    {
        $since = (int)($this->request->getPost('since') ?: 0);
        $records = [];
        $db = $this->openDb();
        if ($db !== null) {
            $stmt = $db->prepare(
                'SELECT tick_time, local_ip, peer_port, delta_bytes_in, delta_bytes_out
                 FROM live_ticks WHERE tick_time > :since ORDER BY tick_time'
            );
            $stmt->bindValue(':since', $since, SQLITE3_INTEGER);
            $result = $stmt->execute();
            while ($row = $result->fetchArray(SQLITE3_ASSOC)) {
                $records[] = $row;
            }
        }

        return ['ticks' => $records];
    }
}
