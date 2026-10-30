(() => {
  const blackout = document.querySelector('[data-blackout-form]');
  if (blackout) {
    function updateBlackout() {
      const limited = blackout.elements.limit_period.checked;
      blackout.querySelector('[data-blackout-period]').hidden = !limited;
      blackout.elements.period.disabled = !limited;
    }
    const allStands = blackout.elements.all_stands;
    if (allStands) {
      function updateStands() {
        blackout.querySelectorAll('[name="locations"]').forEach(input => {
          input.disabled = allStands.checked;
        });
      }
      allStands.addEventListener('change', updateStands);
      updateStands();
    }
    blackout.elements.limit_period.addEventListener('change', updateBlackout);
    updateBlackout();
  }
  const form = document.querySelector('[data-time-off-form]');
  if (!form) return;
  const full = form.elements.full_day;
  const first = form.elements.first;
  const last = form.elements.last;
  const multiple = form.elements.multiple_days;
  const from = form.elements.from;
  const to = form.elements.to;
  let dirty = false;
  document.addEventListener('input', () => { dirty = true; });
  document.addEventListener('change', () => { dirty = true; });
  function update(event) {
    if (event && event.target === multiple && multiple.checked) full.checked = true;
    if (event && event.target === full && !full.checked) multiple.checked = false;
    form.querySelector('[data-time-off-last]').hidden = !multiple.checked;
    form.querySelector('[data-first-day-label]').textContent = multiple.checked ? 'First day' : 'Day';
    last.disabled = !multiple.checked;
    last.required = multiple.checked;
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
