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
      const periods = Array.from(row.querySelectorAll('.unavailable-period'));
      const message = row.querySelector('.day-error');
      const add = row.querySelector('.add-period');
      add.hidden = mode !== 'unavailable';
      add.disabled = periods.length >= 6;
      let error = '';
      const spans = [];
      periods.forEach((period, index) => {
        const active = timed && (mode === 'unavailable' || index === 0);
        period.hidden = mode === 'hours' && index > 0;
        period.setAttribute('role', 'group');
        period.setAttribute('aria-label', `${row.dataset.day} unavailable period ${index + 1}`);
        const start = period.querySelector('[name$="_start"]');
        const end = period.querySelector('[name$="_end"]');
        period.querySelector('.remove-period').hidden = mode !== 'unavailable' || periods.length === 1;
        const from = minute(start.value);
        start.disabled = !active;
        end.disabled = !active || from === null;
        for (const option of end.options) {
          option.disabled = option.value !== '' && (from === null || minute(option.value) <= from);
        }
        if (active && from !== null && end.value && minute(end.value) <= from) end.value = '';
        start.required = end.required = active;
        const to = minute(end.value);
        if (active) {
          if (from === null || to === null) error = 'Choose both a From and To time for each period.';
          else if (to <= from) error = 'To must be later than From.';
          else spans.push([from, to]);
        }
      });
      spans.sort((a, b) => a[0] - b[0]);
      if (spans.some((span, i) => i > 0 && span[0] < spans[i - 1][1])) {
        error = 'Unavailable periods must not overlap. Adjust or remove the overlapping period.';
      }
      if (mode === 'all_day') counted += 600;
      else if (timed && !error) {
        const duration = spans.reduce((sum, span) => sum + span[1] - span[0], 0);
        counted += Math.min(600, mode === 'unavailable' ? 1080 - duration : duration);
      } else if (!timed && mode !== 'none') error = 'Choose an availability option.';
      message.textContent = error;
      message.hidden = !error;
      periods.forEach(period => {
        period.querySelectorAll('select').forEach(field => field.setAttribute('aria-invalid', String(!field.disabled && Boolean(error))));
      });
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

  form.addEventListener('click', event => {
    const add = event.target.closest('.add-period');
    const remove = event.target.closest('.remove-period');
    if (!add && !remove) return;
    const row = event.target.closest('.day-row');
    const periods = row.querySelectorAll('.unavailable-period');
    if (add && periods.length < 6) {
      const period = periods[0].cloneNode(true);
      period.querySelectorAll('select').forEach(field => { field.value = ''; });
      add.before(period);
    } else if (remove && periods.length > 1) {
      remove.closest('.unavailable-period').remove();
    }
    validate();
  });

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
