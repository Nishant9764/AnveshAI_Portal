/**
 * anticheat.js
 * ─────────────
 * Shared lockdown + keystroke-dynamics module used on every test screen
 * (preflight calibration, Part A/B, Round 2, Round 3).
 *
 * Usage on a page:
 *   AntiCheat.initLockdown(sessionId, { onTerminate: () => location.href = '/test/graceful-exit' });
 *   AntiCheat.attachKeystrokeTracking(textareaEl);   // for typed-answer screens
 *   ... on submit: AntiCheat.getKeystrokeMetrics(textareaEl) -> {flight_times, dwell_times}
 */
(function (global) {
  const AntiCheat = {};

  // ---------------------------------------------------------------
  // Fullscreen + tab-switch + copy/paste + right-click + devtools lock
  // ---------------------------------------------------------------
  AntiCheat.initLockdown = function (sessionId, opts) {
    opts = opts || {};
    const beacon = (eventType, detail) => {
      fetch(`/api/test/${sessionId}/integrity-event`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type: eventType, detail: detail || "" }),
        keepalive: true,
      })
        .then((r) => r.json())
        .then((data) => {
          if (data.warnings_count != null) {
            AntiCheat._showWarning(data.warnings_count, opts.maxWarnings || 3);
          }
          if (data.should_terminate) {
            if (opts.onTerminate) opts.onTerminate();
          }
        })
        .catch(() => {});
    };

    // Fullscreen enforcement
    document.addEventListener("fullscreenchange", function () {
      if (!document.fullscreenElement) {
        beacon("fullscreen_exit", "Exited full-screen mode");
      }
    });

    // Tab switch / window blur
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        beacon("tab_switch", "Tab switched or window minimized");
      }
    });

    // Copy / paste / right-click blocking + logging
    document.addEventListener("copy", function (e) {
      e.preventDefault();
      beacon("copy", "Copy attempt blocked");
    });
    document.addEventListener("cut", function (e) {
      e.preventDefault();
    });
    document.addEventListener("paste", function (e) {
      e.preventDefault();
      beacon("paste", "Paste attempt blocked");
    });
    document.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      beacon("right_click", "Right-click menu blocked");
    });

    // Crude devtools-open heuristic (window size delta) — not bulletproof,
    // but catches the common case and adds a soft signal.
    let devtoolsOpen = false;
    setInterval(function () {
      const threshold = 160;
      const isOpen =
        window.outerWidth - window.innerWidth > threshold ||
        window.outerHeight - window.innerHeight > threshold;
      if (isOpen && !devtoolsOpen) {
        devtoolsOpen = true;
        beacon("devtools_open", "Developer tools window detected");
      } else if (!isOpen) {
        devtoolsOpen = false;
      }
    }, 3000);

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
      banner.style.cssText =
        "position:fixed;top:0;left:0;right:0;background:#ff4d6d;color:#fff;" +
        "text-align:center;padding:10px;font-family:monospace;font-size:13px;z-index:9999;";
      document.body.prepend(banner);
    }
    banner.textContent = `⚠ Warning ${count}/${max}: leaving full-screen, switching tabs, or copy/paste is monitored. ${
      count >= max ? "Test ending now." : "One more and the test will end."
    }`;
  };

  // ---------------------------------------------------------------
  // Keystroke dynamics: flight time (ms between keydowns) + dwell time
  // (ms a key is held). Attach to any textarea/input the candidate types
  // an answer into.
  // ---------------------------------------------------------------
  AntiCheat.attachKeystrokeTracking = function (el) {
    if (!el) return;
    const state = { lastKeydown: null, downTimes: {}, flightTimes: [], dwellTimes: [] };
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
