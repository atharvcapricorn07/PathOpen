// --- Setup ---
let start = null, dest = null, routeControl;
let routeActive = false;
let adaMarkers = [];

// --- Anonymous device tracking (no login, just a local ID) ---
function getDeviceId() {
  let id = localStorage.getItem('pathable_device_id');
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem('pathable_device_id', id);
  }
  return id;
}

fetch('https://atharvcapricorn07-pathopen.hf.space/track', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ device_id: getDeviceId() })
}).catch(() => {}); // fire-and-forget, don't block the app if this fails

// --- Elements (declared first so geolocation callback can reference them) ---
const startInput = document.getElementById('startSearch');
const endInput = document.getElementById('endSearch');
const box = document.getElementById('suggestions');
const wrapper = box.parentNode;
let lastSuggestions = [];
let activeInput = null;

// Start on a world view instead of hardcoding a city — we don't know
// where the user is yet, and zooming into Seattle for someone in
// Germany is just confusing. We fly/zoom into their actual location
// (or a typed start point) once we know it.
const WORLD_VIEW = { center: [20, 0], zoom: 2 };
const map = L.map('map', { worldCopyJump: true }).setView(WORLD_VIEW.center, WORLD_VIEW.zoom);
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
  subdomains: 'abcd',
  maxZoom: 19
}).addTo(map);

// Gentle auto-rotation across the globe while we're waiting on a location —
// purely decorative, stops the instant we have somewhere real to go.
let idleSpinTimer = null;
function startIdleSpin() {
  if (idleSpinTimer) return;
  idleSpinTimer = setInterval(() => {
    if (map.getZoom() > WORLD_VIEW.zoom + 0.5) return; // user has interacted/zoomed in, stop nudging
    const c = map.getCenter();
    let lng = c.lng + 6;
    if (lng > 180) lng -= 360;
    map.panTo([c.lat, lng], { animate: true, duration: 1.4, easeLinearity: 1 });
  }, 1500);
}
function stopIdleSpin() {
  clearInterval(idleSpinTimer);
  idleSpinTimer = null;
}
startIdleSpin();

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

// Obstacle-type labels get a coral "avoid this" treatment on the map
// instead of the standard blue "helpful feature" styling.
function isObstacleLabel(label) {
  return label === "a blocked path";
}

const GEOAPIFY_KEY = '6445d99b338a472c939c735ab3798aba';

async function reverseGeocode(lat, lon) {
  try {
    const res = await fetch(
      `https://api.geoapify.com/v1/geocode/reverse?lat=${lat}&lon=${lon}&apiKey=${GEOAPIFY_KEY}`
    );
    const data = await res.json();
    return data.features?.[0]?.properties?.formatted || '';
  } catch(e) {
    console.error('reverseGeocode failed:', e);
    return '';
  }
}

