(() => {
  const authorization = document.getElementById("authorize");
  const warning = document.getElementById("authorization-warning");
  if (authorization && warning) {
    authorization.addEventListener("invalid", () => {
      warning.hidden = false;
      authorization.classList.add("is-invalid");
    });
    authorization.addEventListener("change", () => {
      warning.hidden = authorization.checked;
      authorization.classList.toggle("is-invalid", !authorization.checked);
    });
  }
  const dropdown = document.getElementById("agreement-services");
  if (!dropdown) return;
  const boxes = Array.from(dropdown.querySelectorAll('input[name="case_id"]'));
  const summary = dropdown.querySelector("[data-services-summary]");
  function update() {
    const selected = boxes.filter((box) => box.checked);
    summary.textContent = selected.length ? `${selected.length} service${selected.length === 1 ? "" : "s"} selected` : "Select services";
    if (boxes.length) boxes[0].required = !selected.length;
  }
  boxes.forEach((box) => box.addEventListener("change", update));
  boxes[0]?.addEventListener("invalid", () => { dropdown.open = true; });
  document.addEventListener("click", (event) => {
    if (!dropdown.contains(event.target)) dropdown.open = false;
  });
  dropdown.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      dropdown.open = false;
      dropdown.querySelector("summary").focus();
    }
  });
  update();
})();
