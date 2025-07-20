// --- Setup ---
let start = null, dest = null, routeControl;
let routeActive = false;

const map = L.map('map').setView([0, 0], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

let startMarker = null;
let destMarker = null;

// --- Helpers ---
function haversine([lat1, lon1], [lat2, lon2]) {
  const toRad = d => d * Math.PI / 180;
  const R = 6371e3;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat/2)**2 +
            Math.cos(toRad(lat1)) *
            Math.cos(toRad(lat2)) *
            Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

async function reverseGeocode(lat, lon) {
  const url = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}`;
  const proxyUrl = 'https://api.allorigins.win/raw?url=' + encodeURIComponent(url);
  const res = await fetch(proxyUrl);
  if (!res.ok) throw new Error('Failed reverse geocode');
  const data = await res.json();
  return data.display_name || '';
}

async function geocode(q) {
  const nomUrl = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(q)}`;
  const proxyUrl = 'https://api.allorigins.win/raw?url=' + encodeURIComponent(nomUrl);
  const res = await fetch(proxyUrl, { headers: { 'Accept-Language': 'en' } });
  if (!res.ok) throw new Error('Failed geocode');
  return res.json();
}

// --- Elements ---
const startInput = document.getElementById('startSearch');
const endInput = document.getElementById('endSearch');
const box = document.getElementById('suggestions');
const wrapper = box.parentNode;
let lastSuggestions = [];
let activeInput = null;

// --- Geolocation Init ---
navigator.geolocation.getCurrentPosition(async pos => {
  start = [pos.coords.latitude, pos.coords.longitude];
  map.setView(start, 16);

  const address = await reverseGeocode(...start);
  startInput.value = address;

  if (startMarker) startMarker.setLatLng(start);
  else startMarker = L.marker(start).addTo(map).bindPopup("Start (Your location)").openPopup();
}, () => {
  map.setView([47.6, -122.3], 12);
});

// --- Typeahead + Suggestions ---
[startInput, endInput].forEach(input => {
  input.addEventListener('input', async () => {
    activeInput = input;
    const q = input.value.trim();
    if (q.length < 3) {
      box.innerHTML = '';
      return;
    }
    let results;
    try {
      results = await geocode(q);
    } catch {
      box.innerHTML = '<div class="suggestion">Network error</div>';
      return;
    }
    if (!results.length) {
      box.innerHTML = '<div class="suggestion">No results</div>';
      return;
    }
    if (start) {
      results.sort((a,b) => {
        const da = haversine(start, [parseFloat(a.lat), parseFloat(a.lon)]);
        const db = haversine(start, [parseFloat(b.lat), parseFloat(b.lon)]);
        return da - db;
      });
    }
    lastSuggestions = results;
    box.innerHTML = results.slice(0,5).map(r => `
      <div class="suggestion" data-lat="${r.lat}" data-lon="${r.lon}">
        ${r.display_name}
      </div>
    `).join('');
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && lastSuggestions.length) {
      const first = lastSuggestions[0];
      selectSuggestion(first.display_name, first.lat, first.lon);
      box.innerHTML = '';
    }
  });

  input.addEventListener('focus', () => {
    activeInput = input;
  });
});

// --- Suggestion Click ---
box.addEventListener('click', e => {
  const el = e.target.closest('.suggestion');
  if (!el) return;
  selectSuggestion(el.textContent, el.dataset.lat, el.dataset.lon);
});

// --- Select Suggestion ---
function selectSuggestion(name, lat, lon) {
  if (routeActive) return;

  if (activeInput === startInput) {
    start = [parseFloat(lat), parseFloat(lon)];
    startInput.value = name;
    if (startMarker) startMarker.setLatLng(start).bindPopup("Start").openPopup();
    else startMarker = L.marker(start).addTo(map).bindPopup("Start").openPopup();
  } else if (activeInput === endInput) {
    dest = [parseFloat(lat), parseFloat(lon)];
    endInput.value = name;
    if (destMarker) destMarker.setLatLng(dest).bindPopup("Destination").openPopup();
    else destMarker = L.marker(dest).addTo(map).bindPopup("Destination").openPopup();
  }
  box.innerHTML = '';
  map.setView([lat, lon], 16);
}