// --- Geocode: Places API first (for businesses), then Geoapify's
// Address Autocomplete endpoint (NOT the plain geocode/search endpoint —
// search assumes a complete, well-formed address; autocomplete is built
// specifically for partial, as-you-type input, which is what a typeahead
// dropdown actually needs). ---
async function geocode(q, signal) {
  const KEY = GEOAPIFY_KEY;

  // If we have user location, try Places API first — much better for business name searches
  if (start) {
    const [lat, lon] = start;
    const placesUrl = `https://api.geoapify.com/v2/places?categories=catering,commercial,service,tourism,entertainment,education,healthcare,public_transport&filter=circle:${lon},${lat},50000&bias=proximity:${lon},${lat}&limit=10&name=${encodeURIComponent(q)}&apiKey=${KEY}`;

    try {
      const placesRes = await fetch(placesUrl, { signal });
      if (placesRes.ok) {
        const placesData = await placesRes.json();
        if (placesData.features.length > 0) {
          return placesData.features.map(f => ({
            display_name: f.properties.formatted || f.properties.name,
            lat: f.properties.lat,
            lon: f.properties.lon
          }));
        }
      }
    } catch(e) {
      if (e.name === 'AbortError') throw e; // let the caller handle cancellation
      console.error('Places API failed, falling back to geocoding:', e);
    }
  }

  // Address Autocomplete endpoint — handles partial input ("123 Main" instead
  // of the full formatted address), which is the whole point of a typeahead.
  let url = `https://api.geoapify.com/v1/geocode/autocomplete?text=${encodeURIComponent(q)}&limit=10&apiKey=${KEY}`;
  if (start) {
    const [lat, lon] = start;
    url += `&filter=circle:${lon},${lat},50000&bias=proximity:${lon},${lat}`;
  }

  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error('Geocode failed');
  const data = await res.json();

  // If circle filter returned nothing, retry without it (still autocomplete,
  // just not restricted to the 50km radius around the user)
  if (data.features.length === 0 && start) {
    const [lat, lon] = start;
    const fallback = await fetch(
      `https://api.geoapify.com/v1/geocode/autocomplete?text=${encodeURIComponent(q)}&limit=10&apiKey=${KEY}&bias=proximity:${lon},${lat}`,
      { signal }
    );
    if (!fallback.ok) throw new Error('Geocode failed');
    const fallbackData = await fallback.json();
    return fallbackData.features.map(f => ({
      display_name: f.properties.formatted,
      lat: f.properties.lat,
      lon: f.properties.lon
    }));
  }

  return data.features.map(f => ({
    display_name: f.properties.formatted,
    lat: f.properties.lat,
    lon: f.properties.lon
  }));
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
      stopIdleSpin();
      start = [pos.coords.latitude, pos.coords.longitude];
      map.flyTo(start, 16, { duration: 2.2 });
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
    { timeout: 10000, maximumAge: 0 }
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
      // No more hardcoded Seattle fallback — just stop the idle spin and
      // leave the user wherever the globe happened to be facing.
      stopIdleSpin();
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

// Re-populate start address when page becomes visible again
window.addEventListener('pageshow', () => {
  if (navigator.permissions) {
    navigator.permissions.query({ name: 'geolocation' }).then(result => {
      if (result.state === 'granted' && !startInput.value) {
        requestLocation();
      }
    });
  }
});

// --- Typeahead ---
// Each input gets its own debounce timer and AbortController (via closure),
// so typing in Start and End independently doesn't interfere with each other.
[startInput, endInput].forEach(input => {
  let debounceTimer = null;
  let activeController = null;

  input.addEventListener('input', () => {
    activeInput = input;
    const q = input.value.trim();

    // Cancel whatever's still in flight from a previous keystroke — this is
    // what was causing the "30 second lag": old, slower requests resolving
    // AFTER newer ones and overwriting the dropdown with stale results.
    if (activeController) activeController.abort();
    clearTimeout(debounceTimer);

    if (q.length < 3) { box.innerHTML = ''; return; }

    // Wait for a short pause in typing before actually firing the request,
    // instead of hitting the API on every single keystroke.
    debounceTimer = setTimeout(async () => {
      activeController = new AbortController();
      let results;
      try {
        results = await geocode(q, activeController.signal);
      } catch (e) {
        if (e.name === 'AbortError') return; // superseded by a newer keystroke, ignore
        box.innerHTML = '<div class="suggestion">Network error</div>';
        return;
      }

      // Guard against the (rare) case this resolves after the input changed
      // again but wasn't caught by the abort in time.
      if (input.value.trim() !== q) return;

      if (!results.length) { box.innerHTML = '<div class="suggestion">No results</div>'; return; }

      // Sort by distance from user if we have their location
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
    }, 200);
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
  stopIdleSpin();
  map.flyTo([lat, lon], 16, { duration: 1.8 });
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
  stopIdleSpin();
  map.setView(dest, map.getZoom());
  box.innerHTML = '';
});

document.getElementById('useLocationBtn').addEventListener('click', () => {
  requestLocation();
});

// --- Zoom controls ---
document.getElementById('zoomInBtn').addEventListener('click', () => map.zoomIn());
document.getElementById('zoomOutBtn').addEventListener('click', () => map.zoomOut());

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
    lineOptions: { styles: [{ color: '#2454E8', weight: 4 }] }
  }).addTo(map);
  routeActive = true;
  document.getElementById('cancelRouteBtn').style.display = 'inline-block';
  showNearbyADAMarkers();

  fetch('https://atharvcapricorn07-pathopen.hf.space/track-route', {
    method: 'POST'
  }).catch(() => {});
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

// --- Bug report modal ---
const bugOverlay = document.getElementById('bugReportOverlay');
const bugText = document.getElementById('bugReportText');

document.getElementById('bugReportBtn').addEventListener('click', () => {
  bugOverlay.classList.add('show');
  bugText.value = '';
  bugText.focus();
});

document.getElementById('bugReportCancel').addEventListener('click', () => {
  bugOverlay.classList.remove('show');
});

bugOverlay.addEventListener('click', (e) => {
  if (e.target === bugOverlay) bugOverlay.classList.remove('show');
});

document.getElementById('bugReportSubmit').addEventListener('click', async () => {
  const message = bugText.value.trim();
  if (!message) {
    showToast('Please describe the issue first.');
    return;
  }
  try {
    const res = await fetch('https://atharvcapricorn07-pathopen.hf.space/report-bug', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_id: getDeviceId(),
        message,
        page_url: window.location.href,
        user_agent: navigator.userAgent
      })
    });
    if (!res.ok) throw new Error('Request failed');
    bugOverlay.classList.remove('show');
    showToast('Thanks! Bug report submitted.');
  } catch (e) {
    showToast('Could not submit report. Try again later.');
  }
});

async function showNearbyADAMarkers() {
  if (!dest) return;

  // Clear markers from any previous route before loading the new set —
  // otherwise markers from past destinations stick around and pile up.
  adaMarkers.forEach(m => map.removeLayer(m));
  adaMarkers = [];

  const [lat, lng] = dest;
  const RADIUS_METERS = 804; // ~0.5 miles

  try {
    const res = await fetch(`https://atharvcapricorn07-pathopen.hf.space/markers?lat=${lat}&lng=${lng}&radius=${RADIUS_METERS}`);
    const markers = await res.json();

    // Client-side safety filter: only render markers actually within the radius
    // of the destination, regardless of what the backend returns. This guarantees
    // the 0.5mi limit even if server-side filtering is missing or misconfigured.
    const nearby = markers.filter(m => haversine([lat, lng], [m.lat, m.lng]) <= RADIUS_METERS);

    if (nearby.length === 0) {
      // Empty-state nudge: this is the exact moment a user discovers there's
      // no data here, so it's the highest-intent moment to ask them to add
      // the first marker themselves.
      showToast('No accessibility info here yet — tap Report Feature to add the first one.', 5000);
    }

    nearby.forEach(markerData => {
      const icon = L.divIcon({
        className: 'custom-marker-label',
        html: `<div class="marker-label${isObstacleLabel(markerData.label) ? ' is-obstacle' : ''}">${markerData.label}</div>`,
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