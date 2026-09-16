/* W5: theme preference — applied before first paint, CSP-safe external file.
   localStorage wins; falls back to OS preference; defaults to dark (brand). */
(function () {
  "use strict";
  var stored = null;
  try { stored = localStorage.getItem("ev-theme"); } catch (e) { /* private mode */ }
  var theme = stored === "light" || stored === "dark"
    ? stored
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = theme;
  window.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-btn");
    if (!btn) return;
    var label = function () { btn.textContent = document.documentElement.dataset.theme === "light" ? "dark mode" : "light mode"; };
    label();
    btn.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("ev-theme", next); } catch (e) { /* ignore */ }
      label();
    });
  });
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    window.addEventListener("load", function () { navigator.serviceWorker.register("/sw.js").catch(function () {}); });
  }
})();
