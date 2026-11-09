(() => {
  const form = document.querySelector('[data-intake]');
  if (!form) return;

  const steps = [...form.querySelectorAll('[data-step]')];
  const next = form.querySelector('[data-next]');
  const back = form.querySelector('[data-back]');
  const finish = form.querySelector('[data-finish]');
  const error = form.querySelector('[data-step-error]');
  const progress = document.querySelector('[data-progress]');
  const progressText = document.querySelector('[data-progress-text]');
  let current = steps.findIndex(step => step.dataset.step === form.dataset.start);
  let checkingPhone = false;
  let submitting = false;

  function showStep(index) {
    current = index;
    steps.forEach((step, position) => { step.hidden = position !== current; });
    back.hidden = current === 0;
    next.hidden = current === steps.length - 1;
    finish.hidden = !next.hidden;
    progress.value = steps[current].dataset.step;
    progressText.textContent = `Paso ${progress.value} de ${progress.max}`;
    error.hidden = true;
    steps[current].querySelector('h2').focus();
  }

  function invalidField(step) {
    return [...step.querySelectorAll('input, select')].find(field => !field.checkValidity());
  }

  async function verifyPhone() {
    checkingPhone = true;
    next.disabled = true;
    back.disabled = true;
    try {
      const response = await fetch(form.dataset.phoneUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrf_token.value },
        body: JSON.stringify({ phone: form.elements.phone.value }),
      });
      const result = await response.json();
      if (response.ok && result.valid) return true;
      error.textContent = result.error || 'No pudimos comprobar el teléfono. Inténtalo de nuevo.';
    } catch {
      error.textContent = 'No pudimos comprobar el teléfono. Inténtalo de nuevo.';
    } finally {
      checkingPhone = false;
      next.disabled = false;
      back.disabled = false;
    }
    error.hidden = false;
    form.elements.phone.focus();
    return false;
  }

  async function advance() {
    if (checkingPhone || submitting) return;
    const step = steps[current];
    const invalid = invalidField(step);
    if (invalid) {
      invalid.reportValidity();
      return;
    }
    if (step.querySelector('[name="phone"]') && !(await verifyPhone())) return;
    showStep(current + 1);
  }

  next.addEventListener('click', advance);
  back.addEventListener('click', () => { if (!checkingPhone && current > 0) showStep(current - 1); });
  form.noValidate = true;
  form.addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.matches('input') && !next.hidden) {
      event.preventDefault();
      advance();
    }
  });
  form.addEventListener('submit', event => {
    if (submitting || !next.hidden) {
      event.preventDefault();
      if (!next.hidden) advance();
      return;
    }
    for (const [index, step] of steps.entries()) {
      const invalid = invalidField(step);
      if (invalid) {
        event.preventDefault();
        showStep(index);
        invalid.reportValidity();
        return;
      }
    }
    submitting = true;
    finish.disabled = true;
  });
})();
