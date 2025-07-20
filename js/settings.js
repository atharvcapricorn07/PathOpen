document.addEventListener('DOMContentLoaded', () => {
  const settings = {
    mobilityType: document.getElementById('mobilityType'),
    avoidStairs: document.getElementById('avoidStairs'),
    avoidEscalators: document.getElementById('avoidEscalators'),
    preferElevators: document.getElementById('preferElevators'),
    darkMode: document.getElementById('darkMode'),
    voiceNav: document.getElementById('voiceNav'),
    vibrationAlerts: document.getElementById('vibrationAlerts'),
    followMe: document.getElementById('followMe'),
    locationAccess: document.getElementById('locationAccess'),
    saveRoutes: document.getElementById('saveRoutes'),
  };

  const SETTINGS_KEYS = Object.keys(settings);

  // Load saved settings from localStorage
  SETTINGS_KEYS.forEach(key => {
    const savedValue = localStorage.getItem(key);
    if (savedValue !== null) {
      const input = settings[key];
      if (input.type === 'checkbox') {
        input.checked = savedValue === 'true';
      } else {
        input.value = savedValue;
      }
    }
  });

  // Save settings
  document.getElementById('saveBtn').addEventListener('click', () => {
    SETTINGS_KEYS.forEach(key => {
      const input = settings[key];
      if (input.type === 'checkbox') {
        localStorage.setItem(key, input.checked);
      } else {
        localStorage.setItem(key, input.value);
      }
    });
    alert('Settings saved!');
  });

  // Clear settings
  document.getElementById('clearBtn').addEventListener('click', () => {
    SETTINGS_KEYS.forEach(key => localStorage.removeItem(key));
    location.reload(); // Refresh UI to reset inputs
  });
});