// --- Click Outside to Close ---
document.addEventListener('click', e => {
  if (!wrapper.contains(e.target) && e.target !== startInput && e.target !== endInput) {
    box.innerHTML = '';
  }
});

// --- Use My Location ---
document.getElementById('useLocationBtn').addEventListener('click', async () => {
  if (!navigator.geolocation) {
    alert('Geolocation is not supported by your browser.');
    return;
  }
  navigator.geolocation.getCurrentPosition(async pos => {
    start = [pos.coords.latitude, pos.coords.longitude];
    const address = await reverseGeocode(...start);
    startInput.value = address;
    if (startMarker) startMarker.setLatLng(start).bindPopup("Start (Your location)").openPopup();
    else startMarker = L.marker(start).addTo(map).bindPopup("Start (Your location)").openPopup();
    map.setView(start, 16);
  }, () => {
    alert('Unable to retrieve your location.');
  });
});

// --- Routing ---
document.getElementById('routeBtn').addEventListener('click', () => {
  if (!start) return alert('Select a valid start location.');
  if (!dest) return alert('Select a destination first.');
  if (routeControl) map.removeControl(routeControl);

  routeControl = L.Routing.control({
    waypoints: [L.latLng(start), L.latLng(dest)],
    router: L.Routing.osrmv1({ serviceUrl: 'https://router.project-osrm.org/route/v1' }),
    routeWhileDragging: false,
    draggableWaypoints: false,
    showAlternatives: false,
    lineOptions: { styles: [{ color: 'blue', weight: 4 }] }
  }).addTo(map);

  routeActive = true;
  document.getElementById('cancelRouteBtn').style.display = 'inline-block';

  // ✅ Show nearby ADA markers after routing
  showNearbyADAMarkers();
});

// --- Cancel Route ---
document.getElementById('cancelRouteBtn').addEventListener('click', () => {
  if (routeControl) {
    map.removeControl(routeControl);
    routeControl = null;
  }
  dest = null;
  routeActive = false;
  document.getElementById('cancelRouteBtn').style.display = 'none';

  if (destMarker) {
    map.removeLayer(destMarker);
    destMarker = null;
  }

  endInput.value = '';
  alert('Route canceled. You can now pick a new destination.');
});

// --- Menu Buttons ---
document.getElementById('settingsBtn').addEventListener('click', () => {
  window.location.href = 'html/settings.html';
});
document.getElementById('reportBtn').addEventListener('click', () => {
  window.location.href = 'html/ai.html';
});
['preferencesBtn', 'profileBtn'].forEach(id => {
  document.getElementById(id).addEventListener('click', () => {
    if (id === 'preferencesBtn') {
      window.location.href = 'html/preferences.html';
    } else if (id === 'profileBtn') {
      window.location.href = 'html/profile.html';
    }
  });
});

function showNearbyADAMarkers() {
  const adaData = localStorage.getItem("ada_result");
  if (!adaData || !dest) return;

  const { markers } = JSON.parse(adaData);
  const HALF_MILE_METERS = 804.67;

  markers.forEach(markerData => {
    const distance = haversine([markerData.lat, markerData.lng], dest);
    if (distance <= HALF_MILE_METERS) {
      const icon = L.divIcon({
        className: 'custom-marker-label',
        html: `<div class="marker-label">${markerData.label}</div>`,
        iconSize: [100, 40],
        iconAnchor: [50, 20]
      });

      const marker = L.marker([markerData.lat, markerData.lng], { icon }).addTo(map);

      // 👇 Add click event to show the image
      marker.on('click', () => {
        document.querySelectorAll('.image-overlay').forEach(el => el.remove());

        const img = document.createElement('img');
        img.src = markerData.imageUrl;
        img.className = 'image-overlay fade-in';

        document.body.appendChild(img);

        const screenPos = map.latLngToContainerPoint(marker.getLatLng());
        img.style.left = `${screenPos.x - 100}px`;
        img.style.top = `${screenPos.y - 100}px`;

        // optional: auto-close image on next map click
        map.once('click', () => {
          img.classList.remove('fade-in');
          img.classList.add('fade-out');
          setTimeout(() => img.remove(), 300);
        });
      });
    }
  });

  localStorage.removeItem("ada_result");
}

