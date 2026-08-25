<?php

namespace OPNsense\GoWithTheFlow;

class LiveController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/GoWithTheFlow/live');
    }

    /**
     * ControllerBase::afterExecuteRoute() unconditionally runs
     * $this->view->start()/processRender()/finish() and then overwrites
     * $this->response's content with whatever that produced -- Phalcon's
     * View::processRender() itself does honor disable(), returning
     * without rendering, but the base class calls
     * $this->response->setContent($this->view->getContent(), true)
     * regardless, clobbering overviewWorkerAction()'s JS with the View's
     * (empty) content. A real request confirmed this directly: a 200
     * with the right Content-Type but Content-Length: 0. Skipping the
     * parent hook for this one action is simpler and less fragile than
     * fighting Phalcon's View internals to make disable() suppress that
     * final setContent() call too.
     */
    public function afterExecuteRoute(\OPNsense\Mvc\Dispatcher $dispatcher)
    {
        if ($dispatcher->getActionName() === 'overviewWorker') {
            return;
        }
        parent::afterExecuteRoute($dispatcher);
    }

    /**
     * Serves the Overview chart's poll loop as a same-origin Worker script
     * (rather than a blob: URL built client-side) because this install's
     * CSP is `script-src 'self' 'unsafe-inline' 'unsafe-eval'` -- no
     * `blob:` -- so a blob-constructed Worker is silently blocked by the
     * browser. A real 'self' URL sidesteps that with no CSP change (which
     * would be a core, not plugin, concern anyway). Running the poll loop
     * in a Worker rather than the page's own setTimeout chain is the
     * actual point: a backgrounded browser tab gets its own timers
     * throttled (sometimes to a full stop) by the browser, but a dedicated
     * Worker isn't tied to a document's visibility state, so it keeps
     * polling on schedule the whole time the tab is hidden -- avoiding the
     * gap in the Live chart a throttled main-thread timer produces.
     *
     * Each tick fetches BOTH /live/overview (current session snapshot --
     * Table/Graph freshness) and /live/series (the chart's own
     * server-computed tick history, incremental via its own `since`
     * watermark owned entirely here in the worker) and posts one combined
     * message back. The chart no longer needs this Worker for background-
     * tab immunity the way it used to (a reconnecting tab now just fetches
     * the real missed ticks from live_ticks instead of approximating a
     * gap) -- but keeping both fetches on the same tick keeps one poll
     * loop instead of two, and still keeps the Graph/Table views live
     * while backgrounded too.
     */
    public function overviewWorkerAction()
    {
        $csrf_token = $this->session->get('$PHALCON/CSRF$');
        $csrf_tokenKey = $this->session->get('$PHALCON/CSRF/KEY$');
        if (empty($csrf_token) || empty($csrf_tokenKey)) {
            $csrf_token = $this->security->getToken();
            $csrf_tokenKey = $this->security->getTokenKey();
        }

        $js = <<<'JS'
(function () {
    'use strict';
    var CSRF_TOKEN = %s;
    var intervalMs = 2000;
    var timerId = null;
    var fetchInFlight = false;
    var seriesSince = 0;

    function scheduleNext() {
        if (timerId !== null) {
            clearTimeout(timerId);
            timerId = null;
        }
        if (intervalMs > 0) {
            timerId = setTimeout(tick, intervalMs);
        }
    }

    function postJson(url, body) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': CSRF_TOKEN,
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: body || ''
        }).then(function (r) { return r.json(); });
    }

    function tick() {
        fetchInFlight = true;
        Promise.all([
            postJson('/api/gowiththeflow/live/overview'),
            postJson('/api/gowiththeflow/live/series', 'since=' + seriesSince)
        ]).then(function (results) {
            var overview = results[0], series = results[1];
            var ticks = series.ticks || [];
            for (var i = 0; i < ticks.length; i++) {
                if (ticks[i].tick_time > seriesSince) {
                    seriesSince = ticks[i].tick_time;
                }
            }
            postMessage({ type: 'poll', rows: overview.rows || [], ticks: ticks });
        }).catch(function () {
            // Transient network/API error -- next scheduled tick retries
            // naturally, same as the old main-thread poller did.
        }).then(function () {
            fetchInFlight = false;
            scheduleNext();
        });
    }

    // A setInterval message that lands while a fetch is already in
    // flight must NOT call scheduleNext() itself -- tick()'s own
    // completion handler will do that with the just-updated intervalMs,
    // and calling it here too would leave two live timers running.
    self.onmessage = function (e) {
        var msg = e.data || {};
        if (msg.type === 'setInterval') {
            intervalMs = msg.intervalMs;
            if (!fetchInFlight) {
                scheduleNext();
            }
        } else if (msg.type === 'resetSeries') {
            // The chart's own grouping changed -- force the next series
            // fetch to return everything currently retained again, so
            // switching grouping repopulates full history immediately
            // instead of rebuilding tick by tick.
            seriesSince = 0;
        }
    };

    tick();
})();
JS;
        $js = sprintf($js, json_encode((string)$csrf_token));

        // This OPNsense version's own lightweight MVC dispatcher (not
        // Phalcon) types an action's return as array|string|null and
        // sends whatever's already set on $this->response regardless --
        // returning the Response object itself is a TypeError.
        $this->view->disable();
        $this->response->setContentType('application/javascript', 'UTF-8');
        $this->response->setContent($js);
        return null;
    }
}
