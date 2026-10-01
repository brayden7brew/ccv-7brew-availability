(() => {
  const form = document.getElementById('review-form');
  if (!form) return;
  const note = document.getElementById('review-note');
  const reject = document.getElementById('reject-button');
  const help = document.getElementById('reject-help');
  const confirmation = document.getElementById('replace-existing');
  const approve = document.getElementById('approve-button');
  function update() {
    if (confirmation && approve) approve.disabled = !confirmation.checked;
    const hasReason = note.value.trim().length > 0;
    reject.disabled = !hasReason;
    help.textContent = hasReason
      ? 'Your reason will be shared with the employee if you reject this request.'
      : 'Enter a reason to enable Reject.';
  }
  if (confirmation) confirmation.addEventListener('change', update);
  note.addEventListener('input', update);
  note.addEventListener('change', update);
  window.addEventListener('pageshow', update);
  form.addEventListener('submit', (event) => {
    update();
    if (approve && event.submitter === approve && approve.disabled) event.preventDefault();
    if (event.submitter === reject && reject.disabled) {
      event.preventDefault();
      note.focus();
    }
  });
  update();
})();
