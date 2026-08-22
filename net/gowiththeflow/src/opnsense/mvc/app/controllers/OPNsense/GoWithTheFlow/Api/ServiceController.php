<?php

namespace OPNsense\GowiththeFlow\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;

class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceClass = '\OPNsense\GowiththeFlow\GowiththeFlow';
    protected static $internalServiceEnabled = 'general.enabled';
    protected static $internalServiceName = 'gowiththeflow';
}
