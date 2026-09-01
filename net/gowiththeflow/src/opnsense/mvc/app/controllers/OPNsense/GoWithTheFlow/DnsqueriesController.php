<?php

namespace OPNsense\GoWithTheFlow;

class DnsqueriesController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GoWithTheFlow/dnsqueries');
    }
}
