<?php

namespace OPNsense\GowiththeFlow;

class HistoryController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GowiththeFlow/history');
    }
}
