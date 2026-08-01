(function () {
  'use strict';

  // Runs synchronously the moment this script executes -- before the
  // stylesheets that follow it in <head> are requested, and well before
  // <body> is parsed -- so data-theme is set before first paint.
  // Priority: localStorage (explicit user choice) -> prefers-color-scheme -> light.
  var stored = localStorage.getItem('theme');
  var theme = stored
    ? stored
    : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', theme);

  function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('theme', t);
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = t === 'dark' ? '[ light mode ]' : '[ dark mode ]';
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute('data-theme');
    applyTheme(current === 'dark' ? 'light' : 'dark');
  }

  // The toggle button doesn't exist yet at this point in <head> parsing,
  // so its label and click handler are wired up once the DOM is ready.
  document.addEventListener('DOMContentLoaded', function () {
    var t = document.documentElement.getAttribute('data-theme') || 'light';
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.textContent = t === 'dark' ? '[ light mode ]' : '[ dark mode ]';
      btn.addEventListener('click', toggleTheme);
    }
  });
}());
