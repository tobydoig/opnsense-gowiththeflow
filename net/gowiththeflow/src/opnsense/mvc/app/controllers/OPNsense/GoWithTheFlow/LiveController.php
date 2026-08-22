<?php

namespace OPNsense\GowiththeFlow;

class LiveController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GowiththeFlow/live');
    }
}
