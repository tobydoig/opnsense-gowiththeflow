<?php

namespace OPNsense\GoWithTheFlow\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;

class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceClass = '\OPNsense\GoWithTheFlow\GoWithTheFlow';
    protected static $internalServiceTemplate = 'OPNsense/GoWithTheFlow';
    protected static $internalServiceEnabled = 'general.enabled';
    protected static $internalServiceName = 'gowiththeflow';

    /**
     * ApiMutableServiceControllerBase::invokeFirewallReload() defaults
     * to false -- reconfigureAction() never calls `filter reload`
     * unless a subclass opts in here. Never mattered for the original
     * block-a-host feature (its table/rules are always registered
     * unconditionally, only their *contents* change, via block_host.py's
     * own direct sync_pf() + configctl filter reload calls). It matters
     * now: the reservation gate's rules are only registered at all when
     * enableReservationGate is on, so flipping that toggle and hitting
     * Save/Apply must actually reload the filter for it to take
     * effect -- confirmed live this was missing (the toggle saved
     * correctly to config.xml, but the compiled ruleset didn't change
     * until a manual `configctl filter reload`).
     */
    protected function invokeFirewallReload()
    {
        return true;
    }
}
