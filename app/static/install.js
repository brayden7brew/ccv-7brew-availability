(() => {
  'use strict';
  const panel = document.getElementById('install-panel');
  const trigger = document.getElementById('install-help');
  const install = document.getElementById('install-app');
  const steps = document.getElementById('install-steps');
  if (!panel) return;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const mobile = ios || /Android/.test(navigator.userAgent);
  const standalone = window.matchMedia('(display-mode: standalone)');
  let deferred = null;
  const installed = () => standalone.matches || navigator.standalone === true;
  const key = 'ccv-install-dismissed-until';
  function dismissed() {
    try { return Number(localStorage.getItem(key)) > Date.now(); } catch (_) { return false; }
  }
  function show(manual = false) {
    if (!mobile || installed() || (!manual && dismissed())) return;
    steps.textContent = ios
      ? 'In Safari, tap Share (the square with an upward arrow), then Add to Home Screen. Keep Open as Web App on if shown, and tap Add. If you’re in another app’s browser, open this page in Safari first.'
      : 'Open your browser’s menu and choose Add to Home screen or Install app, then confirm.';
    install.hidden = !deferred;
    panel.hidden = false;
  }
  trigger.hidden = !mobile || installed();
  trigger.addEventListener('click', () => show(true));
  document.getElementById('install-dismiss').addEventListener('click', () => {
    panel.hidden = true;
    try { localStorage.setItem(key, String(Date.now() + 30 * 86400000)); } catch (_) { /* Storage may be unavailable. */ }
    trigger.focus();
  });
  window.addEventListener('beforeinstallprompt', event => {
    if (!mobile || installed()) return;
    event.preventDefault();
    deferred = event;
    show();
  });
  install.addEventListener('click', async () => {
    if (!deferred) return;
    const event = deferred;
    deferred = null;
    install.hidden = true;
    try {
      await event.prompt();
      if ((await event.userChoice).outcome === 'accepted') panel.hidden = true;
    } catch (_) { show(true); }
  });
  function hideInstalled() {
    if (installed()) { panel.hidden = true; trigger.hidden = true; }
  }
  standalone.addEventListener('change', hideInstalled);
  window.addEventListener('appinstalled', () => { panel.hidden = true; trigger.hidden = true; deferred = null; });
  show();
})();
