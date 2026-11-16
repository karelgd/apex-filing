(() => {
  const selectAll = document.getElementById("zelle-select-all");
  const form = document.getElementById("zelle-mark-selected");
  const submit = form.querySelector('button[type="submit"]');
  const rows = Array.from(form.elements).filter(
    (element) => element.name === "alert_id" && !element.disabled
  );

  function updateSelection() {
    const selected = rows.filter((row) => row.checked).length;
    selectAll.checked = rows.length > 0 && selected === rows.length;
    selectAll.indeterminate = selected > 0 && selected < rows.length;
    submit.disabled = selected === 0;
  }

  selectAll.addEventListener("change", () => {
    rows.forEach((row) => { row.checked = selectAll.checked; });
    updateSelection();
  });
  rows.forEach((row) => row.addEventListener("change", updateSelection));
  updateSelection();
})();
