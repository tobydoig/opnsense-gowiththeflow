<?php

namespace OPNsense\GoWithTheFlow;

class LiveController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GoWithTheFlow/live');
    }
}
