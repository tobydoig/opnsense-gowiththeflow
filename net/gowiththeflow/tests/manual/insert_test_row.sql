INSERT INTO live_sessions (proto, local_ip, local_port, remote_ip, remote_port, remote_hostname, hostname_source, first_seen, last_seen, bytes_in, bytes_out, pkts_in, pkts_out)
VALUES ('tcp', '10.0.0.9', 52069, '172.66.147.243', 443, 'example.com', 'dns', strftime('%s','now')-90, strftime('%s','now'), 45000, 3200, 40, 22);

INSERT INTO local_host_identity (mac, ip, hostname, source, updated_at)
VALUES ('0a:00:27:00:00:0a', '10.0.0.9', 'dev-laptop', 'arp', strftime('%s','now'))
ON CONFLICT(mac) DO UPDATE SET hostname=excluded.hostname, updated_at=excluded.updated_at;
