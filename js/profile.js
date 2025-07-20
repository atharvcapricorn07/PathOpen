document.addEventListener("DOMContentLoaded", () => {
  const items = document.querySelectorAll(".menu-item");

  items.forEach(item => {
    item.addEventListener("click", () => {
      item.classList.add("clicked");
      setTimeout(() => {
        item.classList.remove("clicked");
      }, 200);
    });
  });
});
