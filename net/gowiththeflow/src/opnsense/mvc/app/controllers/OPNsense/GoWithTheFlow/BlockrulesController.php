<?php

namespace OPNsense\GoWithTheFlow;

class BlockrulesController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GoWithTheFlow/blockrules');
    }
}
