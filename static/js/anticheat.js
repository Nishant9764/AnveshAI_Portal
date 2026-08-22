/**
 * anticheat.js
 * ─────────────
 * Shared lockdown module used on every test screen (preflight
 * calibration, all 3 sections — Skills MCQ / Experience & Projects /
 * Role Fit — identical behavior on all of them since they all extend
 * test_base.html and load this one file).
 *
 * Usage on a page:
 *   AntiCheat.initLockdown(sessionId, { onTerminate: (reason) => ... });
 *   AntiCheat.attachKeystrokeTracking(textareaEl);
 *   ... before navigating to the next question (both the manual submit
 *   AND the timer-triggered auto-submit): AntiCheat.markLegitimateNavigation();
 *   ... on submit: AntiCheat.getKeystrokeMetrics(textareaEl)
 *
 * ── Architecture ──────────────────────────────────────────────────
 * This app is a classic server-rendered multi-page app: every question
 * is POST -> redirect -> a brand new page. That is NOT changing. The
 * problem this module has to solve is that a normal in-app "Continue"
 * navigation can itself cause the browser to (a) drop full-screen and
 * (b) briefly report the document as hidden — neither of which is the
 * candidate doing anything wrong. Two mechanisms make the anti-cheat
 * layer context-aware without touching the app's routing at all:
 *
 *   1. sessionStorage-bridged "legitimate navigation" flag. Right
 *      before an intentional navigation (Continue click OR the timer's
 *      forced submit), markLegitimateNavigation() stamps a timestamp
 *      into sessionStorage — which, unlike any in-memory JS state,
 *      survives the full page reload. The next page's initLockdown()
 *      reads it once and, for a short grace window right after load,
 *      treats any fullscreen/visibility blips as expected side effects
 *      of navigation rather than violations.
 *
 *   2. The 5-second exit rule is now fully separated from the strike
 *      system. Leaving full-screen or switching tabs is NOT itself
 *      logged as a violation — it only starts a 5-second grace period.
 *      Returning in time produces zero warnings and zero backend
 *      penalty. ONLY failing to return within 5 seconds sends anything
 *      to the server, and that one event ends the session immediately
 *      — no strike-counting ambiguity, no double-counting one physical
 *      exit as two separate violations.
 *
 * Soft violations (copy/paste/right-click/devtools/blocked shortcuts)
 * are a completely separate, lower-severity ladder: they still
 * accumulate toward a 3-warning limit, same as before.
 */
