(() => {
  const form = document.querySelector('[data-time-off-form]');
  if (!form) return;
  const full = form.elements.full_day;
  const first = form.elements.first;
  const last = form.elements.last;
  const from = form.elements.from;
  const to = form.elements.to;
  let dirty = false;
  document.addEventListener('input', () => { dirty = true; });
  document.addEventListener('change', () => { dirty = true; });
  function update() {
    from.disabled = to.disabled = full.checked;
    form.querySelector('[data-partial-times]').hidden = full.checked;
    from.required = to.required = !full.checked;
    last.min = first.value || first.min;
    to.min = from.value;
    to.setCustomValidity(!full.checked && from.value && to.value && to.value <= from.value
      ? 'End time must be after start time.' : '');
  }
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
  if (document.querySelector('[data-requests-processing]')) {
    function refresh() {
      if (dirty) return; // Never discard a draft request or blackout edit.
      if (document.visibilityState === 'visible') window.location.reload();
      else window.setTimeout(refresh, 5000);
    }
    window.setTimeout(refresh, 5000);
  }
})();
