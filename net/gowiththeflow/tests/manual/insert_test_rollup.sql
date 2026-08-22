-- Two hourly buckets for the same (local, remote) pair -- should SUM together in History.
INSERT INTO rollup_hourly (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
VALUES (strftime('%s','now') - 7200, 'tcp', '10.0.0.9', '172.66.147.243', 'example.com', 'dns', 40000, 3000, 30, 20, 3);
INSERT INTO rollup_hourly (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
VALUES (strftime('%s','now') - 3600, 'tcp', '10.0.0.9', '172.66.147.243', 'example.com', 'dns', 15000, 1200, 10, 8, 2);

-- A different remote for the same local host.
INSERT INTO rollup_hourly (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
VALUES (strftime('%s','now') - 3600, 'tcp', '10.0.0.9', '142.251.154.4', 'www.youtube.com', 'dns', 900000, 40000, 500, 300, 5);

-- A second local host entirely.
INSERT INTO rollup_hourly (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
VALUES (strftime('%s','now') - 1800, 'tcp', '10.0.0.42', '20.190.159.4', 'login.microsoftonline.com', 'dns', 5000, 2000, 4, 3, 1);

INSERT INTO local_host_identity (mac, ip, hostname, source, updated_at) VALUES
  ('0a:00:27:00:00:0a', '10.0.0.9', 'dev-laptop', 'dhcp_lease', strftime('%s','now')),
  ('aa:bb:cc:dd:ee:42', '10.0.0.42', 'iot-camera', 'dhcp_lease', strftime('%s','now'))
ON CONFLICT(mac) DO UPDATE SET hostname=excluded.hostname, updated_at=excluded.updated_at;
