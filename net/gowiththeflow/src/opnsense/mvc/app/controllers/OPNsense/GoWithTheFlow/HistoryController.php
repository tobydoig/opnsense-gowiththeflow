<?php

namespace OPNsense\GoWithTheFlow;

class HistoryController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GoWithTheFlow/history');
    }
}
