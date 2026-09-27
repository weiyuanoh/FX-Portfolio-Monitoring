(() => {
  let dragged = null;

  const shelfFor = (scope) => document.querySelector(`.group-shelf[data-group-scope="${scope}"]`);

  const fieldsIn = (shelf) => [...shelf.querySelectorAll("[data-group-chip-field]")].map((chip) => chip.dataset.groupChipField);

  const updateGrouping = (scope, fields) => {
    if (!window.dash_clientside || !window.dash_clientside.set_props) return;
    window.dash_clientside.set_props(`${scope}-grouping`, { data: fields });
  };

  document.addEventListener("dragstart", (event) => {
    const source = event.target.closest("[data-group-field], [data-group-chip-field]");
    if (!source) return;
    const scope = source.dataset.groupScope || source.closest("[data-group-scope]")?.dataset.groupScope;
    const field = source.dataset.groupField || source.dataset.groupChipField;
    if (!scope || !field) return;
    dragged = { scope, field };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", field);
  });

  document.addEventListener("dragover", (event) => {
    const shelf = event.target.closest(".group-shelf[data-group-scope]");
    if (!shelf || !dragged || shelf.dataset.groupScope !== dragged.scope) return;
    event.preventDefault();
    shelf.classList.add("is-dragging-over");
    event.dataTransfer.dropEffect = "move";
  });

  document.addEventListener("dragleave", (event) => {
    const shelf = event.target.closest(".group-shelf[data-group-scope]");
    if (shelf) shelf.classList.remove("is-dragging-over");
  });

  document.addEventListener("dragend", () => {
    document.querySelectorAll(".group-shelf.is-dragging-over").forEach((shelf) => shelf.classList.remove("is-dragging-over"));
    dragged = null;
  });

  document.addEventListener("drop", (event) => {
    const shelf = event.target.closest(".group-shelf[data-group-scope]");
    if (!shelf || !dragged || shelf.dataset.groupScope !== dragged.scope) return;
    event.preventDefault();
    shelf.classList.remove("is-dragging-over");
    const { scope, field } = dragged;
    const targetChip = event.target.closest("[data-group-chip-field]");
    const targetField = targetChip?.dataset.groupChipField;
    const fields = fieldsIn(shelf).filter((item) => item !== field);
    if (targetField && targetField !== field) {
      fields.splice(Math.max(0, fields.indexOf(targetField)), 0, field);
    } else {
      fields.push(field);
    }
    updateGrouping(scope, fields);
    dragged = null;
  });

  document.addEventListener("click", (event) => {
    const remove = event.target.closest(".group-remove");
    if (!remove) return;
    const shelf = remove.closest(".group-shelf[data-group-scope]");
    const chip = remove.closest("[data-group-chip-field]");
    if (!shelf || !chip) return;
    event.preventDefault();
    updateGrouping(shelf.dataset.groupScope, fieldsIn(shelf).filter((field) => field !== chip.dataset.groupChipField));
  });
})();
