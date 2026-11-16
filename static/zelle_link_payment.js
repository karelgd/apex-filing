(() => {
  const modal = document.getElementById("zelle-link-modal");
  const form = document.getElementById("zelle-link-form");
  const search = document.getElementById("zelle-client-search");
  const results = document.getElementById("zelle-client-results");
  const invoiceField = document.getElementById("zelle-invoice-field");
  const invoices = document.getElementById("zelle-invoice-select");
  const status = document.getElementById("zelle-link-status");
  const submit = form.querySelector('button[type="submit"]');
  const clientId = form.elements.namedItem("client_id");
  let timer;
  let pending;

  function cancelPending() {
    clearTimeout(timer);
    pending?.abort();
  }

  function resetInvoices() {
    invoices.replaceChildren(new Option("Select an invoice", ""));
    invoices.disabled = true;
    invoiceField.hidden = true;
    submit.disabled = true;
    clientId.value = "";
  }

  async function load(url, render) {
    const controller = new AbortController();
    pending = controller;
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error("Request failed");
      const data = await response.json();
      if (!controller.signal.aborted) render(data);
    } catch {
      if (!controller.signal.aborted) status.textContent = "Could not load results. Please try again.";
    }
  }

  function selectClient(client) {
    cancelPending();
    results.hidden = true;
    resetInvoices();
    clientId.value = client.id;
    search.value = client.name;
    search.focus();
    status.textContent = "Loading invoices...";
    load(`${modal.dataset.clientUrl}/${client.id}/invoices`, (items) => {
      status.textContent = items.length ? "" : "No eligible invoices found for this client.";
      for (const invoice of items) {
        invoices.add(new Option(`${invoice.number} | ${invoice.case} | Balance: $${invoice.balance}`, invoice.id));
      }
      invoiceField.hidden = !items.length;
      invoices.disabled = !items.length;
    });
  }

  search.addEventListener("input", () => {
    cancelPending();
    resetInvoices();
    results.replaceChildren();
    results.hidden = true;
    status.textContent = "";
    const query = search.value.trim();
    if (query.length < 2) return;
    timer = setTimeout(() => {
      status.textContent = "Searching...";
      load(`${modal.dataset.clientUrl}?q=${encodeURIComponent(query)}`, (clients) => {
        status.textContent = clients.length ? "" : "No clients found.";
        for (const client of clients) {
          const item = document.createElement("button");
          item.type = "button";
          item.className = "list-group-item list-group-item-action text-break";
          const identifiers = [
            client.alien_suffix && `A-number ending ${client.alien_suffix}`,
            client.phone_suffix && `Phone ending ${client.phone_suffix}`,
          ].filter(Boolean);
          item.textContent = [client.name, ...identifiers].join(" | ");
          item.addEventListener("click", () => selectClient(client));
          results.append(item);
        }
        results.hidden = !clients.length;
      });
    }, 250);
  });

  invoices.addEventListener("change", () => { submit.disabled = !invoices.value; });
  modal.addEventListener("show.bs.modal", (event) => {
    const trigger = event.relatedTarget;
    form.action = trigger.dataset.linkAction;
    document.getElementById("zelle-link-summary").textContent = trigger.dataset.paymentLabel;
    search.value = "";
    results.replaceChildren();
    results.hidden = true;
    status.textContent = "";
    resetInvoices();
  });
  modal.addEventListener("shown.bs.modal", () => search.focus());
  modal.addEventListener("hide.bs.modal", cancelPending);
  form.addEventListener("submit", (event) => {
    if (!clientId.value || !invoices.value || submit.disabled) event.preventDefault();
    else submit.disabled = true;
  });
})();
