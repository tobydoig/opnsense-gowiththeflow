<?php
$db = new SQLite3('/tmp/test_flows.db', SQLITE3_OPEN_READONLY);
$result = $db->query('SELECT COUNT(*) as n FROM ip_hostname_cache');
$row = $result->fetchArray(SQLITE3_ASSOC);
print_r($row);

$result2 = $db->query('SELECT ip, hostname, source FROM ip_hostname_cache LIMIT 3');
while ($r = $result2->fetchArray(SQLITE3_ASSOC)) {
    print_r($r);
}
