(() => {
  const form = document.getElementById('review-form');
  if (!form) return;
  const note = document.getElementById('review-note');
  const reject = document.getElementById('reject-button');
  const help = document.getElementById('reject-help');
  const approve = document.getElementById('approve-button');
  const approveLabel = approve ? approve.textContent : '';
  const rejectLabel = reject.textContent;
  let submitting = false;
  let decisionInput = null;
  function update() {
    const hasReason = note.value.trim().length > 0;
    reject.disabled = submitting || !hasReason;
    if (approve) approve.disabled = submitting;
    help.textContent = submitting ? 'Saving your decision. Please wait…' : hasReason
      ? 'Your reason will be shared with the employee if you reject this request.'
      : 'Enter a reason to enable Reject.';
  }
  note.addEventListener('input', update);
  note.addEventListener('change', update);
  window.addEventListener('pageshow', () => {
    submitting = false;
    if (decisionInput) decisionInput.remove();
    decisionInput = null;
    form.removeAttribute('aria-busy');
    if (approve) approve.textContent = approveLabel;
    reject.textContent = rejectLabel;
    update();
  });
  form.addEventListener('submit', (event) => {
    if (submitting) { event.preventDefault(); return; }
    update();
    if (event.submitter === reject && reject.disabled) {
      event.preventDefault();
      note.focus();
      return;
    }
    if (!event.submitter || (event.submitter !== approve && event.submitter !== reject)) {
      event.preventDefault();
      return;
    }
    // Disabled buttons are omitted from native form data; preserve the clicked decision.
    decisionInput = document.createElement('input');
    decisionInput.type = 'hidden';
    decisionInput.name = 'decision';
    decisionInput.value = event.submitter.value;
    form.appendChild(decisionInput);
    submitting = true;
    form.setAttribute('aria-busy', 'true');
    event.submitter.textContent = 'Saving…';
    update();
  });
  update();
})();
