(() => {
  if (!document.querySelector('[data-requests-processing]')) return;
  function refresh() {
    if (document.visibilityState === 'visible') window.location.reload();
    else window.setTimeout(refresh, 5000);
  }
  window.setTimeout(refresh, 5000);
})();