(function (global) {
  const AntiCheat = {};
  const SCREEN_EXIT_LIMIT_MS = 5000;
  const NAV_GRACE_MS = 2500; // how long after marking a nav we treat blips as benign
  const NAV_FLAG_KEY = "ac_legit_nav_at";

  // ---------------------------------------------------------------
  // Legitimate-navigation bridge (survives the page reload)
  // ---------------------------------------------------------------
  AntiCheat.markLegitimateNavigation = function () {
    try {
      sessionStorage.setItem(NAV_FLAG_KEY, String(Date.now()));
    } catch (e) {
      /* sessionStorage unavailable (private mode edge cases) — degrade
         gracefully; worst case a rare false-positive grace period. */
    }
  };

  function consumeLegitimateNavigationFlag() {
    let ts = null;
    try {
      ts = sessionStorage.getItem(NAV_FLAG_KEY);
      sessionStorage.removeItem(NAV_FLAG_KEY);
    } catch (e) {
      /* ignore */
    }
    if (!ts) return false;
    return Date.now() - Number(ts) < NAV_GRACE_MS;
  }

  // ---------------------------------------------------------------
  // Visuals: countdown overlay + violation banner, injected once
  // ---------------------------------------------------------------
  function injectStyles() {
    if (document.getElementById("ac-styles")) return;
    const style = document.createElement("style");
    style.id = "ac-styles";
    style.textContent = `
      .ac-exit-overlay {
        position: fixed; inset: 0; z-index: 99999;
        background: rgba(15, 17, 32, 0.82);
        backdrop-filter: blur(6px);
        display: none; align-items: center; justify-content: center;
        font-family: "Inter", -apple-system, sans-serif;
        animation: acFadeIn 0.2s ease;
      }
      .ac-exit-overlay.active { display: flex; }
      @keyframes acFadeIn { from { opacity: 0; } to { opacity: 1; } }
      .ac-exit-card {
        background: #ffffff; border-radius: 16px; padding: 36px 40px;
        text-align: center; max-width: 360px; box-shadow: 0 20px 60px rgba(0,0,0,0.35);
        animation: acPop 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
      }
      @keyframes acPop { from { transform: scale(0.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
      .ac-ring-wrap { position: relative; width: 108px; height: 108px; margin: 0 auto 18px; }
      .ac-ring { transform: rotate(-90deg); width: 108px; height: 108px; }
      .ac-ring-bg { fill: none; stroke: #f0f1f6; stroke-width: 8; }
      .ac-ring-progress {
        fill: none; stroke: #d1435b; stroke-width: 8; stroke-linecap: round;
        stroke-dasharray: 327; stroke-dashoffset: 0;
      }
      .ac-ring-number {
        position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
        font-size: 30px; font-weight: 700; color: #d1435b; font-variant-numeric: tabular-nums;
      }
      .ac-exit-title { font-size: 17px; font-weight: 700; color: #171a2b; margin-bottom: 8px; }
      .ac-exit-msg { font-size: 13.5px; color: #6b7280; line-height: 1.6; margin-bottom: 20px; }
      .ac-return-btn {
        background: #4338ca; color: #fff; border: none; border-radius: 9px;
        padding: 11px 22px; font-size: 13.5px; font-weight: 600; cursor: pointer;
        font-family: inherit; transition: background 0.15s;
      }
      .ac-return-btn:hover { background: #362da3; }
      .ac-exit-overlay.benign .ac-ring-progress { stroke: #4338ca; }
      .ac-exit-overlay.benign .ac-ring-number { color: #4338ca; }

      .ac-warning-banner {
        position: fixed; top: 0; left: 0; right: 0; z-index: 99998;
        background: #fff7e6; color: #b45309; border-bottom: 1px solid #f3d9a4;
        text-align: center; padding: 10px 16px; font-family: "Inter", sans-serif;
        font-size: 13px; font-weight: 500; animation: acSlideDown 0.2s ease;
      }
      @keyframes acSlideDown { from { transform: translateY(-100%); } to { transform: translateY(0); } }

      .ac-locked-select { user-select: none; -webkit-user-select: none; }
      .ac-locked-select textarea, .ac-locked-select input {
        user-select: text; -webkit-user-select: text;
      }
    `;
    document.head.appendChild(style);
  }

  function buildOverlay() {
    if (document.getElementById("ac-exit-overlay")) return;
    const overlay = document.createElement("div");
    overlay.id = "ac-exit-overlay";
    overlay.className = "ac-exit-overlay";
    overlay.innerHTML = `
      <div class="ac-exit-card">
        <div class="ac-ring-wrap">
          <svg class="ac-ring" viewBox="0 0 116 116">
            <circle class="ac-ring-bg" cx="58" cy="58" r="52"></circle>
            <circle class="ac-ring-progress" id="ac-ring-progress" cx="58" cy="58" r="52"></circle>
          </svg>
          <div class="ac-ring-number" id="ac-ring-number">5</div>
        </div>
        <div class="ac-exit-title" id="ac-exit-title">You've left the assessment</div>
        <div class="ac-exit-msg" id="ac-exit-msg">Return now or this session will end automatically.</div>
        <button class="ac-return-btn" id="ac-return-btn" type="button">Return to Assessment</button>
      </div>
    `;
    document.body.appendChild(overlay);
  }

  // ---------------------------------------------------------------
  // Main lockdown
  // ---------------------------------------------------------------
  AntiCheat.initLockdown = function (sessionId, opts) {
    opts = opts || {};
    injectStyles();
    buildOverlay();
    document.documentElement.classList.add("ac-locked-select");

    const overlay = document.getElementById("ac-exit-overlay");
    const ringProgress = document.getElementById("ac-ring-progress");
    const ringNumber = document.getElementById("ac-ring-number");
    const returnBtn = document.getElementById("ac-return-btn");
    const titleEl = document.getElementById("ac-exit-title");
    const msgEl = document.getElementById("ac-exit-msg");
    const CIRCUMFERENCE = 2 * Math.PI * 52;

    // Was this page reached via our own intentional Continue/auto-submit?
    // If so, blips in the next ~2.5s are expected side effects of
    // navigation, not violations.
    const arrivedViaLegitimateNav = consumeLegitimateNavigationFlag();
    const suppressUntil = arrivedViaLegitimateNav
      ? Date.now() + NAV_GRACE_MS
      : 0;
    function inSuppressionWindow() {
      return Date.now() < suppressUntil;
    }

    let exitTimerActive = false;
    let leftAt = null;
    let pollHandle = null;
    // A normal in-app "Continue" click submits a form -> full page reload.
    // Several browsers drop full-screen mode across that kind of
    // navigation even though nothing suspicious happened. So we only
    // treat losing full-screen as a genuine violation once we've
    // confirmed the candidate WAS actually in full-screen on THIS page —
    // not simply because a fresh page hasn't (re-)entered it yet.
    let fullscreenEstablishedOnThisPage = !!document.fullscreenElement;

    const beacon = (eventType, detail) => {
      return fetch(`/api/test/${sessionId}/integrity-event`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type: eventType, detail: detail || "" }),
        keepalive: true,
      })
        .then((r) => r.json())
        .catch(() => null);
    };

    function isOutOfScreen() {
      return document.hidden || !document.fullscreenElement;
    }

    function showOverlay(benign) {
      overlay.classList.toggle("benign", !!benign);
      if (benign) {
        titleEl.textContent = "Please continue in full-screen";
        msgEl.textContent = "This assessment requires full-screen mode.";
      } else {
        titleEl.textContent = "You've left the assessment";
        msgEl.textContent =
          "Return now or this session will end automatically.";
      }
      overlay.classList.add("active");
    }
    function hideOverlay() {
      overlay.classList.remove("active");
      ringProgress.style.transition = "none";
      ringProgress.style.strokeDashoffset = "0";
    }

    function handleBeaconResult(data) {
      if (!data) return;
      if (data.warnings_count != null) {
        AntiCheat._showWarning(data.warnings_count, opts.maxWarnings || 3);
      }
      if (data.should_terminate && data.reason !== "screen_exit_timeout") {
        // screen_exit_timeout handles its own termination flow directly
        // (see startExitTimer's poll loop); this covers the separate
        // 3-strike path for repeated soft violations only.
        clearExitTimer(true);
        if (opts.onTerminate) opts.onTerminate(data.reason || "warnings");
      }
    }

    // Non-punitive: just needs a click (a required user gesture) to
    // re-enter full-screen. No beacon, no countdown, no strike — this is
    // the expected state right after a normal question-to-question nav
    // in browsers that drop full-screen across a full page load.
    function promptBenignReentry() {
      if (exitTimerActive) return; // a real grace period is already showing
      showOverlay(true);
      ringProgress.style.transition = "none";
      ringProgress.style.strokeDashoffset = String(CIRCUMFERENCE);
      ringNumber.textContent = "";
    }

    // The 5-second grace period. IMPORTANT: this does NOT send anything
    // to the backend when it starts — only a return-in-time (no call at
    // all) or a timeout (exactly one screen_exit_timeout call) ever talk
    // to the server. That's what makes it impossible for one physical
    // exit to register as two violations, and impossible for a quick,
    // recovered blip to count against the candidate at all.
    function startExitTimer() {
      if (exitTimerActive) return;
      exitTimerActive = true;
      leftAt = Date.now();
      showOverlay(false);
      ringNumber.textContent = "5";

      ringProgress.style.transition = "none";
      ringProgress.style.strokeDashoffset = "0";
      requestAnimationFrame(() => {
        ringProgress.style.transition = `stroke-dashoffset ${SCREEN_EXIT_LIMIT_MS}ms linear`;
        ringProgress.style.strokeDashoffset = String(CIRCUMFERENCE);
      });

      // Cosmetic 1s tick for the number; the real pass/fail check below is
      // timestamp-based so background-tab timer throttling can't cause a
      // false pass.
      const tickHandle = setInterval(() => {
        const elapsed = Date.now() - leftAt;
        const remaining = Math.max(
          0,
          Math.ceil((SCREEN_EXIT_LIMIT_MS - elapsed) / 1000)
        );
        ringNumber.textContent = String(remaining);
      }, 250);

      pollHandle = setInterval(() => {
        if (!isOutOfScreen()) {
          clearInterval(tickHandle);
          // Returned in time: zero backend calls, zero strikes — exactly
          // as if nothing happened.
          clearExitTimer();
          return;
        }
        if (Date.now() - leftAt >= SCREEN_EXIT_LIMIT_MS) {
          clearInterval(tickHandle);
          clearExitTimer(true);
          beacon(
            "screen_exit_timeout",
            "Did not return within 5 seconds"
          ).finally(() => {
            if (opts.onTerminate) opts.onTerminate("screen_exit_timeout");
          });
        }
      }, 200);

      overlay._acTickHandle = tickHandle;
    }

    function clearExitTimer(skipHide) {
      exitTimerActive = false;
      leftAt = null;
      if (pollHandle) {
        clearInterval(pollHandle);
        pollHandle = null;
      }
      if (overlay._acTickHandle) {
        clearInterval(overlay._acTickHandle);
        overlay._acTickHandle = null;
      }
      if (!skipHide) hideOverlay();
    }

    returnBtn.addEventListener("click", function () {
      AntiCheat.requestFullscreen();
    });

    // Full-screen genuinely does not survive a full page reload in most
    // browsers — that's a platform security rule, no amount of JS on our
    // side can force it to persist. What we CAN do: try a silent,
    // no-UI re-entry attempt immediately on load. Browsers occasionally
    // honor this if "user activation" carried over from the click that
    // triggered this exact navigation; when they don't, the promise just
    // rejects and nothing visible happens. Either way, we do NOT show any
    // prompt yet — we only show one if, after giving that attempt (and,
    // if this page was reached via markLegitimateNavigation, the full
    // grace window) a fair chance, we're still genuinely not in
    // full-screen. That's the difference between "the browser dropped
    // full-screen because we just navigated" (silent, no prompt) and
    // "we asked and it's still not full-screen" (one polite prompt).
    if (!fullscreenEstablishedOnThisPage) {
      try {
        const p = AntiCheat.requestFullscreen();
        if (p && typeof p.catch === "function") p.catch(function () {});
      } catch (e) {
        /* ignore */
      }
    }

    function maybeShowBenignPrompt() {
      if (document.fullscreenElement) return; // recovered — nothing to show
      if (exitTimerActive) return; // a real violation countdown is already up
      promptBenignReentry();
    }

    if (!fullscreenEstablishedOnThisPage) {
      // Give the silent attempt (and the rest of the navigation grace
      // window, if this page was reached via Continue/auto-submit) time
      // to resolve before bothering the candidate with anything at all.
      setTimeout(
        maybeShowBenignPrompt,
        arrivedViaLegitimateNav ? NAV_GRACE_MS : 400
      );
    }

    document.addEventListener("fullscreenchange", function () {
      if (document.fullscreenElement) {
        fullscreenEstablishedOnThisPage = true;
        if (overlay.classList.contains("benign")) hideOverlay();
        if (!document.hidden) clearExitTimer();
      } else if (fullscreenEstablishedOnThisPage && !inSuppressionWindow()) {
        // A genuine mid-page exit (Escape key, etc.) well outside any
        // navigation grace window — this one starts the real countdown.
        startExitTimer();
      }
      // else: still hasn't (re-)established full-screen since this page
      // loaded, or we're inside the grace window — the deferred
      // maybeShowBenignPrompt() check above already has this covered,
      // nothing to do here.
    });

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        if (inSuppressionWindow()) {
          // Likely just the tail end of our own navigation settling —
          // re-check once the suppression window closes rather than
          // reacting immediately, since visibilitychange won't fire
          // again on its own if the tab is still hidden by then.
          setTimeout(() => {
            if (document.hidden && !inSuppressionWindow()) startExitTimer();
          }, Math.max(0, suppressUntil - Date.now()) + 50);
          return;
        }
        startExitTimer();
      } else if (document.fullscreenElement) {
        clearExitTimer();
      }
    });

    // Copy / paste / right-click blocking + logging (soft violations —
    // separate ladder from the exit-grace-period logic above).
    document.addEventListener("copy", function (e) {
      e.preventDefault();
      beacon("copy", "Copy attempt blocked").then(handleBeaconResult);
    });
    document.addEventListener("cut", function (e) {
      e.preventDefault();
    });
    document.addEventListener("paste", function (e) {
      e.preventDefault();
      beacon("paste", "Paste attempt blocked").then(handleBeaconResult);
    });
    document.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      beacon("right_click", "Right-click menu blocked").then(
        handleBeaconResult
      );
    });

    // Block common devtools / inspect / save / print shortcuts outright.
    document.addEventListener("keydown", function (e) {
      const key = (e.key || "").toLowerCase();
      const ctrlOrCmd = e.ctrlKey || e.metaKey;
      const blocked =
        key === "f12" ||
        (ctrlOrCmd && e.shiftKey && ["i", "j", "c"].includes(key)) ||
        (ctrlOrCmd && ["u", "s", "p"].includes(key));
      if (blocked) {
        e.preventDefault();
        beacon(
          "shortcut_blocked",
          `Blocked shortcut: ${e.ctrlKey ? "Ctrl+" : ""}${
            e.metaKey ? "Cmd+" : ""
          }${e.shiftKey ? "Shift+" : ""}${e.key}`
        ).then(handleBeaconResult);
      }
    });

    // Crude devtools-open heuristic (window size delta) — soft signal.
    let devtoolsOpen = false;
    setInterval(function () {
      const threshold = 160;
      const isOpen =
        window.outerWidth - window.innerWidth > threshold ||
        window.outerHeight - window.innerHeight > threshold;
      if (isOpen && !devtoolsOpen) {
        devtoolsOpen = true;
        beacon("devtools_open", "Developer tools window detected").then(
          handleBeaconResult
        );
      } else if (!isOpen) {
        devtoolsOpen = false;
      }
    }, 3000);

    // NOTE: no beforeunload handler here on purpose. This app submits a
    // normal HTML form and does a full page reload for every single
    // question — a beforeunload "leave site?" prompt fires on every
    // legitimate Continue click too, and in several browsers showing
    // that native dialog also triggers a spurious visibilitychange.

    AntiCheat._beacon = beacon;
  };

  AntiCheat.requestFullscreen = function () {
    const el = document.documentElement;
    if (el.requestFullscreen) return el.requestFullscreen();
    if (el.webkitRequestFullscreen) return el.webkitRequestFullscreen();
  };

  AntiCheat._showWarning = function (count, max) {
    let banner = document.getElementById("ac-warning-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "ac-warning-banner";
      banner.className = "ac-warning-banner";
      document.body.prepend(banner);
    }
    banner.textContent = `Warning ${count}/${max}: this activity is being monitored. ${
      count >= max
        ? "Ending session now."
        : "Please stay focused on the assessment."
    }`;
    clearTimeout(banner._acHideTimer);
    banner._acHideTimer = setTimeout(() => {
      banner.remove();
    }, 6000);
  };

  // ---------------------------------------------------------------
  // Keystroke dynamics: flight time (ms between keydowns) + dwell time
  // (ms a key is held). Attach to any textarea/input the candidate types
  // an answer into.
  // ---------------------------------------------------------------
  AntiCheat.attachKeystrokeTracking = function (el) {
    if (!el) return;
    const state = {
      lastKeydown: null,
      downTimes: {},
      flightTimes: [],
      dwellTimes: [],
    };
    el._acState = state;

    el.addEventListener("keydown", function (e) {
      const now = performance.now();
      if (state.lastKeydown != null) {
        state.flightTimes.push(Math.round(now - state.lastKeydown));
      }
      state.lastKeydown = now;
      if (!(e.key in state.downTimes)) {
        state.downTimes[e.key] = now;
      }
    });

    el.addEventListener("keyup", function (e) {
      const now = performance.now();
      const down = state.downTimes[e.key];
      if (down != null) {
        state.dwellTimes.push(Math.round(now - down));
        delete state.downTimes[e.key];
      }
    });
  };

  AntiCheat.getKeystrokeMetrics = function (el) {
    if (!el || !el._acState) return { flight_times: [], dwell_times: [] };
    return {
      flight_times: el._acState.flightTimes,
      dwell_times: el._acState.dwellTimes,
    };
  };

  AntiCheat.resetKeystrokeMetrics = function (el) {
    if (el && el._acState) {
      el._acState.flightTimes = [];
      el._acState.dwellTimes = [];
      el._acState.lastKeydown = null;
      el._acState.downTimes = {};
    }
  };

  global.AntiCheat = AntiCheat;
})(window);
