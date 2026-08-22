<?php

namespace OPNsense\GowiththeFlow;

class ToptalkersController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GowiththeFlow/toptalkers');
    }
}
