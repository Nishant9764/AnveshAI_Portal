/**
 * testflow.js
 * ────────────
 * Moves between questions using fetch() instead of a real browser
 * navigation. This is the actual fix for full-screen dropping between
 * questions: full-screen is a property of the document/browsing
 * context, and most browsers exit it on ANY full page reload — that's
 * a platform security rule, not a bug we can patch around after the
 * fact. The only real fix is to stop reloading the document at all.
 *
 * The Flask routes are completely unchanged — they still render full
 * HTML pages exactly as before (POST -> redirect -> next question,
 * redirects followed transparently). This module fetches those same
 * URLs, swaps only the #ac-content element's contents into the current
 * (never-reloaded) document, and re-runs the small amount of JS setup
 * each question page needs (timer, keystroke tracking, submit
 * handling) — all generically, driven by data-* attributes rather than
 * per-page inline scripts, so any question type works the same way.
 *
 * Because the document never reloads, AntiCheat.initLockdown() only
 * ever runs once per real page load — its listeners are on `document`,
 * which persists across every soft-navigation automatically.
 */
(function (global) {
  const TestFlow = {};
  const SWAP_TARGET_ID = "ac-content";
  const LOADING_REVEAL_DELAY_MS = 280; // fast responses never show a loading screen at all

  const LOADING_MESSAGES = [
    "Reviewing your response…",
    "Preparing your next section…",
    "Personalizing the next question…",
    "Almost ready…",
  ];

  // ---------------------------------------------------------------
  // Advanced loading screen — only appears if a transition takes long
  // enough to actually need it (e.g. generating the next section's
  // questions live). Indeterminate by design: we genuinely don't know
  // how long a Gemini call will take, so there's no fake progress bar
  // promising a specific finish time.
  // ---------------------------------------------------------------
  function injectLoadingStyles() {
    if (document.getElementById("tf-loading-styles")) return;
    const style = document.createElement("style");
    style.id = "tf-loading-styles";
    style.textContent = `
      .tf-loading-overlay {
        position: fixed; inset: 0; z-index: 99997;
        background: rgba(246,247,251,0.94); backdrop-filter: blur(8px);
        display: none; align-items: center; justify-content: center;
        font-family: "Inter", -apple-system, sans-serif;
      }
      .tf-loading-overlay.active { display: flex; animation: tfFadeIn 0.25s ease; }
      @keyframes tfFadeIn { from { opacity: 0; } to { opacity: 1; } }
      .tf-loading-card { text-align: center; }
      .tf-spinner {
        width: 60px; height: 60px; margin: 0 auto 22px; border-radius: 50%;
        border: 4px solid #e6e8f0; border-top-color: #4338ca;
        animation: tfSpin 0.85s linear infinite;
      }
      @keyframes tfSpin { to { transform: rotate(360deg); } }
      .tf-loading-title { font-size: 16px; font-weight: 700; color: #171a2b; margin-bottom: 6px; }
      .tf-loading-msg {
        font-size: 13px; color: #6b7280; min-height: 18px;
        transition: opacity 0.25s ease;
      }
      .tf-loading-track {
        width: 200px; height: 4px; border-radius: 4px; background: #e6e8f0;
        margin: 20px auto 0; overflow: hidden; position: relative;
      }
      .tf-loading-bar {
        position: absolute; top: 0; left: -40%; width: 40%; height: 100%;
        background: linear-gradient(90deg, transparent, #4338ca, transparent);
        animation: tfBarSlide 1.3s ease-in-out infinite;
      }
      @keyframes tfBarSlide { 0% { left: -40%; } 100% { left: 100%; } }
    `;
    document.head.appendChild(style);
  }

  function buildLoadingOverlay() {
    if (document.getElementById("tf-loading-overlay")) return;
    const overlay = document.createElement("div");
    overlay.id = "tf-loading-overlay";
    overlay.className = "tf-loading-overlay";
    overlay.innerHTML = `
      <div class="tf-loading-card">
        <div class="tf-spinner"></div>
        <div class="tf-loading-title">One moment</div>
        <div class="tf-loading-msg" id="tf-loading-msg">Reviewing your response…</div>
        <div class="tf-loading-track"><div class="tf-loading-bar"></div></div>
      </div>
    `;
    document.body.appendChild(overlay);
  }

  let loadingMsgInterval = null;
  function showLoading() {
    injectLoadingStyles();
    buildLoadingOverlay();
    const overlay = document.getElementById("tf-loading-overlay");
    const msgEl = document.getElementById("tf-loading-msg");
    let i = 0;
    msgEl.textContent = LOADING_MESSAGES[0];
    overlay.classList.add("active");
    clearInterval(loadingMsgInterval);
    loadingMsgInterval = setInterval(function () {
      i = (i + 1) % LOADING_MESSAGES.length;
      msgEl.style.opacity = 0;
      setTimeout(function () {
        msgEl.textContent = LOADING_MESSAGES[i];
        msgEl.style.opacity = 1;
      }, 200);
    }, 1800);
  }
  function hideLoading() {
    clearInterval(loadingMsgInterval);
    const overlay = document.getElementById("tf-loading-overlay");
    if (overlay) overlay.classList.remove("active");
  }

  // ---------------------------------------------------------------
  // Core swap
  // ---------------------------------------------------------------
  function swapContent(html, url) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const newContent = doc.getElementById(SWAP_TARGET_ID);
    const current = document.getElementById(SWAP_TARGET_ID);
    if (!newContent || !current) {
      // Unexpected response shape — safest fallback is a real navigation
      // rather than silently showing nothing.
      window.location.href = url;
      return;
    }
    current.innerHTML = newContent.innerHTML;
    if (doc.title) document.title = doc.title;
    try {
      history.pushState({ tf: true }, doc.title || "", url);
    } catch (e) {
      /* ignore */
    }
    TestFlow.initPage();
  }

  // Public: submit any form via fetch instead of a real navigation.
  // Caller is responsible for filling in any hidden fields first.
  TestFlow.softSubmit = function (form) {
    const formData = new FormData(form);
    const revealTimer = setTimeout(showLoading, LOADING_REVEAL_DELAY_MS);

    fetch(form.action, { method: "POST", body: formData })
      .then(function (r) {
        return r.text().then(function (html) {
          return { html: html, url: r.url };
        });
      })
      .then(function (result) {
        clearTimeout(revealTimer);
        swapContent(result.html, result.url);
        hideLoading();
      })
      .catch(function () {
        clearTimeout(revealTimer);
        hideLoading();
        // Network hiccup — fall back to a real submit rather than
        // leaving the candidate stuck with a dead Continue button.
        form.submit();
      });
  };

  window.addEventListener("popstate", function () {
    // Back button mid-test is an edge case, not something worth faking
    // with more soft-nav machinery — just reload wherever it lands.
    window.location.reload();
  });

  // ---------------------------------------------------------------
  // Generic per-question page setup, driven entirely by data-*
  // attributes so it works identically for every question type without
  // any per-page inline script:
  //
  //   <form data-question-form data-time-limit="60" data-urgent-at="10">
  //     ... <textarea data-keystroke-track> for subjective answers ...
  //     <input name="time_taken_seconds"> <input name="keystroke_metrics">
  //   </form>
  //   <div id="timer"><span id="timer-text"></span></div>
  //
  // Runs once on real page load, and again after every soft-swap (since
  // the swapped-in DOM nodes are fresh and need their own timer/handlers
  // — but AntiCheat.initLockdown() itself no-ops on repeat calls, since
  // its listeners live on `document` and never need re-attaching).
  // ---------------------------------------------------------------
  TestFlow.initPage = function () {
    const contentEl = document.getElementById(SWAP_TARGET_ID);
    const sessionId = contentEl ? contentEl.dataset.sessionId : null;

    if (sessionId && global.AntiCheat) {
      AntiCheat.initLockdown(sessionId, {
        onTerminate: function (reason) {
          const path =
            reason === "screen_exit_timeout" ? "terminated" : "graceful-exit";
          window.location.href = "/test/" + sessionId + "/" + path;
        },
      });
    }

    const form = document.querySelector("#ac-content form[data-question-form]");
    if (!form) return; // a terminal page (complete/exit/etc.) — nothing further to wire up

    const answerEl = form.querySelector("[data-keystroke-track]");
    if (answerEl && global.AntiCheat)
      AntiCheat.attachKeystrokeTracking(answerEl);

    const timerRow = document.getElementById("timer");
    const timerText = document.getElementById("timer-text");
    const timeLimit = parseInt(form.dataset.timeLimit, 10) || 60;
    const urgentAt = parseInt(form.dataset.urgentAt, 10) || 10;
    const startedAt = performance.now();
    let remaining = timeLimit;
    let interval = null;

    function fillHiddenFields() {
      const timeField = form.querySelector('[name="time_taken_seconds"]');
      if (timeField)
        timeField.value = Math.round((performance.now() - startedAt) / 1000);
      const ksField = form.querySelector('[name="keystroke_metrics"]');
      if (ksField && answerEl)
        ksField.value = JSON.stringify(AntiCheat.getKeystrokeMetrics(answerEl));
    }

    function submitNow() {
      if (interval) clearInterval(interval);
      fillHiddenFields();
      TestFlow.softSubmit(form);
    }

    function tick() {
      remaining -= 1;
      const m = Math.floor(Math.max(remaining, 0) / 60)
        .toString()
        .padStart(2, "0");
      const s = Math.max(remaining, 0) % 60;
      if (timerText)
        timerText.textContent = m + ":" + s.toString().padStart(2, "0");
      if (timerRow && remaining <= urgentAt) timerRow.classList.add("urgent");
      if (remaining <= 0) submitNow();
    }

    if (timerRow && timerText) {
      interval = setInterval(tick, 1000);
      tick();
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submitNow();
    });
  };

  global.TestFlow = TestFlow;
  document.addEventListener("DOMContentLoaded", TestFlow.initPage);
})(window);
