<?php

namespace OPNsense\GoWithTheFlow;

class InternalController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GoWithTheFlow/internal');
    }
}
