<?php

namespace OPNsense\GoWithTheFlow;

class SettingsController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->generalForm = $this->getForm('general');
        $this->view->pick('OPNsense/GoWithTheFlow/settings');
    }
}
