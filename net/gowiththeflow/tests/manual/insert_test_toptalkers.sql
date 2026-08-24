INSERT INTO rollup_hourly (bucket_start, proto, local_ip, peer_ip, peer_is_local, peer_hostname, hostname_source, bytes_in, bytes_out, pkts_in, pkts_out, conn_count) VALUES
  (strftime('%s','now') - 3600, 'tcp', '10.0.0.9', '172.66.147.243', 0, 'example.com', 'dns', 40000, 3000, 30, 20, 3),
  (strftime('%s','now') - 3600, 'tcp', '10.0.0.9', '142.251.154.4', 0, 'www.youtube.com', 'dns', 900000, 40000, 500, 300, 5),
  (strftime('%s','now') - 1800, 'tcp', '10.0.0.42', '20.190.159.4', 0, 'login.microsoftonline.com', 'dns', 5000, 2000, 4, 3, 1),
  (strftime('%s','now') - 1800, 'tcp', '10.0.0.42', '142.251.154.4', 0, 'www.youtube.com', 'dns', 20000, 1000, 15, 10, 2);

-- An internal (local<->local) pair, canonicalized local_ip < peer_ip
-- numerically -- 10.0.0.9 (dev-laptop) is the smaller member, 10.0.0.20
-- (nvr) is the numerically larger member. Before the UNION ALL fix, nvr
-- (the low-static-IP device that's the whole motivating case) would never
-- show up under Top Peers at all, since it's never the peer_ip side of
-- any of its own internal-pair rows.
INSERT INTO rollup_hourly (bucket_start, proto, local_ip, peer_ip, peer_is_local, category, bytes_in, bytes_out, pkts_in, pkts_out, conn_count) VALUES
  (strftime('%s','now') - 3600, 'tcp', '10.0.0.9', '10.0.0.20', 1, 'Internal', 12000, 800, 15, 9, 2);

INSERT INTO local_host_identity (mac, ip, hostname, source, updated_at) VALUES
  ('0a:00:27:00:00:0a', '10.0.0.9', 'dev-laptop', 'dhcp_lease', strftime('%s','now')),
  ('aa:bb:cc:dd:ee:42', '10.0.0.42', 'iot-camera', 'dhcp_lease', strftime('%s','now')),
  ('bb:cc:dd:ee:ff:20', '10.0.0.20', 'nvr', 'dhcp_lease', strftime('%s','now'))
ON CONFLICT(mac) DO UPDATE SET hostname=excluded.hostname, updated_at=excluded.updated_at;
