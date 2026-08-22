#!/usr/local/bin/php
<?php
require_once('script/load_phalcon.php');

$model = new \OPNsense\GowiththeFlow\GowiththeFlow();

echo "enabled default: " . (string)$model->general->enabled . "\n";
echo "rawRetentionDays default: " . (string)$model->general->rawRetentionDays . "\n";
echo "rollupHourlyRetentionDays default: " . (string)$model->general->rollupHourlyRetentionDays . "\n";
echo "cpuLimitPct default: " . (string)$model->general->cpuLimitPct . "\n";
echo "enableDnsSniffing default: " . (string)$model->hostname->enableDnsSniffing . "\n";
echo "enableSniSniffing default: " . (string)$model->hostname->enableSniSniffing . "\n";
echo "model loaded OK\n";
