"use strict";
(() => {
  const form = document.querySelector("#template-editor");
  if (!form) return;
  let target = form.querySelector("#id_body");
  const editors = ["header", "title", "body", "footer"].map(name => form.querySelector("#id_" + name));
  for (const editor of editors) editor.addEventListener("focus", () => { target = editor; });
  const insert = token => {
    const start = target.selectionStart, end = target.selectionEnd;
    target.setRangeText(token, start, end, "end");
    target.dispatchEvent(new Event("input", {bubbles: true}));
    target.focus();
  };
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-template-token]");
    if (button) insert(button.dataset.templateToken);
  });
  const refresh = () => {
    for (const group of document.querySelectorAll("[data-template-source]")) group.hidden = group.dataset.templateSource !== form.querySelector("#id_source").value;
    const custom = document.querySelector("#custom-field-tokens");
    custom.replaceChildren();
    for (const label of new Set(form.querySelector("#id_custom_fields").value.split("\n").map(text => text.trim()).filter(Boolean).slice(0, 20))) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "template-token";
      button.textContent = label; button.dataset.templateToken = "{{" + label + "}}";
      custom.append(button);
    }
  };
  form.querySelector("#id_source").addEventListener("change", refresh);
  form.querySelector("#id_custom_fields").addEventListener("input", refresh);
  refresh();
})();
