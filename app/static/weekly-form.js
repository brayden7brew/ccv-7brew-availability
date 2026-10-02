(() => {
  'use strict';
  const form = document.getElementById('weekly-form');
  if (!form) return;
  const date = form.elements.namedItem('effective_date');
  const note = form.elements.namedItem('employee_note');
  const button = document.getElementById('weekly-submit');
  const credit = document.getElementById('weekly-credit');
  const feedback = document.getElementById('weekly-errors');
  const rows = Array.from(form.querySelectorAll('.day-row'));
  const minimum = Number(form.dataset.minimumMinutes);
  const blocked = form.dataset.limitBlocked === 'true';
  const buttonLabel = button.textContent;
  let sending = false;

  function minute(value) {
    if (!/^\d{2}:\d{2}$/.test(value)) return null;
    const [h, m] = value.split(':').map(Number);
    const result = h * 60 + m;
    return m < 60 && result >= 300 && result <= 1380 && result % 15 === 0 ? result : null;
  }

  function validate() {
    const errors = [];
    let counted = 0;
    if (!date.value) errors.push('Choose a start date.');
    else if (!date.validity.valid || date.value < date.min || date.value > date.max) {
      errors.push('Choose a start date within the allowed dates shown in the calendar.');
    }
    date.setAttribute('aria-invalid', String(!date.value || !date.validity.valid || date.value < date.min || date.value > date.max));
    for (const row of rows) {
      const mode = row.querySelector('[name$="_mode"]').value;
      const timed = mode === 'hours' || mode === 'unavailable';
      const start = row.querySelector('[name$="_start"]');
      const end = row.querySelector('[name$="_end"]');
      const message = row.querySelector('.day-error');
      const fromMinute = minute(start.value);
      start.disabled = !timed;
      end.disabled = !timed || fromMinute === null;
      for (const option of end.options) {
        option.disabled = option.value !== '' && (fromMinute === null || minute(option.value) <= fromMinute);
      }
      if (fromMinute !== null && end.value && minute(end.value) <= fromMinute) end.value = '';
      start.required = end.required = timed;
      let error = '';
      if (mode === 'all_day') counted += 600;
      else if (timed) {
        const from = minute(start.value);
        const to = minute(end.value);
        if (from === null || to === null) error = 'Choose both a From and To time.';
        else if (to <= from) error = 'To must be later than From.';
        else counted += Math.min(600, mode === 'unavailable' ? 1080 - (to - from) : to - from);
      } else if (mode !== 'none') error = 'Choose an availability option.';
      message.textContent = error;
      message.hidden = !error;
      start.setAttribute('aria-invalid', String(Boolean(error)));
      end.setAttribute('aria-invalid', String(Boolean(error)));
      if (error) errors.push(`${row.dataset.day}: ${error}`);
    }
    credit.textContent = minimum > 0
      ? `${counted / 60} of ${minimum / 60} required hours counted · Maximum 10 hours per day.`
      : `${counted / 60} hours counted · No weekly minimum applies.`;
    if (counted < minimum) errors.push(`Make ${(minimum - counted) / 60} more hours available to meet your weekly minimum. If you need to provide fewer hours, contact your stand manager to discuss an exception.`);
    if (note.value.length > 2000) errors.push('Keep your note to 2,000 characters or fewer.');
    if (blocked) errors.push('Your 30-day request limit has been reached. See the date above for your next request.');
    feedback.textContent = errors.length ? errors.join(' ') : 'Your request is ready to send.';
    form.dataset.validationState = errors.length ? 'invalid' : 'valid';
    button.disabled = sending || errors.length > 0;
    return errors.length === 0;
  }

  form.addEventListener('input', validate);
  form.addEventListener('change', validate);
  form.addEventListener('submit', event => {
    if (sending || !validate()) {
      event.preventDefault();
      return;
    }
    sending = true;
    button.disabled = true;
    button.textContent = 'Sending…';
  });
  window.addEventListener('pageshow', () => {
    sending = false;
    button.textContent = buttonLabel;
    validate();
  });
  validate();
})();
