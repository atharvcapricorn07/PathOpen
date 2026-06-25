// --- Setup ---
let start = null, dest = null, routeControl;
let routeActive = false;
let adaMarkers = [];

// --- Elements (declared first so geolocation callback can reference them) ---
const startInput = document.getElementById('startSearch');
const endInput = document.getElementById('endSearch');
const box = document.getElementById('suggestions');
const wrapper = box.parentNode;
let lastSuggestions = [];
let activeInput = null;

const map = L.map('map').setView([47.6062, -122.3321], 13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
  subdomains: 'abcd',
  maxZoom: 19
}).addTo(map);

let startMarker = null;
let destMarker = null;

// --- Toast ---
function showToast(msg, duration = 3000) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('show'), duration);
}

// --- Helpers ---
function haversine([lat1, lon1], [lat2, lon2]) {
  const toRad = d => d * Math.PI / 180;
  const R = 6371e3;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

async function reverseGeocode(lat, lon) {
  try {
    const res = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&email=pathable-app@example.com`,
      { headers: { 'Accept-Language': 'en' } }
    );
    if (!res.ok) return '';
    const data = await res.json();
    return data.display_name || '';
  } catch (e) { console.error('reverseGeocode error:', e); return ''; }
}

// --- Geocode with location bias ---
async function geocode(q) {
  let url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(q)}&addressdetails=1&limit=10&email=pathable-app@example.com`;

  if (start) {
    const [lat, lon] = start;
    const delta = 0.5;
    url += `&viewbox=${lon - delta},${lat + delta},${lon + delta},${lat - delta}&bounded=0`;
  }

  const res = await fetch(url, {
    headers: { 'Accept-Language': 'en' }
  });
  if (!res.ok) throw new Error(`Geocode failed: ${res.status}`);
  return res.json();
}

// --- Geolocation ---
function requestLocation() {
  if (!navigator.geolocation) {
    showToast('Geolocation not supported by your browser.');
    return;
  }
  navigator.geolocation.getCurrentPosition(
    async pos => {
      hideBanner();
      start = [pos.coords.latitude, pos.coords.longitude];
      map.setView(start, 16);
      const address = await reverseGeocode(...start);
      startInput.value = address;
      if (startMarker) startMarker.setLatLng(start).bindPopup("Start (Your location)").openPopup();
      else startMarker = L.marker(start).addTo(map).bindPopup("Start (Your location)").openPopup();
    },
    err => {
      hideBanner();
      if (err.code === 1) {
        showToast('Location blocked. Enable it in browser settings and refresh.', 5000);
      } else if (err.code === 2) {
        showToast('Location unavailable. Try again.', 4000);
      } else {
        showToast('Location request timed out.', 4000);
      }
    },
    { timeout: 10000, maximumAge: 60000 }
  );
}

function hideBanner() {
  const banner = document.getElementById('locationBanner');
  if (banner) banner.classList.add('hidden');
}

function isSafari() {
  const ua = navigator.userAgent;
  return /^((?!chrome|android).)*safari/i.test(ua);
}

if (isSafari()) {
  requestLocation();
} else {
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('locationBannerBtn').addEventListener('click', () => {
      requestLocation();
    });
    document.getElementById('locationBannerDismiss').addEventListener('click', () => {
      hideBanner();
      map.setView([47.6062, -122.3321], 12);
    });

    if (navigator.permissions) {
      navigator.permissions.query({ name: 'geolocation' }).then(result => {
        if (result.state === 'granted') {
          hideBanner();
          requestLocation();
        }
      });
    }
  });
}

