// Map picker for the device GPS widget (Leaflet, served locally).
// Tiles: Esri World Street Map / World Imagery (keyless). Not used:
// tile.openstreetmap.org (usage policy forbids app basemaps -> warning tiles)
// and CARTO basemaps (now return an "API KEY REQUIRED" tile).
// Place search: Nominatim (on submit only, as its usage policy asks — no
// search-as-you-type), with a descriptive Referer the browser sends for us.

const $ = (s, r = document) => r.querySelector(s);
const DEFAULT = { lat: -6.2088, lon: 106.8456, zoom: 11 }; // Jakarta

let loading = null;
function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "/lib/leaflet/leaflet.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = "/lib/leaflet/leaflet.js";
    js.onload = () => resolve(window.L);
    js.onerror = () => reject(new Error("Could not load the map library."));
    document.head.appendChild(js);
  });
  return loading;
}

export function createLocationMap({ onPick, escapeHtml }) {
  let map = null;
  let marker = null;
  let applied = null; // circle showing the location currently on the device

  function fmt(n) {
    return Number(n).toFixed(6);
  }

  function setInputs(lat, lon) {
    $("#location-latitude").value = fmt(lat);
    $("#location-longitude").value = fmt(lon);
  }

  function placeMarker(lat, lon, { pan = false, label = "" } = {}) {
    if (!map) return;
    const L = window.L;
    if (!marker) {
      marker = L.marker([lat, lon], { draggable: true, autoPan: true }).addTo(map);
      marker.on("dragend", () => {
        const p = marker.getLatLng();
        setInputs(p.lat, p.lng);
        onPick?.({ lat: p.lat, lon: p.lng, label: "" });
      });
    } else {
      marker.setLatLng([lat, lon]);
    }
    if (pan) map.setView([lat, lon], Math.max(map.getZoom(), 14));
    $("#location-pick-label").textContent = label || `${fmt(lat)}, ${fmt(lon)}`;
  }

  async function ensure() {
    const L = await loadLeaflet();
    if (map) {
      map.invalidateSize();
      return map;
    }
    L.Icon.Default.imagePath = "/lib/leaflet/images/";
    map = L.map("location-map", { zoomControl: true, attributionControl: true, worldCopyJump: true });
    const esri = (layer) =>
      L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/${layer}/MapServer/tile/{z}/{y}/{x}`, {
        maxZoom: 19,
        attribution:
          'Tiles &copy; <a href="https://www.esri.com" target="_blank" rel="noopener">Esri</a> &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
      });
    const street = esri("World_Street_Map").addTo(map);
    L.control.layers({ Map: street, Satellite: esri("World_Imagery") }, null, { position: "topright" }).addTo(map);
    map.setView([DEFAULT.lat, DEFAULT.lon], DEFAULT.zoom);
    map.on("click", (e) => {
      placeMarker(e.latlng.lat, e.latlng.lng);
      setInputs(e.latlng.lat, e.latlng.lng);
      onPick?.({ lat: e.latlng.lat, lon: e.latlng.lng, label: "" });
    });
    // The drawer animates open; re-measure once it has its final size.
    setTimeout(() => map.invalidateSize(), 250);
    return map;
  }

  function showApplied(loc) {
    if (!map) return;
    const L = window.L;
    if (applied) applied.remove();
    applied = null;
    if (!loc) return;
    applied = L.circleMarker([loc.latitude, loc.longitude], {
      radius: 7,
      color: "#fff",
      weight: 2,
      fillColor: "#2da44e",
      fillOpacity: 1,
    })
      .bindTooltip(`On device: ${escapeHtml(loc.label || "Custom")}`)
      .addTo(map);
  }

  async function show(loc) {
    try {
      await ensure();
    } catch (err) {
      $("#location-map").innerHTML = `<p class="hint map-error">${escapeHtml(err.message)}</p>`;
      return;
    }
    showApplied(loc);
    if (loc) {
      map.setView([loc.latitude, loc.longitude], 14);
      placeMarker(loc.latitude, loc.longitude, { label: loc.label });
    }
  }

  async function search(q) {
    const list = $("#location-results");
    if (!q.trim()) return;
    list.hidden = false;
    list.innerHTML = `<p class="hint">Searching…</p>`;
    try {
      const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&accept-language=id,en&q=${encodeURIComponent(q)}`;
      const res = await fetch(url, { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`Search failed (${res.status}).`);
      const rows = await res.json();
      if (!rows.length) {
        list.innerHTML = `<p class="hint">No places found.</p>`;
        return;
      }
      list.innerHTML = rows
        .map(
          (r, i) => `<button type="button" class="loc-result" data-i="${i}">
            <strong>${escapeHtml(r.name || r.display_name.split(",")[0])}</strong>
            <small>${escapeHtml(r.display_name)}</small></button>`,
        )
        .join("");
      list.onclick = (e) => {
        const btn = e.target.closest("[data-i]");
        if (!btn) return;
        const r = rows[Number(btn.dataset.i)];
        const lat = Number(r.lat);
        const lon = Number(r.lon);
        const label = (r.name || r.display_name.split(",")[0]).slice(0, 60);
        placeMarker(lat, lon, { pan: true, label });
        setInputs(lat, lon);
        onPick?.({ lat, lon, label });
        list.hidden = true;
      };
    } catch (err) {
      list.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
    }
  }

  function bind() {
    $("#location-search")?.addEventListener("submit", (e) => {
      e.preventDefault();
      search($("#location-query").value);
    });
    for (const id of ["#location-latitude", "#location-longitude"]) {
      $(id)?.addEventListener("change", () => {
        const lat = Number($("#location-latitude").value);
        const lon = Number($("#location-longitude").value);
        if (Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
          placeMarker(lat, lon, { pan: true });
          onPick?.({ lat, lon, label: "" });
        }
      });
    }
  }

  bind();

  return {
    show,
    showApplied,
    focus(lat, lon, label) {
      if (!map) return;
      placeMarker(lat, lon, { pan: true, label });
    },
  };
}
