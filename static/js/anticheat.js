/**
 * anticheat.js
 * ─────────────
 * Shared lockdown module. Listeners live on `document`, which — thanks
 * to testflow.js moving between questions via fetch() instead of real
 * navigations — is never torn down for the whole test after the very
 * first page load. That's what makes this module simple: it no longer
 * needs to guess whether an exit was "caused by our own navigation,"
 * because our own navigation genuinely never causes one anymore. Any
 * full-screen exit or tab switch it sees is a real signal.
 *
 * Usage:
 *   AntiCheat.initLockdown(sessionId, { onTerminate: (reason) => ... });
 *   (called once by testflow.js; safe to call again, it no-ops)
 *   AntiCheat.attachKeystrokeTracking(textareaEl);
 *   AntiCheat.getKeystrokeMetrics(textareaEl) — read before submitting
 *
 * Policy:
 *   - Exiting full-screen or switching tabs starts a 5-second grace
 *     period with a visible countdown. Returning in time: zero backend
 *     calls, zero strikes. Not returning: exactly one screen_exit_timeout
 *     call, which ends the session immediately — no strike ambiguity.
 *   - Soft violations (copy/paste/right-click/devtools/blocked
 *     shortcuts) are a separate, lower-severity ladder that still
 *     accumulates toward a 3-warning limit.
 */
(function (global) {
  const AntiCheat = {};
  const SCREEN_EXIT_LIMIT_MS = 5000;
  let lockdownInitialized = false;
  let lockdownActive = false;

  // ---------------------------------------------------------------
  // Visuals
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
        <div class="ac-exit-title">You've left the assessment</div>
        <div class="ac-exit-msg">Return now or this session will end automatically.</div>
        <button class="ac-return-btn" id="ac-return-btn" type="button">Return to Assessment</button>
      </div>
    `;
    document.body.appendChild(overlay);
  }

  // ---------------------------------------------------------------
  // Main lockdown — safe to call more than once, only the first call
  // actually attaches anything.
  // ---------------------------------------------------------------
  AntiCheat.initLockdown = function (sessionId, opts) {
    lockdownActive = true;
    if (lockdownInitialized) return;
    lockdownInitialized = true;
    opts = opts || {};

    injectStyles();
    buildOverlay();
    document.documentElement.classList.add("ac-locked-select");

    const overlay = document.getElementById("ac-exit-overlay");
    const ringProgress = document.getElementById("ac-ring-progress");
    const ringNumber = document.getElementById("ac-ring-number");
    const returnBtn = document.getElementById("ac-return-btn");
    const CIRCUMFERENCE = 2 * Math.PI * 52;

    let exitTimerActive = false;
    let leftAt = null;
    let pollHandle = null;

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

    function showOverlay() {
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
        clearExitTimer(true);
        if (opts.onTerminate) opts.onTerminate(data.reason || "warnings");
      }
    }

    // The 5-second grace period. Sends NOTHING to the backend when it
    // starts — only a return-in-time (no call at all) or a timeout
    // (exactly one screen_exit_timeout call) ever talk to the server.
    function startExitTimer() {
      if (exitTimerActive) return;
      exitTimerActive = true;
      leftAt = Date.now();
      showOverlay();
      ringNumber.textContent = "5";

      ringProgress.style.transition = "none";
      ringProgress.style.strokeDashoffset = "0";
      requestAnimationFrame(() => {
        ringProgress.style.transition = `stroke-dashoffset ${SCREEN_EXIT_LIMIT_MS}ms linear`;
        ringProgress.style.strokeDashoffset = String(CIRCUMFERENCE);
      });

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
          clearExitTimer(); // returned in time — no backend call, no strike
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

    document.addEventListener("fullscreenchange", function () {
      if (!lockdownActive) return;
      if (document.fullscreenElement) {
        if (!document.hidden) clearExitTimer();
      } else {
        startExitTimer();
      }
    });

    document.addEventListener("visibilitychange", function () {
      if (!lockdownActive) return;
      if (document.hidden) {
        startExitTimer();
      } else if (document.fullscreenElement) {
        clearExitTimer();
      }
    });

    // Copy / paste / right-click blocking + logging (soft violations —
    // a separate ladder from the exit-grace-period logic above).
    document.addEventListener("copy", function (e) {
      if (!lockdownActive) return;
      e.preventDefault();
      beacon("copy", "Copy attempt blocked").then(handleBeaconResult);
    });
    document.addEventListener("cut", function (e) {
      if (lockdownActive) e.preventDefault();
    });
    document.addEventListener("paste", function (e) {
      if (!lockdownActive) return;
      e.preventDefault();
      beacon("paste", "Paste attempt blocked").then(handleBeaconResult);
    });
    document.addEventListener("contextmenu", function (e) {
      if (!lockdownActive) return;
      e.preventDefault();
      beacon("right_click", "Right-click menu blocked").then(
        handleBeaconResult
      );
    });

    // Block common devtools / inspect / save / print shortcuts outright.
    document.addEventListener("keydown", function (e) {
      if (!lockdownActive) return;
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
      if (!lockdownActive) return;
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

    AntiCheat._beacon = beacon;
    AntiCheat._teardown = function () {
      lockdownActive = false;
      clearExitTimer(true);
      hideOverlay();
      const banner = document.getElementById("ac-warning-banner");
      if (banner) banner.remove();
      document.documentElement.classList.remove("ac-locked-select");
    };
  };

  // Call this once the candidate reaches a page that no longer needs
  // monitoring (test complete / graceful exit / terminated). Without
  // this, the document-level listeners above — which deliberately stay
  // attached across soft-navigations so they can watch the whole test —
  // would keep watching forever, including after the test is legitimately
  // over, and could misfire "you left the assessment" when someone just
  // closes the tab on the results screen.
  AntiCheat.teardownLockdown = function () {
    if (AntiCheat._teardown) AntiCheat._teardown();
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
  // (ms a key is held).
  // ---------------------------------------------------------------
  AntiCheat.attachKeystrokeTracking = function (el) {
    if (!el || el._acState) return; // already attached (e.g. after a soft-swap re-run)
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
