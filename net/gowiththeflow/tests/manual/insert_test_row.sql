INSERT INTO live_sessions (proto, local_ip, local_port, peer_ip, peer_port, peer_is_local, peer_hostname, hostname_source, dpi_protocol, state, first_seen, last_seen, last_activity, bytes_in, bytes_out, pkts_in, pkts_out)
VALUES ('tcp', '10.0.0.9', 52069, '172.66.147.243', 443, 0, 'example.com', 'dns', 'TLS', 'ESTABLISHED', strftime('%s','now')-90, strftime('%s','now'), strftime('%s','now'), 45000, 3200, 40, 22);

-- Internal (local<->local) session -- neither side ever gets a DNS/SNI
-- hostname; both are named via local_host_identity at query time instead.
INSERT INTO live_sessions (proto, local_ip, local_port, peer_ip, peer_port, peer_is_local, category, state, first_seen, last_seen, last_activity, bytes_in, bytes_out, pkts_in, pkts_out)
VALUES ('tcp', '10.0.0.9', 52101, '10.0.0.20', 554, 1, 'Internal', 'ESTABLISHED', strftime('%s','now')-60, strftime('%s','now'), strftime('%s','now')-60, 12000, 800, 15, 9);

INSERT INTO local_host_identity (mac, ip, hostname, source, updated_at) VALUES
  ('0a:00:27:00:00:0a', '10.0.0.9', 'dev-laptop', 'arp', strftime('%s','now')),
  ('bb:cc:dd:ee:ff:20', '10.0.0.20', 'nvr', 'arp', strftime('%s','now'))
ON CONFLICT(mac) DO UPDATE SET hostname=excluded.hostname, updated_at=excluded.updated_at;
