const conversion = document.getElementById("lead-conversion");
document.getElementById("open-lead-conversion")?.addEventListener("click", () => conversion.showModal());
if (conversion?.hasAttribute("data-reopen")) {
  conversion.showModal();
}
