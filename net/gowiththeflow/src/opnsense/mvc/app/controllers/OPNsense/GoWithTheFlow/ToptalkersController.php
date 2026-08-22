<?php

namespace OPNsense\GoWithTheFlow;

class ToptalkersController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GoWithTheFlow/toptalkers');
    }
}
