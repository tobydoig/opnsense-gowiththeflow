-- A repeated successful A lookup -- count > 1, as record_dns_query_event()
-- would produce for several identical queries within the same hour bucket.
INSERT INTO dns_query_log (bucket_start, local_ip, query_name, query_type, rcode, answers, count, first_seen, last_seen) VALUES
  (strftime('%s','now') - (strftime('%s','now') % 3600), '10.0.0.9', 'example.com', 'A', 'NOERROR', 'A:93.184.216.34', 14, strftime('%s','now') - 1800, strftime('%s','now') - 30);

-- A failed lookup -- exactly what plain hostname-cache DNS sniffing
-- throws away entirely, and the main reason this feature exists.
INSERT INTO dns_query_log (bucket_start, local_ip, query_name, query_type, rcode, answers, count, first_seen, last_seen) VALUES
  (strftime('%s','now') - (strftime('%s','now') % 3600), '10.0.0.42', 'nonexistent.example.com', 'A', 'NXDOMAIN', NULL, 1, strftime('%s','now') - 60, strftime('%s','now') - 60);

-- A CNAME chain -- both records in one row's `answers`, matching
-- extract_query_events()'s "type:value" formatting per answer.
INSERT INTO dns_query_log (bucket_start, local_ip, query_name, query_type, rcode, answers, count, first_seen, last_seen) VALUES
  (strftime('%s','now') - (strftime('%s','now') % 3600), '10.0.0.9', 'www.example.com', 'A', 'NOERROR', 'CNAME:example.com,A:93.184.216.34', 3, strftime('%s','now') - 900, strftime('%s','now') - 120);

INSERT INTO local_host_identity (mac, ip, hostname, source, updated_at) VALUES
  ('0a:00:27:00:00:0a', '10.0.0.9', 'dev-laptop', 'dhcp_lease', strftime('%s','now')),
  ('aa:bb:cc:dd:ee:42', '10.0.0.42', 'iot-camera', 'dhcp_lease', strftime('%s','now'))
ON CONFLICT(mac) DO UPDATE SET hostname=excluded.hostname, updated_at=excluded.updated_at;
