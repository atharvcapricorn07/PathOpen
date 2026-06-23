self.addEventListener("install", () => {
  console.log("Service Worker installed");
});

self.addEventListener("fetch", (event) => {
  // basic offline caching (optional simple version)
});