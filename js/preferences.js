// preferences.js

document.addEventListener("DOMContentLoaded", () => {
  const saveBtn = document.getElementById("savePreferencesBtn");
  const resetBtn = document.getElementById("resetPreferencesBtn");

  loadPreferences();

  saveBtn.addEventListener("click", () => {
    const preferences = {
      terrain: document.getElementById("terrain").value,
      surfaceStability: document.getElementById("surfaceStability").value,
      avoidCurbs: document.getElementById("avoidCurbs").checked,
      widePaths: document.getElementById("widePaths").checked,
      usesCrutches: document.getElementById("usesCrutches").checked,
      mobilityScooter: document.getElementById("mobilityScooter").checked,
      needsHandrails: document.getElementById("needsHandrails").checked,
      highContrast: document.getElementById("highContrast").checked,
      colorBlindMode: document.getElementById("colorBlindMode").checked,
      fontLarge: document.getElementById("fontLarge").checked,
    };

    localStorage.setItem("pathablePreferences", JSON.stringify(preferences));
    alert("Preferences saved!");
  });

  resetBtn.addEventListener("click", () => {
    if (confirm("Reset all preferences to default?")) {
      localStorage.removeItem("pathablePreferences");
      window.location.reload();
    }
  });
});

function loadPreferences() {
  const stored = localStorage.getItem("pathablePreferences");
  if (!stored) return;

  try {
    const prefs = JSON.parse(stored);
    document.getElementById("terrain").value = prefs.terrain || "flat";
    document.getElementById("surfaceStability").value = prefs.surfaceStability || "stable";
    document.getElementById("avoidCurbs").checked = prefs.avoidCurbs || false;
    document.getElementById("widePaths").checked = prefs.widePaths || false;
    document.getElementById("usesCrutches").checked = prefs.usesCrutches || false;
    document.getElementById("mobilityScooter").checked = prefs.mobilityScooter || false;
    document.getElementById("needsHandrails").checked = prefs.needsHandrails || false;
    document.getElementById("highContrast").checked = prefs.highContrast || false;
    document.getElementById("colorBlindMode").checked = prefs.colorBlindMode || false;
    document.getElementById("fontLarge").checked = prefs.fontLarge || false;
  } catch (e) {
    console.error("Failed to parse stored preferences", e);
  }
}
