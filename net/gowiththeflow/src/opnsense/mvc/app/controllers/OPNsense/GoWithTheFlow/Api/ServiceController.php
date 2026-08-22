<?php

namespace OPNsense\GoWithTheFlow\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;

class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceClass = '\OPNsense\GoWithTheFlow\GoWithTheFlow';
    protected static $internalServiceTemplate = 'OPNsense/GoWithTheFlow';
    protected static $internalServiceEnabled = 'general.enabled';
    protected static $internalServiceName = 'gowiththeflow';
}