// --- Typeahead ---
[startInput, endInput].forEach(input => {
  input.addEventListener('input', async () => {
    activeInput = input;
    const q = input.value.trim();
    if (q.length < 3) { box.innerHTML = ''; return; }
    let results;
    try { results = await geocode(q); }
    catch { box.innerHTML = '<div class="suggestion">Network error</div>'; return; }
    if (!results.length) { box.innerHTML = '<div class="suggestion">No results</div>'; return; }

    if (start) {
      results.sort((a, b) => {
        const da = haversine(start, [parseFloat(a.lat), parseFloat(a.lon)]);
        const db = haversine(start, [parseFloat(b.lat), parseFloat(b.lon)]);
        return da - db;
      });
    }

    lastSuggestions = results;
    box.innerHTML = results.slice(0, 5).map(r =>
      `<div class="suggestion" data-lat="${r.lat}" data-lon="${r.lon}">${r.display_name}</div>`
    ).join('');
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && lastSuggestions.length) {
      const first = lastSuggestions[0];
      selectSuggestion(first.display_name, first.lat, first.lon);
      box.innerHTML = '';
    }
  });
  input.addEventListener('focus', () => { activeInput = input; });
});

box.addEventListener('click', e => {
  const el = e.target.closest('.suggestion');
  if (!el) return;
  selectSuggestion(el.textContent, el.dataset.lat, el.dataset.lon);
});

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

document.addEventListener('click', e => {
  if (!wrapper.contains(e.target) && e.target !== startInput && e.target !== endInput) {
    box.innerHTML = '';
  }
});

map.on('click', async (e) => {
  if (routeActive) return;
  const { lat, lng } = e.latlng;
  dest = [lat, lng];
  const address = await reverseGeocode(lat, lng);
  endInput.value = address;
  if (destMarker) destMarker.setLatLng(dest).bindPopup("Destination").openPopup();
  else destMarker = L.marker(dest).addTo(map).bindPopup("Destination").openPopup();
  map.setView(dest, map.getZoom());
  box.innerHTML = '';
});

document.getElementById('useLocationBtn').addEventListener('click', () => {
  requestLocation();
});

// --- Routing ---
document.getElementById('routeBtn').addEventListener('click', () => {
  if (!start) return showToast('Set a start location first.');
  if (!dest) return showToast('Set a destination first.');
  if (routeControl) map.removeControl(routeControl);
  routeControl = L.Routing.control({
    waypoints: [L.latLng(start), L.latLng(dest)],
    router: L.Routing.osrmv1({ serviceUrl: 'https://router.project-osrm.org/route/v1' }),
    routeWhileDragging: false,
    draggableWaypoints: false,
    showAlternatives: false,
    lineOptions: { styles: [{ color: '#0d9488', weight: 4 }] }
  }).addTo(map);
  routeActive = true;
  document.getElementById('cancelRouteBtn').style.display = 'inline-block';
  showNearbyADAMarkers();
});

document.getElementById('cancelRouteBtn').addEventListener('click', () => {
  if (routeControl) { map.removeControl(routeControl); routeControl = null; }
  adaMarkers.forEach(m => map.removeLayer(m));
  adaMarkers = [];
  dest = null;
  routeActive = false;
  document.getElementById('cancelRouteBtn').style.display = 'none';
  if (destMarker) { map.removeLayer(destMarker); destMarker = null; }
  endInput.value = '';
  showToast('Route cancelled. Pick a new destination.');
});

document.getElementById('fab').addEventListener('click', () => {
  window.location.href = 'html/ai.html';
});

async function showNearbyADAMarkers() {
  if (!dest) return;
  const [lat, lng] = dest;
  try {
    const res = await fetch(`https://atharvcapricorn07-pathable.hf.space/markers?lat=${lat}&lng=${lng}&radius=804`);
    const markers = await res.json();
    markers.forEach(markerData => {
      const icon = L.divIcon({
        className: 'custom-marker-label',
        html: `<div class="marker-label">${markerData.label}</div>`,
        iconSize: [100, 40],
        iconAnchor: [50, 20]
      });
      const marker = L.marker([markerData.lat, markerData.lng], { icon }).addTo(map);
      marker.bindPopup(`
        <img src="${markerData.image_url}" style="width:150px;height:150px;object-fit:cover;border-radius:8px;display:block;" />
        <div style="text-align:center;margin-top:6px;font-weight:bold;">${markerData.label}</div>
      `, { maxWidth: 170 });
      adaMarkers.push(marker);
    });
  } catch (e) { console.error('Failed to load ADA markers:', e); }
}