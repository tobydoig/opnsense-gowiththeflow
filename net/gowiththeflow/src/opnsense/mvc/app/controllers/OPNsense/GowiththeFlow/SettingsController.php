<?php

namespace OPNsense\GowiththeFlow;

class SettingsController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->generalForm = $this->getForm('general');
        $this->view->pick('OPNsense/GowiththeFlow/settings');
    }
}
