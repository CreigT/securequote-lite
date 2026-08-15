const form = document.querySelector("#quote-form");
const statusBox = document.querySelector("#form-status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusBox.className = "status";
  statusBox.textContent = "";
  if (!form.reportValidity()) return;

  const button = form.querySelector("button[type='submit']");
  button.disabled = true;
  button.textContent = "Validating…";
  try {
    const response = await fetch("/securequote/api/intakes", {method: "POST", body: new FormData(form)});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "The intake could not be validated.");
    statusBox.className = "status success";
    statusBox.textContent = body.message;
    form.reset();
    window.location.assign(body.review_url);
  } catch (error) {
    statusBox.className = "status error";
    statusBox.textContent = error.message || "The intake could not be validated.";
  } finally {
    button.disabled = false;
    button.innerHTML = "Analyze <span aria-hidden='true'>→</span>";
    statusBox.focus();
  }
});
