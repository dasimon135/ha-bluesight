/**
 * BlueSight Card
 * -----------------------------------------------------------------------------
 * A vanilla-JS Lovelace custom card that visualises the state exposed by the
 * BlueSight integration:
 *
 *   - Per ESPHome/Bluetooth proxy: a tile with a "used/total" count and a
 *     vertical rack of slots -- one row per slot, a "pip" on the left (filled
 *     = used, empty = free) and, beside it, the device holding that slot.
 *     Auto-discovered from any `sensor.*_slots_used` entity that carries a
 *     `total` attribute. With `show_devices: false` the same pips are drawn
 *     as a single horizontal row and nobody is named: a denser tile for a
 *     fleet the rack would make too tall.
 *   - With `layout: rings` the same proxy is drawn as a ring of slots, one
 *     segment each, coloured by how well the device holding it is heard, under
 *     a strip that sums the fleet up. The devices follow as tiles, the ones to
 *     look at first on top, each with its day as a row of bars.
 *   - A global incident banner driven by `binary_sensor.bluesight_incident`.
 *     Each incident is rendered as a coloured badge (red for deadlock/ghost
 *     slot, amber for storm) with its kind, address and detail.
 *
 * No build step, no Lit, no external imports -- a single self-contained file
 * that HA can serve as a `module` dashboard resource.
 *
 * Config (all optional):
 *   type: custom:bluesight-card
 *   proxies: [sensor.foo_slots_used, ...]   # override auto-discovery
 *   incident_entity: binary_sensor.bluesight_incident
 *   title: BlueSight                        # card header text
 *   show_devices: true                      # false = one row of pips, no names
 *   layout: full                            # `tile` = one line, tap for a popup;
 *                                           # `rings` = one ring per proxy
 *   tile_tap: popup                         # `more-info` = HA's own dialog
 *   bounce_threshold: 5                     # rings: proxy changes in 24 h that outline a device
 *   popup_layout: full                      # `rings` = what the one-line card's popup shows
 */

// Kept equal to `manifest.json`'s version by tests/test_card_locale.py. The
// card has no build step, so there is nowhere to inject the real version at
// package time; a checked constant is the cheap way to make the drift loud.
const CARD_VERSION = "0.12.0";

// eslint-disable-next-line no-console
console.info(
  `%c BLUESIGHT-CARD %c v${CARD_VERSION} `,
  "color: white; background: #03a9f4; font-weight: 700;",
  "color: #03a9f4; background: white; font-weight: 700;"
);

const DEFAULT_INCIDENT_ENTITY = "binary_sensor.bluesight_incident";

// The fleet-wide connections sensor, where each device's connections per hour
// are published. Found through the entity registry when there is one (its id
// follows the user's language); this is the English install's id.
const DEFAULT_CONNECTIONS_ENTITY = "sensor.bluesight_connections_24h";

// How many device tiles `layout: rings` draws before "show the others".
const DEVICE_TILES_SHOWN = 8;

// Proxy changes in a day from which a device's tile gets an amber outline.
const DEFAULT_BOUNCE_THRESHOLD = 5;

// Incident kinds that are considered critical (red). Anything else is amber.
//
// The criterion, so the next kind added has one instead of a precedent: red is
// for a fault that WASTES A SCARCE RESOURCE -- a connection slot held for
// nothing. A proxy has a handful of slots and every one of them is spent on a
// device that is not there, which is why those two are red.
//
// A device that cannot connect is bad, and differently bad: it holds no slot,
// takes nothing from anything else, and waits for a person. `bond_lost` is
// therefore deliberately NOT in this set. A pairing problem also tends to hit
// several devices at once, so putting it here would turn the whole card red at
// once -- and a card that is always red has stopped saying anything.
const CRITICAL_KINDS = new Set(["deadlock", "ghost_slot"]);

// States in which the incident sensor is not answering. Separate from the
// proxy-reachability rule in `_reachability`, which is about a proxy and has a
// single reader by design (tests/test_card_locale.py pins that); this one is
// about the diagnostic itself, and is what turns the tile's dot grey.
const SENSOR_SILENT_STATES = new Set(["unavailable", "unknown"]);

// An SVG node with its attributes. A class goes through `setAttribute` like any
// other attribute: `className` is a read-only object on an SVG element, and
// assigning to it throws in strict mode.
function svgElement(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const key of Object.keys(attrs)) el.setAttribute(key, String(attrs[key]));
  return el;
}

// A circular arc as an SVG path, angles in degrees measured clockwise from
// twelve o'clock. Used by the rings layout; pure, so it is easy to reason about.
function arcPath(cx, cy, radius, from, to) {
  const point = (angle) => {
    const t = ((angle - 90) * Math.PI) / 180;
    return [cx + radius * Math.cos(t), cy + radius * Math.sin(t)];
  };
  const [x0, y0] = point(from);
  const [x1, y1] = point(to);
  const large = to - from > 180 ? 1 : 0;
  return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${radius} ${radius} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}

// Which of a proxy device's BlueSight entities the card reads, by the
// translation key the backend gives each one. The key is the entity's
// identity in every language; its id and its name are not.
const REGISTRY_ROLES = {
  slots_used: "slots",
  online: "online",
  last_device_seen: "lastSeen",
  saturation_24h: "saturation",
};

// ---------------------------------------------------------------------------
// Locale
// ---------------------------------------------------------------------------
//
// The card renders in the VIEWER's profile language (`hass.language`), not the
// installation's (`hass.config.language`). This is the only BlueSight surface
// where two people can legitimately be looking at the same dashboard in two
// languages at once. `incident.detail` is the exception: it arrives already
// rendered in the installation language, because it is a published attribute
// that user automations format push notifications from, so it is never
// re-translated here.
//
// The catalogue is the very file the backend reads, served out of the
// directory this card is served from -- one source of truth for both sides,
// and no HTTP registration beyond the static path that already exists.

// `?v=` is a cache-buster, not a parameter: the static path serves the whole
// `www` directory with `cache_headers=True`, which is `max-age=2678400` -- 31
// days. The card module itself escapes that because its Lovelace resource URL
// already carries the integration version, so an upgrade fetches a new module
// from a new URL; the catalogue had no such query and would have stayed
// cached for a month behind a card asking it for this release's keys, which
// degrades to raw keys -- the exact failure the fallback cascade exists to
// prevent, arriving silently and late.
const localeUrl = (language) =>
  `/bluesight/locale/incidents.${language}.json?v=${CARD_VERSION}`;

// Resolved catalogues, by base language. A language we do not ship resolves to
// an empty object rather than staying absent, so a 404 is remembered and not
// re-requested (and not re-logged) on every render.
const CATALOGUES = new Map();

// In-flight requests, by base language: every card on the dashboard shares one
// fetch per language.
const CATALOGUE_REQUESTS = new Map();

// Last-resort English, embedded so a card whose fetch fails -- a stale cached
// module, a reverse proxy that does not pass /bluesight/, an offline PWA --
// still renders words rather than raw keys. The card must never render worse
// than it did before it was translated.
//
// Kept byte-identical to the `card.*` half of the shipped English catalogue by
// tests/test_card_locale.py: change one and the test tells you to change the
// other.
const EMBEDDED_EN = {
  "card.proxies.empty": "No BlueSight proxies found. Once the integration is set up, sensor.<proxy>_slots_used entities appear automatically. You can also list them explicitly with the `proxies:` config option.",
  "card.proxy.missing": "missing",
  "card.proxy.offline": "offline",
  "card.proxy.scan_only": "scan only — no connection slots",
  "card.proxy.unknown_device": "unknown to Home Assistant",
  "card.slot.signal": "{rssi} dBm · {quality}",
  "card.slot.signal_hint": "Signal heard just before the connection opened",
  "card.signal.good": "good",
  "card.signal.fair": "fair",
  "card.signal.weak": "weak",
  "card.slot.stronger": "{proxy} heard it at {stronger}",
  "card.slot.stronger_full": "{proxy} heard it at {stronger}, but was full",
  "card.slot.connections.one": "{count} connection in 24 h",
  "card.slot.connections.other": "{count} connections in 24 h",
  "card.slot.proxy_changes.one": "{count} proxy change",
  "card.slot.proxy_changes.other": "{count} proxy changes",
  "card.proxy.last_advert": "last advert {age} ago",
  "card.proxy.last_advert_with_devices.one": "last advert {age} ago · {count} device seen",
  "card.proxy.last_advert_with_devices.other": "last advert {age} ago · {count} devices seen",
  "card.incidents.none": "No incidents",
  "card.incidents.one": "{count} incident",
  "card.incidents.other": "{count} incidents",
  "card.incidents.no_detail": "Incident active (no detail available)",
  "card.incidents.sensor_missing": "Incident sensor {entity} not found.",
  "card.tile.slots.one": "{used}/{total} slot",
  "card.tile.slots.other": "{used}/{total} slots",
  "card.tile.no_data": "No diagnostic data",
  "card.fleet.proxies.one": "{count} proxy",
  "card.fleet.proxies.other": "{count} proxies",
  "card.fleet.weak.one": "{count} weak signal",
  "card.fleet.weak.other": "{count} weak signals",
  "card.ring.saturation": "full {percent}% of the day",
  "card.ring.label": "{proxy}: {used} of {total} slots",
  "card.devices.heading": "Devices",
  "card.devices.via": "via {proxy}",
  "card.devices.more.one": "Show the {count} other device",
  "card.devices.more.other": "Show the {count} other devices",
  "card.devices.less": "Show fewer",
  "card.detail.back": "Back",
  "card.detail.heading": "Which proxy held it",
  "card.proxy_detail.heading": "Who held a slot",
  "card.proxy_detail.empty": "No device held a slot on this proxy in this period.",
  "card.proxy_detail.more.one": "and {count} more device",
  "card.proxy_detail.more.other": "and {count} more devices",
  "card.detail.held_by": "held by {proxy}",
  "card.detail.connections.one": "connection",
  "card.detail.connections.other": "connections",
  "card.detail.changes.one": "proxy change",
  "card.detail.changes.other": "proxy changes",
  "card.detail.proxies_used.one": "proxy used",
  "card.detail.proxies_used.other": "proxies used",
  "card.detail.loading": "Loading…",
  "card.detail.error": "The timeline could not be loaded.",
  "card.detail.empty": "No connection recorded in this period.",
  "card.detail.truncated": "Only the most recent stays are kept for a device that reconnects this often.",
  "card.detail.unobserved": "Not watched yet",
  "card.detail.ago": "{hours} h ago",
  "card.detail.now": "now",
  "card.dialog.close": "Close",
  "card.editor.title": "Title",
  "card.editor.layout": "Layout",
  "card.editor.layout_helper": "Not set: the full card.",
  "card.editor.layout_full": "Full card",
  "card.editor.layout_tile": "One line",
  "card.editor.layout_rings": "Rings, one per proxy",
  "card.editor.popup_layout": "Popup shows (one-line layout)",
  "card.editor.popup_layout_helper": "What the popup shows when you tap the one-line card. Not set: the full card.",
  "card.editor.bounce_threshold": "Proxy changes before a device is outlined",
  "card.editor.bounce_threshold_helper": "Rings layout: a device that changed proxy at least this often in 24 h gets an amber outline. Display only, nothing is raised. Not set: 5.",
  "card.editor.show_devices": "Name the device in each slot",
  "card.editor.show_devices_helper": "Off: one row of slots per proxy, without names. Shorter for a large fleet.",
  "card.editor.tile_tap": "On tap (one-line layout)",
  "card.editor.tile_tap_helper": "What tapping the one-line card opens. Not set: the full card, in a popup.",
  "card.editor.tile_tap_popup": "The full card, in a popup",
  "card.editor.tile_tap_more_info": "The incident sensor's dialog",
  "card.editor.incident_entity": "Incident sensor",
  "card.incident.sources": "on {sources}",
  "card.kind.deadlock": "Deadlock",
  "card.kind.ghost_slot": "Ghost slot",
  "card.kind.storm": "Storm",
  "card.kind.bond_lost": "Missing pairing key",
  "card.kind.proxy_offline": "Proxy offline",
  "card.kind.proxy_stalled": "Proxy stalled",
  "card.kind.proxy_reboot_storm": "Proxy reboot storm",
  "card.kind.unknown": "Unknown",
  "card.age.seconds": "{value} s",
  "card.age.minutes": "{value} min",
  "card.age.hours": "{value} h",
};

// One `{placeholder}`. Mirrors `rendering._PLACEHOLDER` on the backend: the
// names are authored by us, so the class is deliberately narrow and anything
// else stays literal.
const PLACEHOLDER = /\{(\w+)\}/g;

/**
 * Base language of a Home Assistant tag: `fr-CA` and `fr_CA` are both `fr`.
 * Mirrors `Catalogue.for_language` on the backend.
 */
/**
 * `name` without a trailing `(AA:BB:CC:DD:EE:FF)`, kept whole if that is all
 * there is.
 *
 * habluetooth names an ESPHome proxy "node (MAC)", and that stays the device's
 * name until someone renames it. The node name says which proxy it is; the
 * address is noise on a card. Mirrors `device_index._without_own_mac`, except
 * that the card cannot know the proxy's source address from here, so any MAC
 * in that position goes: nothing else in a proxy's name looks like one.
 */
function withoutTrailingMac(name) {
  const stripped = name
    .replace(/\s*\((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\)\s*$/, "")
    .trim();
  return stripped || name;
}

function baseLanguage(tag) {
  if (!tag) {
    return "en";
  }
  const base = String(tag).replace(/_/g, "-").split("-")[0].toLowerCase();
  return base || "en";
}

/**
 * Fetch one language, once, ever; call `onReady` when it lands.
 *
 * Returns immediately (without calling back) for a language already resolved,
 * so a caller may invoke this on every render without piling up listeners.
 * Nothing here rejects: a missing catalogue is a degraded card, not an error,
 * and an unhandled rejection per render would fill the console.
 */
function loadCatalogue(language, onReady) {
  if (CATALOGUES.has(language)) {
    return;
  }
  let request = CATALOGUE_REQUESTS.get(language);
  if (!request) {
    request = fetch(localeUrl(language))
      .then((response) => (response.ok ? response.json() : {}))
      .catch(() => ({}))
      .then((data) => {
        CATALOGUES.set(
          language,
          data && typeof data === "object" ? data : {}
        );
        CATALOGUE_REQUESTS.delete(language);
      });
    CATALOGUE_REQUESTS.set(language, request);
  }
  request.then(onReady).catch(() => {});
}

/**
 * Substitute `{name}` placeholders in one pass over the template.
 *
 * `String.replace` with a function never rescans what it substituted, which is
 * the property that matters: parameters carry user-controlled proxy and device
 * names, so a proxy literally named `{count}` must survive verbatim instead of
 * being substituted in turn. An unknown name keeps its placeholder -- visible,
 * but legible. `hasOwnProperty` rather than `in`, so a parameter named
 * `constructor` cannot reach up the prototype chain.
 */
function interpolate(template, params) {
  const values = params || {};
  return template.replace(PLACEHOLDER, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name)
      ? String(values[name])
      : match
  );
}

/**
 * The first usable string for `key` in `language`, or `null`; see
 * `BlueSightCard._lookup`. At module level so the static editor schema, which
 * has no card instance, resolves its labels by the same three levels.
 */
function lookupKey(language, key) {
  const sources = [];
  if (language !== "en" && CATALOGUES.has(language)) {
    sources.push(CATALOGUES.get(language));
  }
  if (CATALOGUES.has("en")) {
    sources.push(CATALOGUES.get("en"));
  }
  sources.push(EMBEDDED_EN);
  for (const source of sources) {
    const value = source[key];
    if (typeof value === "string" && value.trim() !== "") {
      return value;
    }
  }
  return null;
}

class BlueSightCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    // Signature of the last render so we can skip redundant DOM rebuilds.
    this._lastSignature = null;
    this._built = false;
    // Base language we have already asked `loadCatalogue` for, so a render
    // storm subscribes once rather than once per `set hass`.
    this._requestedLanguage = null;
  }

  /**
   * Accept an (optional) config. Nothing here can throw for a user typo --
   * discovery happens lazily against `hass` so an empty config is valid.
   */
  setConfig(config) {
    this._config = config || {};
    this._lastSignature = null; // force a re-render on next hass tick
  }

  /**
   * `full` (the default), `tile` or `rings`.
   *
   * `rings` is the full card with each proxy drawn as a ring of slots instead
   * of a rack: one segment per slot, coloured by how well the device in it is
   * heard. It is opt-in -- `full` is what every stored config means.
   *
   * The tile is one line: a coloured dot, the fleet's slot count, how many
   * incidents are open. It answers "do I need to look", where the full card
   * answers "where is it" -- and tapping it opens that full card in a popup,
   * so the second question is one gesture away. `layout` is the option name
   * the other cards from this author use.
   */
  _layout() {
    const value = String((this._config || {}).layout || "").trim().toLowerCase();
    return value === "tile" || value === "rings" ? value : "full";
  }

  /**
   * The colour of the dot: `red`, `amber`, `grey` or `green`.
   *
   * `red` and `amber` are the incident feed's own criterion, unchanged: red is
   * a fault that wastes a connection slot (see CRITICAL_KINDS).
   *
   * `grey` is the state the feed never needed and the tile cannot do without:
   * the diagnostic is not answering, so the card knows nothing. A sensor that
   * is missing or unavailable is one case; `availability_degraded` -- the
   * integration saying its own picture is incomplete -- is the other. Green
   * there would be the one lie a diagnostic card must never tell.
   *
   * Degradation never hides an incident: something seen is a fact whatever the
   * gaps around it, so an open incident still colours the dot.
   */
  _severity(incidentState) {
    if (!incidentState) return "grey";
    const state = incidentState.state;
    if (SENSOR_SILENT_STATES.has(state)) return "grey";

    const attrs = incidentState.attributes || {};
    if (state === "on") {
      const incidents = Array.isArray(attrs.incidents) ? attrs.incidents : [];
      const critical = incidents.some(
        (inc) => inc && CRITICAL_KINDS.has(String(inc.kind))
      );
      return critical ? "red" : "amber";
    }
    return attrs.availability_degraded ? "grey" : "green";
  }

  /** Slots used and slots existing, summed over every discovered proxy. */
  _fleetSlots(proxyEntities, hass) {
    const states = (hass && hass.states) || {};
    let used = 0;
    let total = 0;
    for (const entityId of proxyEntities) {
      const stateObj = states[entityId];
      if (!stateObj) continue;
      const attrs = stateObj.attributes || {};
      used += this._toInt(stateObj.state, 0);
      total += this._toInt(attrs.total, 0);
    }
    return { used, total };
  }

  /**
   * Draw a rack with a name per slot (`true`, the default), or a single
   * horizontal row of bare pips (`false`)?
   *
   * Defaults to `true` because that is what the card did before the option
   * existed: a dashboard whose stored config predates it must not change
   * appearance on upgrade. `this._config` is `{}` from the constructor, so
   * this is also safe on any path that reads it before `setConfig` --
   * `getCardSize` can be one, and answering "rack" there matches what the
   * first paint will draw.
   *
   * Only an explicit false turns it off. A YAML author who quotes the value
   * (`show_devices: "false"`) means the same thing and would otherwise get a
   * truthy string, which is the one way this option could silently do the
   * opposite of what was written.
   */
  _showDevices() {
    const value = this._config.show_devices;
    if (value === undefined || value === null) {
      return true;
    }
    if (typeof value === "string") {
      return value.trim().toLowerCase() !== "false";
    }
    return Boolean(value);
  }

  /**
   * Re-render on every state change, but cheaply: we compute a small signature
   * string from the values we actually draw and bail out if it is unchanged.
   * This getter/setter must NEVER throw -- a thrown error from a card's
   * `set hass` tears down the whole dashboard view.
   */
  set hass(hass) {
    this._hass = hass;
    // The popup holds a second card; nothing else feeds it.
    if (this._dialogCard) this._dialogCard.hass = hass;
    try {
      this._render();
    } catch (err) {
      // Last-resort guard: show the error inside the card instead of blowing
      // up Lovelace.
      // eslint-disable-next-line no-console
      console.error("bluesight-card render error", err);
      this._renderFatal(err);
    }
  }

  get hass() {
    return this._hass;
  }

  // ---------------------------------------------------------------------------
  // Translation
  // ---------------------------------------------------------------------------

  /** The viewer's base language; `en` until `hass` has arrived. */
  _language() {
    return baseLanguage(this._hass && this._hass.language);
  }

  /**
   * Make sure the viewer's catalogue is on its way, without ever waiting for
   * it. The first paint draws from whatever is already loaded -- the embedded
   * English, on a cold load -- and `_onCatalogueLoaded` repaints when the
   * fetch resolves. Awaiting here would trade a translated card for a blank
   * one on every dashboard open.
   *
   * English is fetched alongside a non-English language so a half-translated
   * catalogue falls back to the SHIPPED English rather than to the embedded
   * copy, which is a snapshot and may be older than the install.
   */
  _ensureCatalogue() {
    const language = this._language();
    if (this._requestedLanguage === language) {
      return;
    }
    this._requestedLanguage = language;
    const ready = () => this._onCatalogueLoaded();
    loadCatalogue(language, ready);
    if (language !== "en") {
      loadCatalogue("en", ready);
    }
  }

  /** Repaint once a catalogue lands. Nothing awaits this, so it must not throw. */
  _onCatalogueLoaded() {
    this._lastSignature = null;
    try {
      this._render();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("bluesight-card render error", err);
      this._renderFatal(err);
    }
  }

  /**
   * The first usable string for `key`, or `null`.
   *
   * Three levels, in order: the viewer's language, the shipped English, the
   * embedded English. A blank value counts as missing at every level -- an
   * entry a translator left empty is an untranslated one, not a translation to
   * nothing. Mirrors `Catalogue.lookup` on the backend, plus the embedded map
   * as a floor the backend does not need.
   */
  _lookup(key) {
    return lookupKey(this._language(), key);
  }

  /**
   * Render `key` with `params`, in the viewer's language.
   *
   * `count` selects a plural form: `<key>.one` / `<key>.other` is tried first.
   * English and French agree on the 1-vs-rest boundary; a language that does
   * not can add its own forms without touching a caller. A key nothing
   * resolves comes back as itself -- self-diagnosing, and better than a blank.
   *
   * Mirrors `rendering.render` on the backend, deliberately: the same key with
   * the same parameters must read the same whether the backend rendered it or
   * the card did.
   */
  _t(key, params, count) {
    let template = null;
    if (typeof count === "number" && Number.isFinite(count)) {
      const suffix = Math.abs(count) === 1 ? "one" : "other";
      template = this._lookup(`${key}.${suffix}`);
    }
    if (template === null) {
      template = this._lookup(key);
    }
    if (template === null) {
      return key;
    }
    return interpolate(template, params);
  }

  // ---------------------------------------------------------------------------
  // Discovery helpers
  // ---------------------------------------------------------------------------

  /**
   * Resolve the ordered list of proxy `slots_used` entity ids to display.
   * Honours an explicit `proxies:` override, otherwise auto-discovers any
   * `sensor.*_slots_used` entity that carries a numeric-ish `total` attribute.
   */
  _discoverProxyEntities(hass) {
    if (Array.isArray(this._config.proxies) && this._config.proxies.length) {
      return this._config.proxies.slice();
    }
    const states = hass && hass.states ? hass.states : {};
    // By what the entity *is*, where the frontend has a registry to ask: Home
    // Assistant builds a new entity's id from its translated name in some
    // forty languages, so an id ending in `_slots_used` is an English install,
    // not a BlueSight sensor. An entity with no state (disabled) draws nothing.
    const registered = Object.keys(this._registryIndex(hass).bySlotSensor)
      .filter((entityId) => states[entityId])
      .sort();
    if (registered.length) {
      return registered;
    }
    // No registry (an older Home Assistant), or a backend too old to have
    // given its entities a translation key: the suffix rule, as before.
    const found = [];
    for (const entityId of Object.keys(states)) {
      if (!entityId.startsWith("sensor.") || !entityId.endsWith("_slots_used")) {
        continue;
      }
      const stateObj = states[entityId];
      const attrs = (stateObj && stateObj.attributes) || {};
      // The `total` attribute is what marks this as a BlueSight slot sensor.
      if (attrs.total === undefined || attrs.total === null) {
        continue;
      }
      found.push(entityId);
    }
    found.sort();
    return found;
  }

  /**
   * The BlueSight entities the frontend's registry knows, grouped by device.
   *
   * `bySlotSensor` maps a slots-used entity id to its device's companions
   * (`online`, `lastSeen`) and to the device id, which is where the proxy's
   * name lives. Built once per registry object: Home Assistant replaces
   * `hass.entities` when the registry changes and hands back the same object
   * otherwise, while `set hass` runs on every state change in the house.
   */
  _registryIndex(hass) {
    const entities = hass && hass.entities;
    if (this._registrySource === entities && this._registryCache) {
      return this._registryCache;
    }
    const byDevice = {};
    let connections = null;
    if (entities && typeof entities === "object") {
      for (const entityId of Object.keys(entities)) {
        const entry = entities[entityId] || {};
        if (
          entry.platform === "bluesight" &&
          entry.translation_key === "connections_24h"
        ) {
          connections = entityId;
        }
        if (entry.platform !== "bluesight" || !entry.device_id) continue;
        const role = REGISTRY_ROLES[entry.translation_key];
        if (!role) continue;
        (byDevice[entry.device_id] = byDevice[entry.device_id] || {})[role] =
          entityId;
      }
    }
    const bySlotSensor = {};
    for (const deviceId of Object.keys(byDevice)) {
      const found = byDevice[deviceId];
      if (found.slots) {
        bySlotSensor[found.slots] = { ...found, deviceId };
      }
    }
    this._registrySource = entities;
    this._registryCache = { bySlotSensor, connections };
    return this._registryCache;
  }

  /**
   * The proxy's name: its device's, as the user renamed it. Falls back to the
   * slot sensor's friendly name with its English suffix cut off, which is all
   * there is to go on without a registry.
   */
  _proxyName(stateObj, entityId) {
    const known = this._registryIndex(this._hass).bySlotSensor[entityId];
    const devices = this._hass && this._hass.devices;
    const device = known && devices ? devices[known.deviceId] : null;
    if (device && device.name_by_user) {
      // The user's own words, drawn as written.
      return String(device.name_by_user);
    }
    if (device && device.name) {
      return withoutTrailingMac(String(device.name));
    }
    const friendly =
      (stateObj && stateObj.attributes && stateObj.attributes.friendly_name) ||
      entityId;
    return (
      withoutTrailingMac(
        String(friendly).replace(/\s*Slots Used$/i, "").trim()
      ) || entityId
    );
  }

  /**
   * The proxy-health companions of a `sensor.<proxy>_slots_used` entity.
   * BlueSight names them off the same device slug, so they are derivable
   * without a second discovery pass. Either may legitimately be absent.
   */
  _healthEntities(entityId) {
    const known = this._registryIndex(this._hass).bySlotSensor[entityId];
    if (known) {
      // Through the device, so a translated entity id costs nothing. A
      // companion the registry does not list is simply absent, as before.
      return {
        online: known.online,
        lastSeen: known.lastSeen,
        saturation: known.saturation,
      };
    }
    const slug = entityId.replace(/^sensor\./, "").replace(/_slots_used$/, "");
    return {
      online: `binary_sensor.${slug}_online`,
      lastSeen: `sensor.${slug}_last_device_seen`,
      saturation: `sensor.${slug}_saturation_24h`,
    };
  }

  /**
   * Is this proxy reachable, and is its slot sensor carrying live values?
   *
   * Extracted rather than inlined in the tile because `getCardSize` needs the
   * same answer: an offline proxy draws no rack, so it contributes no rack
   * rows to the card's height. Two copies of this rule would drift, and the
   * copy that drifted would be the one nothing looks at.
   */
  _reachability(stateObj, states, health) {
    const onlineState = states[health.online];
    const unavailable =
      stateObj.state === "unavailable" || stateObj.state === "unknown";
    // The dedicated online sensor is authoritative when present; fall back to
    // the slot sensor's own availability for a pre-0.2 install.
    const offline = onlineState ? onlineState.state !== "on" : unavailable;
    return { offline, unavailable };
  }

  /** Compact "3 min" / "2 h" style age, from a seconds value. */
  _formatAge(seconds) {
    const n = this._toInt(seconds, -1);
    if (n < 0) {
      return null;
    }
    if (n < 90) {
      return this._t("card.age.seconds", { value: String(n) });
    }
    if (n < 5400) {
      return this._t("card.age.minutes", { value: String(Math.round(n / 60)) });
    }
    return this._t("card.age.hours", { value: String(Math.round(n / 3600)) });
  }

  _toInt(value, fallback = 0) {
    const n = parseInt(value, 10);
    return Number.isFinite(n) ? n : fallback;
  }

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------

  _render() {
    const hass = this._hass;
    if (!hass) {
      return;
    }
    this._ensureCatalogue();

    const proxyEntities = this._discoverProxyEntities(hass);
    const incidentEntity =
      this._config.incident_entity || DEFAULT_INCIDENT_ENTITY;
    const incidentState = hass.states ? hass.states[incidentEntity] : undefined;
    const title = this._config.title || "BlueSight";

    // Build a compact signature of everything we draw so we can skip identical
    // re-renders (HA fires `set hass` very frequently).
    const signature = this._computeSignature(
      proxyEntities,
      hass,
      incidentEntity,
      incidentState,
      title
    );
    if (this._built && signature === this._lastSignature) {
      return;
    }
    this._lastSignature = signature;

    if (this._layout() === "tile") {
      this._renderTile(title, proxyEntities, hass, incidentState);
      return;
    }

    const layout = this._layout();
    if (!this._built || this._builtLayout !== layout) {
      this._buildSkeleton(layout);
      this._built = true;
      this._builtLayout = layout;
    }

    this._headerEl.textContent = title;
    if (this._fleetEl) this._renderFleet(proxyEntities, hass);
    this._renderProxies(proxyEntities, hass);
    if (this._devicesEl) this._renderDevices(proxyEntities, hass);
    this._renderIncidents(incidentEntity, incidentState);
  }

  /**
   * One row: dot, name, slot count, incident count.
   *
   * Rebuilt whole on every changed signature rather than patched in place --
   * it is four nodes, and the skeleton-and-patch dance the full card needs for
   * its dozens of rows would buy nothing here.
   */
  _renderTile(title, proxyEntities, hass, incidentState) {
    const severity = this._severity(incidentState);
    const { used, total } = this._fleetSlots(proxyEntities, hass);

    const style = document.createElement("style");
    style.textContent = this._css();

    const card = document.createElement("ha-card");
    const row = document.createElement("div");
    row.className = "tile " + severity;
    // A bare div takes no focus, so without these two the keydown listener and
    // the `:focus-visible` rule below are both unreachable.
    row.setAttribute("role", "button");
    row.setAttribute("tabindex", "0");
    this._tileRow = row;

    const dot = document.createElement("span");
    dot.className = "dot " + severity;
    row.appendChild(dot);

    const name = document.createElement("span");
    name.className = "tile-name";
    name.textContent = title;
    row.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "tile-meta";
    meta.textContent = this._tileSummary(severity, used, total, incidentState);
    row.appendChild(meta);

    card.appendChild(row);
    this.shadowRoot.innerHTML = "";
    this.shadowRoot.appendChild(style);
    this.shadowRoot.appendChild(card);

    row.addEventListener("click", () => this._onTileTap());
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this._onTileTap();
      }
    });

    this._built = true;
    this._builtLayout = "tile";
  }

  /** The right-hand half of the line: slots, then what the dot means. */
  _tileSummary(severity, used, total, incidentState) {
    const slots = this._t("card.tile.slots", { used, total }, total);
    if (severity === "grey") {
      return `${slots} · ${this._t("card.tile.no_data")}`;
    }
    if (severity === "green") {
      return `${slots} · ${this._t("card.incidents.none")}`;
    }
    const attrs = (incidentState && incidentState.attributes) || {};
    const incidents = Array.isArray(attrs.incidents) ? attrs.incidents : [];
    const count =
      attrs.incident_count !== undefined
        ? this._toInt(attrs.incident_count, incidents.length)
        : incidents.length;
    return `${slots} · ${this._t("card.incidents", { count: String(count) }, count)}`;
  }

  /**
   * What tapping the line does: open the full card in a popup (the default),
   * or Home Assistant's own dialog for the incident sensor.
   */
  _onTileTap() {
    if (this._config.tile_tap === "more-info") {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: {
            entityId: this._config.incident_entity || DEFAULT_INCIDENT_ENTITY,
          },
          bubbles: true,
          composed: true,
        })
      );
      return;
    }
    this._openCardDialog();
  }

  /** What the one-line card's popup shows: `full` (the default) or `rings`. */
  _popupLayout() {
    const value = String((this._config || {}).popup_layout || "")
      .trim()
      .toLowerCase();
    return value === "rings" ? "rings" : "full";
  }

  /**
   * The self-contained modal every popup of this card is built on, with no
   * external dependency. Mounted on document.body by the caller so it is never
   * clipped by the card's grid cell; Home Assistant's theme variables inherit
   * through the shadow root.
   *
   * The scroll is this card's own problem and not the other cards': a fleet of
   * eight proxies is a rack far taller than a phone screen, so the wrapper
   * scrolls rather than overflowing off the viewport.
   *
   * `withCardStyles` adds this card's stylesheet to the shadow root, for a
   * popup that draws its own markup (the device detail) rather than hosting a
   * card that carries its own.
   */
  _dialogShell(ariaLabel, withCardStyles = false) {
    const host = document.createElement("div");
    const sr = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
      .scrim { position:fixed; inset:0; z-index:1000; display:grid; place-items:center;
        box-sizing:border-box; padding:16px; background:rgba(0,0,0,.5);
        animation:bsfade .15s ease; }
      @keyframes bsfade { from{opacity:0} to{opacity:1} }
      .wrap { position:relative; width:100%; max-width:480px; max-height:90vh;
        box-sizing:border-box; padding:12px 12px 0 0; overflow:auto; }
      .x { position:absolute; top:0; right:0; z-index:1; width:34px; height:34px;
        border-radius:50%; border:none; cursor:pointer; font-size:17px; line-height:1;
        display:grid; place-items:center;
        background:var(--card-background-color,#fff); color:var(--primary-text-color,#222);
        box-shadow:0 2px 10px rgba(0,0,0,.35); }
      .x:focus-visible { outline:2px solid var(--primary-color,#03a9f4); outline-offset:2px; }
      @media (prefers-reduced-motion: reduce) { .scrim { animation:none } }
    `;

    const scrim = document.createElement("div");
    scrim.className = "scrim";
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-label", ariaLabel);
    const closeButton = document.createElement("button");
    closeButton.className = "x";
    closeButton.setAttribute("aria-label", this._t("card.dialog.close"));
    closeButton.textContent = "✕";

    scrim.appendChild(wrap);
    sr.appendChild(style);
    if (withCardStyles) {
      const cardStyle = document.createElement("style");
      cardStyle.textContent = this._css();
      sr.appendChild(cardStyle);
    }
    sr.appendChild(scrim);
    return { host, sr, scrim, wrap, closeButton };
  }

  /**
   * The full card in a popup, for the one-line layout.
   *
   * The inner card is told who opened it (`_owner`), so that a tap on one of its
   * devices changes the screen of this popup instead of opening a second one on
   * top: one dialog, two screens, a "back" between them.
   */
  _openCardDialog() {
    if (this._dialog) return;
    // With the card's stylesheet: the inner card carries its own, but the device
    // detail that replaces it is markup of this popup's, and without these rules
    // it would be bare text.
    const { host, scrim, wrap, closeButton } = this._dialogShell(
      this._config.title || "BlueSight",
      true
    );

    const card = document.createElement("bluesight-card");
    card._owner = this;
    card.setConfig({ ...this._config, layout: this._popupLayout() });
    card.hass = this._hass;

    wrap.appendChild(closeButton);
    wrap.appendChild(card);

    const close = () => this._closeCardDialog();
    // A click on the scrim or on the empty area of the wrapper closes. It is
    // decided by what was hit, not by what the click passed through: the
    // "back" button removes the detail from the tree before the click reaches
    // this listener, so a test of the path would find it outside everything.
    scrim.addEventListener("click", (event) => {
      if (event.target === scrim || event.target === wrap) close();
    });
    closeButton.addEventListener("click", close);
    this._dialogKey = (event) => {
      if (event.key !== "Escape") return;
      // One step back first: a person on the detail screen expects Escape to
      // return to the list, not to lose both.
      if (this._detailPanel) this._leaveDetail();
      else close();
    };
    window.addEventListener("keydown", this._dialogKey);
    document.body.appendChild(host);
    this._dialog = host;
    this._dialogCard = card;
    this._dialogParts = { wrap, card };
    // Otherwise the next Tab lands on whatever sits behind the scrim.
    closeButton.focus();
  }

  _closeCardDialog() {
    if (this._dialogKey) window.removeEventListener("keydown", this._dialogKey);
    const wasOpen = Boolean(this._dialog);
    if (this._dialog) this._dialog.remove();
    this._dialog = null;
    this._dialogCard = null;
    this._dialogKey = null;
    this._dialogParts = null;
    this._detailPanel = null;
    // An answer still on its way must not draw into a popup that is gone.
    this._detailToken = (this._detailToken || 0) + 1;
    // Back to where the keyboard user was. Only after a real close:
    // `disconnectedCallback` calls this too, with nothing open.
    if (wasOpen && this._tileRow) this._tileRow.focus();
  }

  // ---------------------------------------------------------------------------
  // The device detail: which proxy held it, hour by hour
  // ---------------------------------------------------------------------------

  /**
   * A device tile was tapped. Inside a one-line card's popup the detail replaces
   * the list in that same popup; anywhere else it opens a popup of its own.
   */
  _openDeviceDetail(row, origin) {
    if (this._owner) {
      this._owner._showDetailInDialog(row, origin);
      return;
    }
    this._openDetailDialog(row, origin);
  }

  /** Owner side: swap the card in this popup for the detail, with a way back. */
  _showDetailInDialog(row, origin) {
    if (!this._dialogParts || this._detailPanel) return;
    const { wrap, card } = this._dialogParts;
    this._detailOrigin = origin;
    const panel = this._detailPanelFor(row, () => this._leaveDetail());
    wrap.removeChild(card);
    wrap.appendChild(panel);
    this._detailPanel = panel;
    wrap.scrollTop = 0;
    if (panel.focusTarget) panel.focusTarget.focus();
  }

  _leaveDetail() {
    if (!this._dialogParts || !this._detailPanel) return;
    const { wrap, card } = this._dialogParts;
    wrap.removeChild(this._detailPanel);
    wrap.appendChild(card);
    this._detailPanel = null;
    this._detailToken = (this._detailToken || 0) + 1;
    wrap.scrollTop = 0;
    if (this._detailOrigin && this._detailOrigin.focus) this._detailOrigin.focus();
  }

  /** A popup of its own for the detail: the card sits on a dashboard. */
  _openDetailDialog(row, origin) {
    if (this._detailDialog) return;
    const { host, scrim, wrap, closeButton } = this._dialogShell(row.label, true);
    const panel = this._detailPanelFor(row, null);
    wrap.appendChild(closeButton);
    wrap.appendChild(panel);

    const close = () => this._closeDetailDialog();
    scrim.addEventListener("click", (event) => {
      if (event.target === scrim || event.target === wrap) close();
    });
    closeButton.addEventListener("click", close);
    this._detailKey = (event) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", this._detailKey);
    document.body.appendChild(host);
    this._detailDialog = host;
    this._detailOriginOwn = origin;
    closeButton.focus();
  }

  _closeDetailDialog() {
    if (this._detailKey) window.removeEventListener("keydown", this._detailKey);
    const wasOpen = Boolean(this._detailDialog);
    if (this._detailDialog) this._detailDialog.remove();
    this._detailDialog = null;
    this._detailKey = null;
    this._detailToken = (this._detailToken || 0) + 1;
    if (wasOpen && this._detailOriginOwn && this._detailOriginOwn.focus) {
      this._detailOriginOwn.focus();
    }
  }

  /** The detail as a card of its own, with a "back" when it replaces a list. */
  _detailPanelFor(row, back) {
    const panel = document.createElement("ha-card");
    panel.className = "detail-card";
    panel.focusTarget = null;
    if (back) {
      const bar = document.createElement("div");
      bar.className = "detail-bar";
      const button = document.createElement("button");
      button.className = "detail-back";
      button.setAttribute("type", "button");
      button.textContent = `← ${this._t("card.detail.back")}`;
      button.addEventListener("click", back);
      bar.appendChild(button);
      panel.appendChild(bar);
      panel.focusTarget = button;
    }
    panel.appendChild(
      row.kind === "proxy" ? this._buildProxyDetail(row) : this._buildDetail(row)
    );
    return panel;
  }

  /**
   * The detail of one device: its name and signal, the three numbers the tile
   * has no room for, and the day as a timeline.
   *
   * The numbers come from what the card already holds; the timeline is asked
   * for, because it changes with every connection in the house and is not worth
   * pushing to every open dashboard. A backend that cannot answer leaves the
   * rest of the detail standing.
   */
  _buildDetail(row) {
    const { entry, label, proxy } = row;
    const path = this._pathDrawn(entry.path) ? entry.path : null;
    const quality =
      path && ["good", "fair", "weak"].includes(path.quality) ? path.quality : null;
    const history = entry.history || {};

    const body = document.createElement("div");
    body.className = "detail-body";

    const title = document.createElement("div");
    title.className = "detail-title";
    title.textContent = label;
    body.appendChild(title);

    const sub = document.createElement("div");
    sub.className = quality ? `detail-sub band-${quality}` : "detail-sub";
    if (quality) sub.appendChild(this._bandBars(quality));
    const pathLine = this._renderPath(entry.path);
    const text = document.createElement("span");
    text.textContent = [
      pathLine && pathLine.textContent,
      this._t("card.detail.held_by", { proxy }),
    ]
      .filter(Boolean)
      .join(" · ");
    sub.appendChild(text);
    body.appendChild(sub);

    const figures = document.createElement("div");
    figures.className = "detail-kv";
    // A figure the card does not have shows a dash and takes the plural, which
    // is the form of "none" and "unknown" in the languages it ships.
    const number = (value) => (Number.isFinite(value) ? String(value) : "–");
    let used = null;
    for (const [value, count, key] of [
      [number(history.connections), history.connections, "card.detail.connections"],
      [number(history.proxy_changes), history.proxy_changes, "card.detail.changes"],
      ["–", undefined, "card.detail.proxies_used"],
    ]) {
      const box = document.createElement("div");
      const figure = document.createElement("b");
      figure.textContent = value;
      const caption = document.createElement("span");
      caption.textContent = this._t(key, {}, Number.isFinite(count) ? count : 2);
      box.appendChild(figure);
      box.appendChild(caption);
      figures.appendChild(box);
      used = { value: figure, caption };
    }
    body.appendChild(figures);

    const heading = document.createElement("div");
    heading.className = "devices-heading";
    heading.textContent = this._t("card.detail.heading");
    body.appendChild(heading);

    const timeline = document.createElement("div");
    timeline.className = "detail-timeline";
    body.appendChild(timeline);

    this._detailToken = (this._detailToken || 0) + 1;
    this._loadTimeline(entry.address, timeline, used, this._detailToken);
    return body;
  }

  /** Ask the backend for the device's stays and draw them when they arrive. */
  _loadTimeline(address, host, used, token) {
    const note = (key, className) => {
      host.innerHTML = "";
      const line = document.createElement("div");
      line.className = className;
      line.textContent = this._t(key);
      host.appendChild(line);
    };
    note("card.detail.loading", "detail-note");
    const hass = this._hass;
    const call =
      hass && typeof hass.callWS === "function"
        ? hass.callWS({ type: "bluesight/device_timeline", address: String(address) })
        : Promise.reject(new Error("no WebSocket"));
    call
      .then((data) => {
        if (token !== this._detailToken) return;
        this._drawTimeline(host, used, data || {});
      })
      .catch(() => {
        if (token !== this._detailToken) return;
        note("card.detail.error", "detail-note");
      });
  }

  /**
   * One bar for the window, a stay drawn in its proxy's colour at its place on
   * the line. A stretch the integration had not been watching yet is hatched,
   * and a gap is a gap: nothing held the device then.
   */
  _drawTimeline(host, figure, data) {
    host.innerHTML = "";
    const stays = Array.isArray(data.stays) ? data.stays : [];
    const proxies = Array.isArray(data.proxies) ? data.proxies : [];
    if (figure) {
      figure.value.textContent = String(proxies.length);
      figure.caption.textContent = this._t(
        "card.detail.proxies_used",
        {},
        proxies.length
      );
    }

    if (!stays.length) {
      const empty = document.createElement("div");
      empty.className = "detail-note";
      empty.textContent = this._t("card.detail.empty");
      host.appendChild(empty);
      return;
    }
    const windowS = Number(data.window_s) > 0 ? Number(data.window_s) : 86400;
    const observed = Math.min(windowS, Number(data.observed_s) || windowS);

    const track = document.createElement("div");
    track.className = "detail-track";
    if (observed < windowS) {
      const unwatched = document.createElement("div");
      unwatched.className = "detail-unobserved";
      unwatched.style.left = "0%";
      unwatched.style.width = `${(((windowS - observed) / windowS) * 100).toFixed(2)}%`;
      unwatched.setAttribute("title", this._t("card.detail.unobserved"));
      track.appendChild(unwatched);
    }
    const used = new Set();
    for (const stay of stays) {
      const [from, to, index] = stay.map(Number);
      const slot = Number.isFinite(index) ? index : 0;
      used.add(slot);
      const seg = document.createElement("div");
      seg.className = `detail-seg proxy-${slot % 5}`;
      seg.style.left = `${(((windowS - from) / windowS) * 100).toFixed(2)}%`;
      // Never thinner than a hairline: a stay of a few seconds is still a stay.
      seg.style.width = `${Math.max(0.6, ((from - to) / windowS) * 100).toFixed(2)}%`;
      const span = this._formatAge(from - to);
      seg.setAttribute(
        "title",
        [proxies[slot] || "?", span].filter(Boolean).join(" · ")
      );
      track.appendChild(seg);
    }
    host.appendChild(track);

    host.appendChild(this._axisFor(windowS));

    const legend = document.createElement("div");
    legend.className = "detail-legend";
    for (const slot of [...used].sort((a, b) => a - b)) {
      const item = document.createElement("span");
      const swatch = document.createElement("i");
      swatch.className = `proxy-${slot % 5}`;
      item.appendChild(swatch);
      const name = document.createElement("span");
      name.textContent = proxies[slot] || "?";
      item.appendChild(name);
      legend.appendChild(item);
    }
    host.appendChild(legend);

    if (data.truncated) {
      const cut = document.createElement("div");
      cut.className = "detail-note";
      cut.textContent = this._t("card.detail.truncated");
      host.appendChild(cut);
    }
  }

  /** The time axis under a timeline: the start of the window, its middle, now. */
  _axisFor(windowS) {
    const hours = Math.max(1, Math.round(windowS / 3600));
    const axis = document.createElement("div");
    axis.className = "detail-axis";
    for (const text of [
      this._t("card.detail.ago", { hours: String(hours) }),
      `${Math.round(hours / 2)} h`,
      this._t("card.detail.now"),
    ]) {
      const tick = document.createElement("span");
      tick.textContent = text;
      axis.appendChild(tick);
    }
    return axis;
  }

  /**
   * The detail of one proxy: how full it is, and which devices held a slot on
   * it over the day. The other way round from a device's detail, and asked for
   * in the same way and for the same reason.
   */
  _buildProxyDetail(row) {
    const { label, source, facts } = row;
    const body = document.createElement("div");
    body.className = "detail-body";

    const title = document.createElement("div");
    title.className = "detail-title";
    title.textContent = label;
    body.appendChild(title);

    const sub = document.createElement("div");
    sub.className = "detail-sub";
    const text = document.createElement("span");
    const parts = [];
    if (facts && !facts.offline) {
      parts.push(
        this._t(
          "card.tile.slots",
          { used: String(facts.used), total: String(facts.total) },
          facts.total
        )
      );
      if (facts.saturation !== null) {
        parts.push(
          this._t("card.ring.saturation", {
            percent: String(Math.round(facts.saturation)),
          })
        );
      }
    } else {
      parts.push(this._t("card.proxy.offline"));
    }
    text.textContent = parts.join(" · ");
    sub.appendChild(text);
    body.appendChild(sub);

    const heading = document.createElement("div");
    heading.className = "devices-heading";
    heading.textContent = this._t("card.proxy_detail.heading");
    body.appendChild(heading);

    const list = document.createElement("div");
    list.className = "detail-timeline";
    body.appendChild(list);

    this._detailToken = (this._detailToken || 0) + 1;
    this._loadProxyTimeline(source, list, this._detailToken);
    return body;
  }

  /** Ask the backend who held a slot on the proxy and draw it when it arrives. */
  _loadProxyTimeline(source, host, token) {
    const note = (key) => {
      host.innerHTML = "";
      const line = document.createElement("div");
      line.className = "detail-note";
      line.textContent = this._t(key);
      host.appendChild(line);
    };
    note("card.detail.loading");
    const hass = this._hass;
    const call =
      hass && typeof hass.callWS === "function"
        ? hass.callWS({ type: "bluesight/proxy_timeline", source: String(source) })
        : Promise.reject(new Error("no WebSocket"));
    call
      .then((data) => {
        if (token !== this._detailToken) return;
        this._drawProxyTimeline(host, data || {});
      })
      .catch(() => {
        if (token !== this._detailToken) return;
        note("card.detail.error");
      });
  }

  /**
   * One line per device that held a slot, longest holders first: its name, and
   * a bar for the window with a segment for each stay. The bars share one axis,
   * so two devices can be read against each other. A device with no name Home
   * Assistant can give shows its address, which is what the rack falls back to.
   */
  _drawProxyTimeline(host, data) {
    host.innerHTML = "";
    const devices = Array.isArray(data.devices) ? data.devices : [];
    if (!devices.length) {
      const empty = document.createElement("div");
      empty.className = "detail-note";
      empty.textContent = this._t("card.proxy_detail.empty");
      host.appendChild(empty);
      return;
    }
    const windowS = Number(data.window_s) > 0 ? Number(data.window_s) : 86400;
    const observed = Math.min(windowS, Number(data.observed_s) || windowS);

    let truncated = false;
    for (const device of devices) {
      const line = document.createElement("div");
      line.className = "proxy-dev";
      const name = document.createElement("div");
      name.className = "proxy-dev-name";
      name.textContent = device.name || device.address || "?";
      line.appendChild(name);

      const track = document.createElement("div");
      track.className = "detail-track proxy-dev-track";
      if (observed < windowS) {
        const unwatched = document.createElement("div");
        unwatched.className = "detail-unobserved";
        unwatched.style.left = "0%";
        unwatched.style.width = `${(((windowS - observed) / windowS) * 100).toFixed(2)}%`;
        unwatched.setAttribute("title", this._t("card.detail.unobserved"));
        track.appendChild(unwatched);
      }
      for (const stay of Array.isArray(device.stays) ? device.stays : []) {
        const [from, to] = stay.map(Number);
        const seg = document.createElement("div");
        seg.className = "detail-seg proxy-0";
        seg.style.left = `${(((windowS - from) / windowS) * 100).toFixed(2)}%`;
        seg.style.width = `${Math.max(0.6, ((from - to) / windowS) * 100).toFixed(2)}%`;
        seg.setAttribute("title", this._formatAge(from - to) || "");
        track.appendChild(seg);
      }
      line.appendChild(track);
      host.appendChild(line);
      if (device.truncated) truncated = true;
    }
    host.appendChild(this._axisFor(windowS));

    const omitted = Number(data.omitted) || 0;
    if (omitted > 0) {
      const more = document.createElement("div");
      more.className = "detail-note";
      more.textContent = this._t(
        "card.proxy_detail.more",
        { count: String(omitted) },
        omitted
      );
      host.appendChild(more);
    }
    if (truncated) {
      const cut = document.createElement("div");
      cut.className = "detail-note";
      cut.textContent = this._t("card.detail.truncated");
      host.appendChild(cut);
    }
  }

  /** The three-bar signal glyph, lit by band (three good, one weak). */
  _bandBars(quality) {
    const bars = document.createElement("span");
    bars.className = "bars";
    const lit = { good: 3, fair: 2, weak: 1 }[quality] || 0;
    for (let i = 1; i <= 3; i += 1) {
      const bar = document.createElement("i");
      bar.className = i <= lit ? "on" : "";
      bars.appendChild(bar);
    }
    return bars;
  }

  disconnectedCallback() {
    this._closeCardDialog();
    this._closeDetailDialog();
  }

  _computeSignature(proxyEntities, hass, incidentEntity, incidentState, title) {
    // The language belongs in the signature: every drawn string depends on it,
    // and a viewer who changes their profile language moves nothing else.
    //
    // So does the layout. `setConfig` nulls `_lastSignature`, so a config edit
    // repaints without this -- but that is a promise made by a different
    // method, and this one's job is that the signature describes what is
    // drawn. Two layouts with the same signature is exactly the defect that
    // rule exists to prevent.
    const parts = [
      title,
      incidentEntity,
      this._language(),
      `rack:${this._showDevices() ? 1 : 0}`,
      `layout:${this._layout()}`,
    ];
    if (this._layout() === "rings") {
      // The tiles' sparklines come from the connections sensor, and whether the
      // list is expanded and where the flag starts change what is drawn.
      const conn = this._connectionsState(hass);
      parts.push(
        `devx:${this._devicesExpanded ? 1 : 0}`,
        `thr:${this._bounceThreshold()}`,
        `conn:${conn ? conn.last_updated : "-"}`
      );
    }
    for (const id of proxyEntities) {
      const s = hass.states ? hass.states[id] : undefined;
      if (!s) {
        parts.push(`${id}:missing`);
        continue;
      }
      const a = s.attributes || {};
      parts.push(`${id}:${s.state}:${a.total}:${a.free}`);
      // The name drawn comes from the device registry, so a rename moves
      // nothing in `states` and has to be looked at here.
      parts.push(`name:${this._proxyName(s, id)}`);
      // Which devices hold the slots, not just how many. `used/total` can sit
      // perfectly still while one device disconnects and another connects, or
      // while a device is renamed in the registry; left out here, the list
      // would keep showing the previous fleet until something else moved.
      const allocated = Array.isArray(a.allocated_devices)
        ? a.allocated_devices
        : [];
      parts.push(
        `dev:${allocated
          .map((d) => {
            const entry = d || {};
            const p = entry.path || {};
            return (
              `${entry.address}=${entry.name}` +
              `@${p.rssi}/${p.quality}/${p.stronger_name}/${p.stronger_rssi}` +
              `/${p.stronger_was_full}` +
              `#${(entry.history || {}).connections}` +
              `/${(entry.history || {}).proxy_changes}`
            );
          })
          .join(",")}`
      );
      // The health companions are drawn too, so they belong in the signature.
      const health = this._healthEntities(id);
      const online = hass.states ? hass.states[health.online] : undefined;
      const lastSeen = hass.states ? hass.states[health.lastSeen] : undefined;
      parts.push(`on:${online ? online.state : "-"}`);
      parts.push(
        `seen:${lastSeen ? this._formatAge(lastSeen.state) : "-"}:` +
          `${lastSeen ? (lastSeen.attributes || {}).device_count : "-"}`
      );
      // The ring's outer arc is the proxy's saturation, a sensor of its own.
      if (this._layout() === "rings") {
        const sat = hass.states ? hass.states[health.saturation] : undefined;
        parts.push(`sat:${sat ? sat.state : "-"}`);
      }
    }
    if (incidentState) {
      parts.push(`inc:${incidentState.state}`);
      // The tile's dot turns grey on this alone, with every other drawn value
      // standing still, so it has to be here or the colour lags a whole cycle.
      parts.push(
        `degr:${(incidentState.attributes || {}).availability_degraded ? 1 : 0}`
      );
      const incidents =
        (incidentState.attributes && incidentState.attributes.incidents) || [];
      // Length + a hash-ish join is enough to detect changes cheaply.
      parts.push(`incN:${incidents.length}`);
      for (const inc of incidents) {
        // `sources` is rendered for deadlocks, so a change of the proxy set
        // must invalidate the signature too.
        const sources = Array.isArray(inc.sources) ? inc.sources.join(",") : "";
        // Names are part of what is drawn, so a rename with everything else
        // unchanged has to invalidate the signature or the badge keeps the
        // old label until some other attribute happens to move.
        const names = Array.isArray(inc.source_names)
          ? inc.source_names.join(",")
          : "";
        parts.push(
          `${inc.kind}|${inc.address}|${inc.device_name || ""}|${inc.detail}` +
            `|${sources}|${names}`
        );
      }
    } else {
      parts.push("inc:missing");
    }
    return parts.join("~");
  }

  _buildSkeleton(layout = "full") {
    const style = document.createElement("style");
    style.textContent = this._css();

    const card = document.createElement("ha-card");

    this._headerEl = document.createElement("div");
    this._headerEl.className = "card-header";

    this._proxiesEl = document.createElement("div");
    this._proxiesEl.className = layout === "rings" ? "proxies rings" : "proxies";

    this._incidentsEl = document.createElement("div");
    this._incidentsEl.className = "incidents";

    // The fleet strip exists only under `rings`; every other layout leaves it
    // null so `_render` knows there is nothing to fill.
    this._fleetEl = null;
    card.appendChild(this._headerEl);
    if (layout === "rings") {
      this._fleetEl = document.createElement("div");
      this._fleetEl.className = "fleet";
      card.appendChild(this._fleetEl);
    }
    card.appendChild(this._proxiesEl);
    // The device tiles sit between the rings and the incidents, `rings` only.
    this._devicesEl = null;
    if (layout === "rings") {
      this._devicesEl = document.createElement("div");
      this._devicesEl.className = "devices";
      card.appendChild(this._devicesEl);
    }
    card.appendChild(this._incidentsEl);

    // Reset shadow root (handles a re-build after setConfig).
    this.shadowRoot.innerHTML = "";
    this.shadowRoot.appendChild(style);
    this.shadowRoot.appendChild(card);
  }

  _renderProxies(proxyEntities, hass) {
    const container = this._proxiesEl;
    container.innerHTML = "";

    if (!proxyEntities.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = this._t("card.proxies.empty");
      container.appendChild(empty);
      return;
    }

    for (const entityId of proxyEntities) {
      const stateObj = hass.states ? hass.states[entityId] : undefined;
      container.appendChild(
        this._layout() === "rings"
          ? this._renderProxyRing(entityId, stateObj, hass)
          : this._renderProxyTile(entityId, stateObj, hass)
      );
    }
  }

  _renderProxyTile(entityId, stateObj, hass) {
    const tile = document.createElement("div");
    tile.className = "proxy";

    const name = document.createElement("div");
    name.className = "proxy-name";

    const count = document.createElement("span");
    count.className = "proxy-count";

    if (!stateObj) {
      tile.classList.add("offline");
      name.textContent = entityId;
      count.textContent = this._t("card.proxy.missing");
      name.appendChild(count);
      tile.appendChild(name);
      return tile;
    }

    const states = (hass && hass.states) || {};
    const health = this._healthEntities(entityId);
    const { offline, unavailable } = this._reachability(stateObj, states, health);
    const attrs = stateObj.attributes || {};
    const total = this._toInt(attrs.total, 0);
    const used = this._toInt(stateObj.state, 0);

    name.textContent = this._proxyName(stateObj, entityId);
    if (offline || unavailable) {
      tile.classList.add("offline");
      count.textContent = this._t("card.proxy.offline");
    } else {
      count.textContent = `${used}/${total}`;
    }
    name.appendChild(count);
    tile.appendChild(name);

    // Proxy-health line: how long since this proxy last heard anything, and
    // how many devices it currently sees.
    const lastSeenState = states[health.lastSeen];
    if (lastSeenState && !offline) {
      const age = this._formatAge(lastSeenState.state);
      if (age !== null) {
        const meta = document.createElement("div");
        meta.className = "proxy-meta";
        const seen = this._toInt(
          (lastSeenState.attributes || {}).device_count,
          -1
        );
        meta.textContent =
          seen >= 0
            ? this._t(
                "card.proxy.last_advert_with_devices",
                { age, count: String(seen) },
                seen
              )
            : this._t("card.proxy.last_advert", { age });
        tile.appendChild(meta);
      }
    }

    // The slot area: a rack (one row per slot, the pip on the left, whoever
    // holds that slot beside it) or, under `show_devices: false`, one
    // horizontal row of the same pips and no names at all. An offline or
    // unavailable proxy draws neither -- its last known occupants are exactly
    // the thing you must not believe.
    //
    // The names arrive resolved from the backend. Re-deriving them here from
    // `hass.devices` would mean reimplementing `build_device_index`'s rule in
    // JavaScript -- which registry evidence may speak for a BLE address --
    // for no benefit. A pre-0.6.0 backend publishes no `allocated_devices` at
    // all: the rack then draws bare pips, which is what the card drew before
    // the attribute existed.
    const drawable = !offline && !unavailable;
    const allocated = drawable && Array.isArray(attrs.allocated_devices)
      ? attrs.allocated_devices
      : [];
    if (drawable) {
      if (total <= 0) {
        // A passive (non-connectable) scanner reports slots=0: it can see
        // devices but never hold a connection, so there is no rack to draw --
        // the sentence takes its place, exactly as before the rack existed.
        const hint = document.createElement("div");
        hint.className = "pip-hint";
        hint.textContent = this._t("card.proxy.scan_only");
        tile.appendChild(hint);
      }
      // Not an `else`: a scanner reporting zero slots AND an allocated address
      // is a contradiction, and the occupant is the half of it worth seeing.
      // It draws under the sentence, as an overflow row (or, with the names
      // off, as an amber pip on its own).
      const slots = this._showDevices()
        ? this._renderSlotRack(used, total, allocated)
        : this._renderSlotRow(used, total, allocated);
      if (slots) {
        tile.appendChild(slots);
      }
    }

    return tile;
  }

  // ---------------------------------------------------------------------------
  // layout: rings
  // ---------------------------------------------------------------------------

  /**
   * What the ring and the fleet strip both read about one proxy, in one place
   * so they cannot disagree about which proxies are worth believing.
   *
   * An offline or unavailable proxy draws no occupants, exactly as in the rack:
   * its last known devices are the thing you must not believe. `saturation` is
   * the share of the day (0-100) the proxy had no free slot, or `null` when the
   * sensor is missing or has not observed the proxy yet -- unknown, not zero.
   */
  _ringFacts(entityId, hass) {
    const states = (hass && hass.states) || {};
    const stateObj = states[entityId];
    if (!stateObj) {
      return { missing: true, offline: true, used: 0, total: 0, allocated: [] };
    }
    // `_drawnOccupants` is the one reader of the reachability rule besides the
    // tile, so the ring cannot believe a proxy the sizer does not.
    const drawn = this._drawnOccupants(entityId, states);
    const attrs = stateObj.attributes || {};
    const satState = states[this._healthEntities(entityId).saturation];
    const sat = satState ? Number.parseFloat(satState.state) : Number.NaN;
    return {
      missing: false,
      offline: drawn === null,
      used: this._toInt(stateObj.state, 0),
      total: this._toInt(attrs.total, 0),
      allocated: drawn || [],
      saturation: Number.isFinite(sat) ? Math.max(0, Math.min(100, sat)) : null,
    };
  }

  /**
   * The strip above the rings: how many proxies, how many slots the fleet is
   * spending (the tile's own sum), and how many devices are heard weakly.
   *
   * A count, not a verdict: nothing here raises an incident. The weak count
   * reads the band the backend already put on each connection.
   */
  _renderFleet(proxyEntities, hass) {
    const strip = this._fleetEl;
    strip.innerHTML = "";
    if (!proxyEntities.length) return;

    const { used, total } = this._fleetSlots(proxyEntities, hass);
    let weak = 0;
    for (const entityId of proxyEntities) {
      for (const device of this._ringFacts(entityId, hass).allocated) {
        const path = (device || {}).path;
        if (this._pathDrawn(path) && path.quality === "weak") weak += 1;
      }
    }
    const count = proxyEntities.length;
    const chips = [
      [null, this._t("card.fleet.proxies", { count: String(count) }, count)],
      [null, this._t("card.tile.slots", { used, total }, total)],
      [
        weak > 0 ? "weak" : "good",
        this._t("card.fleet.weak", { count: String(weak) }, weak),
      ],
    ];
    for (const [signal, text] of chips) {
      const chip = document.createElement("span");
      chip.className = "chip";
      if (signal) {
        const dot = document.createElement("span");
        dot.className = `chip-dot signal-${signal}`;
        chip.appendChild(dot);
      }
      const label = document.createElement("span");
      label.className = "chip-name";
      label.textContent = text;
      chip.appendChild(label);
      strip.appendChild(chip);
    }
  }

  /**
   * One proxy as a ring: a segment per slot, then its name, how long it was
   * full today and, unless `show_devices` is off, the devices holding it.
   */
  _renderProxyRing(entityId, stateObj, hass) {
    const facts = this._ringFacts(entityId, hass);
    const cell = document.createElement("div");
    cell.className = facts.offline ? "ring offline" : "ring";

    const name = facts.missing ? entityId : this._proxyName(stateObj, entityId);
    // A tap opens who held a slot on this proxy. It needs the proxy's `source`,
    // which is what the history is keyed by; a proxy that does not publish one
    // is a plain ring. Offline proxies stay tappable: what they held before they
    // dropped is the question an owner asks of one.
    const source = facts.missing ? "" : String((stateObj.attributes || {}).source || "");
    if (source) {
      cell.setAttribute("role", "button");
      cell.setAttribute("tabindex", "0");
      const row = { kind: "proxy", label: name, source, facts };
      const open = () => this._openDeviceDetail(row, cell);
      cell.addEventListener("click", open);
      cell.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      });
    }
    cell.appendChild(this._ringGauge(name, facts));

    const title = document.createElement("div");
    title.className = "ring-name";
    title.textContent = name;
    cell.appendChild(title);

    if (facts.missing || facts.offline) {
      const state = document.createElement("div");
      state.className = "ring-sub";
      state.textContent = this._t(
        facts.missing ? "card.proxy.missing" : "card.proxy.offline"
      );
      cell.appendChild(state);
      return cell;
    }

    if (facts.saturation !== null) {
      const sat = document.createElement("div");
      sat.className = "ring-sub";
      sat.textContent = this._t("card.ring.saturation", {
        percent: String(Math.round(facts.saturation)),
      });
      cell.appendChild(sat);
    }
    return cell;
  }

  /**
   * The gauge itself. SVG elements take their class through `setAttribute`:
   * `className` is a read-only object on them, and assigning to it throws in
   * strict mode.
   *
   * Geometry is `_slotCounts`, the rack's own, so a ring and a rack cannot
   * reach different conclusions about the same proxy -- an overflow occupant
   * still gets its segment, dashed, because dropping it would under-report how
   * many devices hold a connection.
   *
   * Colour: a held segment takes the band of the device in it (good, fair,
   * weak), or the theme's info colour when no signal was read. That is a
   * different rule from the rack's, where every held pip is green: a ring has
   * the room to say how well the slot is held, and "held" alone is what the
   * count says.
   *
   * The outer saturation arc is grey on purpose. A theme's primary colour is
   * often one of the band colours, and an arc that looks like a segment reads as
   * one more slot.
   */
  _ringGauge(name, facts) {
    const make = svgElement;
    const svg = make("svg", {
      viewBox: "0 0 80 80",
      class: "ring-gauge",
      role: "img",
      "aria-label": facts.offline
        ? `${name}: ${this._t(facts.missing ? "card.proxy.missing" : "card.proxy.offline")}`
        : this._t("card.ring.label", {
            proxy: name,
            used: String(facts.used),
            total: String(facts.total),
          }),
    });
    const START = 225; // lower left, measured clockwise from twelve o'clock
    const SWEEP = 270;

    svg.appendChild(
      make("path", { class: "track", d: arcPath(40, 40, 37, START, START + SWEEP) })
    );
    if (facts.offline) {
      svg.appendChild(this._ringCount(make, "–"));
      return svg;
    }

    if (facts.saturation !== null && facts.saturation > 0) {
      svg.appendChild(
        make("path", {
          class: "sat",
          d: arcPath(40, 40, 37, START, START + (SWEEP * facts.saturation) / 100),
        })
      );
    }

    const { slots, filled, rows } = this._slotCounts(
      facts.used,
      facts.total,
      facts.allocated
    );
    const gap = rows > 8 ? 3 : 8;
    const width = rows > 0 ? (SWEEP - gap * (rows - 1)) / rows : 0;
    for (let i = 0; i < rows; i += 1) {
      const from = START + i * (width + gap);
      const classes = ["seg"];
      if (i >= slots) {
        classes.push("overflow");
      } else if (i < filled) {
        classes.push("held");
      }
      if (i < filled) {
        const entry = facts.allocated[i] || {};
        const quality = this._pathDrawn(entry.path) ? entry.path.quality : null;
        if (["good", "fair", "weak"].includes(quality)) classes.push(quality);
      }
      svg.appendChild(
        make("path", { class: classes.join(" "), d: arcPath(40, 40, 31, from, from + width) })
      );
    }
    svg.appendChild(this._ringCount(make, rows > 0 ? `${facts.used}/${facts.total}` : "–"));
    return svg;
  }

  _ringCount(make, text) {
    const label = make("text", { class: "ring-count", x: 40, y: 46 });
    label.textContent = text;
    return label;
  }

  /** Proxy changes in a day from which a device is outlined; display only. */
  _bounceThreshold() {
    const value = parseInt(this._config.bounce_threshold, 10);
    return Number.isFinite(value) && value >= 1 ? value : DEFAULT_BOUNCE_THRESHOLD;
  }

  /** The fleet-wide connections sensor's state, or undefined without one. */
  _connectionsState(hass) {
    const states = (hass && hass.states) || {};
    const id =
      this._registryIndex(hass).connections || DEFAULT_CONNECTIONS_ENTITY;
    return states[id];
  }

  /**
   * Each listed device's connections per hour, by upper-case address.
   *
   * The sensor lists the busiest devices only, so a quiet one has no series and
   * its tile draws no sparkline -- which is also the truth about it.
   */
  _hourlyByAddress(hass) {
    const state = this._connectionsState(hass);
    const attrs = (state && state.attributes) || {};
    const listed = Array.isArray(attrs.devices) ? attrs.devices : [];
    const out = new Map();
    for (const device of listed) {
      const entry = device || {};
      if (entry.address && Array.isArray(entry.hourly)) {
        out.set(String(entry.address).toUpperCase(), entry.hourly);
      }
    }
    return out;
  }

  /**
   * The devices holding a slot, in the order a person should look at them.
   *
   * A device that changed proxy at least `bounce_threshold` times comes first
   * (most changes first), then the rest by signal, weakest band and weakest
   * figure first, then those with no reading, then by name. An offline proxy
   * contributes nothing: its last occupants are not to be believed.
   *
   * The order is for reading, not a verdict. Nothing here raises an incident.
   */
  _deviceRows(proxyEntities, hass) {
    const threshold = this._bounceThreshold();
    const band = { weak: 0, fair: 1, good: 2 };
    const rows = [];
    for (const entityId of proxyEntities) {
      const facts = this._ringFacts(entityId, hass);
      if (facts.missing || facts.offline) continue;
      const proxy = this._proxyName(hass.states[entityId], entityId);
      for (const device of facts.allocated) {
        const entry = device || {};
        const label = entry.name ? String(entry.name) : String(entry.address || "");
        if (!label) continue;
        const changes = this._toInt((entry.history || {}).proxy_changes, 0);
        const path = this._pathDrawn(entry.path) ? entry.path : null;
        rows.push({
          entry,
          label,
          proxy,
          changes,
          flagged: changes >= threshold,
          band: path && band[path.quality] !== undefined ? band[path.quality] : 3,
          rssi: path ? path.rssi : 0,
        });
      }
    }
    rows.sort(
      (a, b) =>
        Number(b.flagged) - Number(a.flagged) ||
        (a.flagged ? b.changes - a.changes : 0) ||
        a.band - b.band ||
        a.rssi - b.rssi ||
        a.label.localeCompare(b.label)
    );
    return rows;
  }

  /**
   * The tiles under the rings: one per device, the ones to look at first.
   * Skipped under `show_devices: false`, which asks for the rings alone.
   */
  _renderDevices(proxyEntities, hass) {
    const host = this._devicesEl;
    host.innerHTML = "";
    if (!this._showDevices()) return;
    const rows = this._deviceRows(proxyEntities, hass);
    if (!rows.length) return;

    const heading = document.createElement("div");
    heading.className = "devices-heading";
    heading.textContent = this._t("card.devices.heading");
    host.appendChild(heading);

    const hourly = this._hourlyByAddress(hass);
    const shown = this._devicesExpanded ? rows : rows.slice(0, DEVICE_TILES_SHOWN);
    const grid = document.createElement("div");
    grid.className = "device-tiles";
    for (const row of shown) grid.appendChild(this._renderDeviceTile(row, hourly));
    host.appendChild(grid);

    const hidden = rows.length - DEVICE_TILES_SHOWN;
    if (hidden > 0) {
      const button = document.createElement("button");
      button.className = "more";
      button.setAttribute("type", "button");
      button.textContent = this._devicesExpanded
        ? this._t("card.devices.less")
        : this._t("card.devices.more", { count: String(hidden) }, hidden);
      button.addEventListener("click", () => {
        this._devicesExpanded = !this._devicesExpanded;
        this._lastSignature = null;
        try {
          this._render();
        } catch (err) {
          // eslint-disable-next-line no-console
          console.error("bluesight-card render error", err);
          this._renderFatal(err);
        }
      });
      host.appendChild(button);
    }
  }

  /**
   * One device: a badge carrying its signal band, its name, which proxy holds
   * it and how well, and its day as a row of bars.
   *
   * The band is carried by the badge's colour and by the number of bars (three
   * for good, one for weak), so it reads without telling colours apart. The
   * whole story, including the connections line, is in the tooltip.
   */
  _renderDeviceTile(row, hourly) {
    const { entry, label, proxy } = row;
    const path = this._pathDrawn(entry.path) ? entry.path : null;
    const quality =
      path && ["good", "fair", "weak"].includes(path.quality) ? path.quality : null;

    const tile = document.createElement("div");
    tile.className = row.flagged ? "device-tile flagged" : "device-tile";
    // A tap opens the device's detail. A bare div takes no focus, so without the
    // role and the tab stop the keyboard could not reach it.
    tile.setAttribute("role", "button");
    tile.setAttribute("tabindex", "0");
    const open = () => this._openDeviceDetail(row, tile);
    tile.addEventListener("click", open);
    tile.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    const pathLine = this._renderPath(entry.path);
    const historyLine = this._renderHistory(entry.history);
    tile.setAttribute(
      "title",
      [label, pathLine && pathLine.textContent, historyLine && historyLine.textContent]
        .filter(Boolean)
        .join(" · ")
    );

    const badge = document.createElement("span");
    badge.className = quality ? `device-badge band-${quality}` : "device-badge";
    if (quality) {
      badge.setAttribute("role", "img");
      badge.setAttribute("aria-label", this._t(`card.signal.${quality}`));
    }
    badge.appendChild(this._bandBars(quality));
    tile.appendChild(badge);

    const text = document.createElement("div");
    text.className = "device-text";
    const title = document.createElement("div");
    title.className = "device-title";
    title.textContent = label;
    text.appendChild(title);

    const sub = document.createElement("div");
    sub.className = "device-sub";
    if (row.flagged) {
      const flag = document.createElement("span");
      flag.className = "device-flag";
      flag.textContent = this._t(
        "card.slot.proxy_changes",
        { count: String(row.changes) },
        row.changes
      );
      sub.appendChild(flag);
    }
    const rest = document.createElement("span");
    const parts = [this._t("card.devices.via", { proxy })];
    if (path) parts.push(`${path.rssi} dBm`);
    rest.textContent = (row.flagged ? " · " : "") + parts.join(" · ");
    sub.appendChild(rest);
    text.appendChild(sub);
    tile.appendChild(text);

    const series = hourly.get(String(entry.address || "").toUpperCase());
    if (series && series.some((n) => Number(n) > 0)) {
      tile.appendChild(this._renderSpark(series));
    }
    return tile;
  }

  /**
   * A device's day as one bar per hour, oldest on the left, scaled to its own
   * busiest hour: the shape is the point, and a figure is in the tooltip.
   */
  _renderSpark(series) {
    const counts = series.map((n) => Math.max(0, Number(n) || 0));
    const peak = Math.max(1, ...counts);
    const total = counts.reduce((sum, n) => sum + n, 0);
    const step = 3;
    const svg = svgElement("svg", {
      class: "device-spark",
      width: counts.length * step,
      height: 18,
      viewBox: `0 0 ${counts.length * step} 18`,
      role: "img",
      "aria-label": this._t("card.slot.connections", { count: String(total) }, total),
    });
    counts.forEach((count, i) => {
      const height = count > 0 ? 3 + (13 * count) / peak : 1.5;
      svg.appendChild(
        svgElement("rect", {
          class: count > 0 ? "on" : "off",
          x: i * step,
          y: 18 - height,
          width: 2,
          height,
          rx: 1,
        })
      );
    });
    return svg;
  }

  /**
   * The rack: one row per slot, or `null` for a proxy with no slots and
   * nothing holding one.
   *
   * Vertical, one occupant per row, because the correspondence between a pip
   * and a name used to be carried only by list order -- nothing on screen said
   * the first filled pip was the first name. A free slot keeps its row (empty
   * pip, no label) so the tile still reads as a gauge: saturation stays
   * visible without reading the numbers, at the price of a fixed height.
   *
   * Two numbers decide the drawing and they come from the same habluetooth
   * snapshot but not from the same field: `used` is `slots - free`, while the
   * occupant list is the allocated-address list. They can momentarily
   * disagree, in either direction, and each direction is handled on purpose:
   *
   *   - Fewer occupants than `used` -- a pre-0.6.0 backend, or a resolver that
   *     answered nothing -- fills the pips anyway. The count is authoritative
   *     about how many slots are spent even when nothing can name them.
   *   - More occupants than `slots` gets a row each, past the end of the rack
   *     and marked. Dropping one would hide a device that is holding a
   *     connection, which is the single thing this card exists to show; an
   *     extra row merely says the two numbers disagree. The header count is
   *     left alone -- it reports what the sensor says, not what we drew.
   */
  _renderSlotRack(used, total, allocated) {
    const { slots, filled, rows } = this._slotCounts(used, total, allocated);
    if (rows <= 0) {
      return null;
    }
    const rack = document.createElement("div");
    rack.className = "slot-rack";
    for (let i = 0; i < rows; i += 1) {
      rack.appendChild(this._renderSlot(i < filled, i >= slots, allocated[i]));
    }
    return rack;
  }

  /**
   * The same pips, side by side, with nothing named -- `show_devices: false`.
   *
   * This is the layout the card had before the rack, and it exists for the
   * fleet the rack is wrong for: the rack spends a fixed row per slot, so
   * thirty slots is thirty rows, while one wrapped line of squares still
   * answers "is anything saturated?" at a glance. What it cannot answer is
   * "who is holding it", which is the whole of the trade.
   *
   * Geometry is `_slotCounts`, byte for byte the rack's, so the two layouts
   * cannot come to different conclusions about the same proxy. In particular
   * an overflow pip is still drawn, still amber: dropping it here would be
   * the one thing the rack refuses to do -- silently under-report how many
   * devices hold a connection -- and it is worse without names, because the
   * amber square is then the only thing on screen saying the two numbers
   * disagree.
   */
  _renderSlotRow(used, total, allocated) {
    const { slots, filled, rows } = this._slotCounts(used, total, allocated);
    if (rows <= 0) {
      return null;
    }
    const strip = document.createElement("div");
    strip.className = "pips";
    for (let i = 0; i < rows; i += 1) {
      strip.appendChild(this._renderPip(i < filled, i >= slots));
    }
    return strip;
  }

  /**
   * How many pips to draw (`rows`), how many of them are held (`filled`), and
   * where the proxy says its slots stop (`slots`) -- past which a pip is an
   * overflow.
   *
   * One place, because three readers need the same answer: the rack, the
   * horizontal row, and `_slotRowCount` (which sizes the card without
   * rendering). The rules it encodes are argued in `_renderSlotRack`.
   */
  _slotCounts(used, total, allocated) {
    const slots = Math.max(0, total);
    // Clamp used into [0, slots] so a stale value can't over/under-draw, then
    // let the occupants extend it: a named device always gets a filled pip.
    const filled = Math.max(
      Math.max(0, Math.min(used, slots)),
      allocated.length
    );
    return { slots, filled, rows: Math.max(slots, allocated.length) };
  }

  /** One square. Filled = held; overflow = past the end of the rack, amber. */
  _renderPip(occupied, overflow) {
    const pip = document.createElement("span");
    pip.className = "pip";
    if (occupied) {
      pip.classList.add("filled");
    }
    if (overflow) {
      pip.classList.add("overflow");
    }
    return pip;
  }

  /** One rack row: the pip, then whatever holds it (if anything). */
  _renderSlot(occupied, overflow, device) {
    const row = document.createElement("div");
    row.className = "slot";
    row.appendChild(this._renderPip(occupied, overflow));

    const label = this._renderConnectedDevice(device);
    if (label) {
      row.appendChild(label);
    }
    return row;
  }

  /**
   * The label beside one slot's pip, or `null` for a free slot (and for an
   * occupied one whose entry carries nothing to print).
   *
   * Three cases, and the third is the one this feature exists for:
   *
   *   - Home Assistant knows the device and it has a name -> show the name.
   *     The address is already published in the `allocated` attribute, and a
   *     MAC beside every name is noise on a phone.
   *   - Home Assistant knows the device but it has no name -> show the raw
   *     address, unmarked. HA allows a nameless device; saying it is unknown
   *     would be false.
   *   - Home Assistant does not know the address at all -> show the raw
   *     address WITH the marker. A device the registry cannot account for,
   *     holding one of a proxy's handful of connection slots, is exactly what
   *     wants surfacing. `device_id` decides this, not the emptiness of the
   *     name, because those are two different facts.
   */
  _renderConnectedDevice(device) {
    const entry = device || {};
    const address = entry.address ? String(entry.address) : "";
    const name = entry.name ? String(entry.name) : "";
    if (!address && !name) {
      return null;
    }

    const cell = document.createElement("div");
    cell.className = "slot-label";

    const label = document.createElement("span");
    label.className = name ? "device-name" : "device-addr";
    label.textContent = name || address;
    cell.appendChild(label);

    const path = this._renderPath(entry.path);
    if (path) {
      cell.appendChild(path);
    }

    const history = this._renderHistory(entry.history);
    if (history) {
      cell.appendChild(history);
    }

    if (!entry.device_id) {
      // Its own line, under the address rather than after it: the address is
      // already long enough to wrap on a phone, and a marker that wraps into
      // the middle of a MAC reads as part of it.
      const marker = document.createElement("span");
      marker.className = "device-unknown";
      marker.textContent = this._t("card.proxy.unknown_device");
      cell.appendChild(marker);
    }

    return cell;
  }

  /**
   * How well this proxy heard the device, and why it is the one holding the
   * slot -- or `null` when the backend read nothing.
   *
   * The signal is drawn on every row that has one, with the band the backend
   * put it in: "-91 dBm" asks the reader to know what that means, "weak" does
   * not. It was read as the slot appeared -- a connected device stops
   * advertising, so there is no later reading to show -- and the tooltip says
   * so, because a number that never moves looks live otherwise.
   *
   * The second half is drawn only when the backend named a proxy that heard
   * the device clearly better. It applies habluetooth's own switch margin, so
   * a route nobody would question carries no `stronger_name`. That a stronger
   * proxy was *full* is the case worth the pixels: Home Assistant rules a
   * proxy with no free slot out whatever it hears, so the odd-looking route is
   * explained rather than merely reported.
   */
  /** Whether `_renderPath` draws a line; also what `_labelLineCount` counts. */
  _pathDrawn(path) {
    return Boolean(path) && typeof path.rssi === "number";
  }

  /** Whether `_renderHistory` draws a line; the same rule for the sizer. */
  _historyDrawn(history) {
    return (
      Boolean(history) &&
      typeof history.connections === "number" &&
      history.connections >= 2
    );
  }

  _renderPath(path) {
    if (!this._pathDrawn(path)) {
      return null;
    }
    const line = document.createElement("span");
    const quality = ["good", "fair", "weak"].includes(path.quality)
      ? path.quality
      : null;
    line.className = quality ? `slot-path signal-${quality}` : "slot-path";
    line.setAttribute("title", this._t("card.slot.signal_hint"));
    // A backend that publishes no band (none ships without one, but a cached
    // card can meet anything) still gets the number, which is a fact.
    const parts = [
      quality
        ? this._t("card.slot.signal", {
            rssi: String(path.rssi),
            quality: this._t(`card.signal.${quality}`),
          })
        : `${path.rssi} dBm`,
    ];
    if (path.stronger_name) {
      parts.push(
        this._t(
          path.stronger_was_full ? "card.slot.stronger_full" : "card.slot.stronger",
          {
            proxy: String(path.stronger_name),
            stronger: String(path.stronger_rssi),
          }
        )
      );
    }
    line.textContent = parts.join(" · ");
    return line;
  }

  /**
   * How often this device took a slot over the last day, or `null` when that
   * is not worth a line.
   *
   * One connection, held, is what a well-behaved device looks like, so the
   * line starts at two. Nothing here is a verdict -- a sensor that wakes every
   * few minutes reconnects all day by design -- which is why it is set in the
   * same muted type as the signal and never coloured.
   */
  _renderHistory(history) {
    if (!this._historyDrawn(history)) {
      return null;
    }
    const parts = [
      this._t(
        "card.slot.connections",
        { count: String(history.connections) },
        history.connections
      ),
    ];
    const changes = this._toInt(history.proxy_changes, 0);
    if (changes > 0) {
      parts.push(
        this._t("card.slot.proxy_changes", { count: String(changes) }, changes)
      );
    }
    const line = document.createElement("span");
    line.className = "slot-history";
    line.textContent = parts.join(" · ");
    return line;
  }

  _renderIncidents(incidentEntity, incidentState) {
    const container = this._incidentsEl;
    container.innerHTML = "";

    // Missing entity -> neutral note (integration may still be loading).
    if (!incidentState) {
      const note = document.createElement("div");
      note.className = "incident-ok muted";
      note.textContent = this._t("card.incidents.sensor_missing", {
        entity: incidentEntity,
      });
      container.appendChild(note);
      return;
    }

    const isOn = incidentState.state === "on";
    if (!isOn) {
      const ok = document.createElement("div");
      ok.className = "incident-ok";
      ok.textContent = this._t("card.incidents.none");
      container.appendChild(ok);
      return;
    }

    const attrs = incidentState.attributes || {};
    const incidents = Array.isArray(attrs.incidents) ? attrs.incidents : [];

    const heading = document.createElement("div");
    heading.className = "incident-heading";
    const count =
      attrs.incident_count !== undefined
        ? this._toInt(attrs.incident_count, incidents.length)
        : incidents.length;
    heading.textContent = this._t(
      "card.incidents",
      { count: String(count) },
      count
    );
    container.appendChild(heading);

    if (!incidents.length) {
      // On, but no detail available -- still surface the alert.
      const badge = document.createElement("div");
      badge.className = "incident critical";
      badge.textContent = this._t("card.incidents.no_detail");
      container.appendChild(badge);
      return;
    }

    for (const inc of incidents) {
      container.appendChild(this._renderIncidentBadge(inc));
    }
  }

  _renderIncidentBadge(inc) {
    const kind = inc && inc.kind ? String(inc.kind) : "unknown";
    const address = inc && inc.address ? String(inc.address) : "";
    const detail = inc && inc.detail ? String(inc.detail) : "";

    const badge = document.createElement("div");
    badge.className =
      "incident " + (CRITICAL_KINDS.has(kind) ? "critical" : "warning");

    const label = document.createElement("span");
    label.className = "incident-kind";
    // A kind this build does not know about -- a newer backend seen by an
    // older cached card -- falls back to the catalogue's "unknown" label
    // rather than to a raw key.
    const kindKey = `card.kind.${kind}`;
    label.textContent =
      this._lookup(kindKey) !== null
        ? this._t(kindKey)
        : this._t("card.kind.unknown");
    badge.appendChild(label);

    // What Home Assistant calls the device, when it can name it at all. The
    // address stays on the badge either way: it is what correlates this row
    // with the proxy's own entities and with the logs, and for a peripheral
    // the registry cannot account for it is the only identity there is --
    // which is itself the diagnostic. So the name leads and the address
    // follows it, demoted; an unnamed device simply keeps the address alone.
    const deviceName = inc && inc.device_name ? String(inc.device_name) : "";
    if (deviceName) {
      const named = document.createElement("span");
      named.className = "incident-device";
      named.textContent = deviceName;
      badge.appendChild(named);
    }
    if (address) {
      const addr = document.createElement("span");
      addr.className = deviceName ? "incident-addr subtle" : "incident-addr";
      addr.textContent = address;
      badge.appendChild(addr);
    }

    // `detail` is deliberately NOT translated here: the backend renders it
    // from the same catalogue, in the installation's language, and publishes
    // it as an entity attribute that user automations format notifications
    // from. Re-rendering it in the viewer's language would need the key and
    // parameters, which the attribute does not carry.
    if (detail) {
      const det = document.createElement("span");
      det.className = "incident-detail";
      det.textContent = detail;
      badge.appendChild(det);
    }

    // Which proxies are involved — the actionable part of a deadlock.
    //
    // Prefer the names the backend resolved: they are the same ones the detail
    // sentence above already used, so a badge cannot call one proxy two things
    // (it used to read "on Proxy Buanderie" and then "on D0:CF:13:0F:05:5A").
    // A backend too old to publish them, seen by a newer cached card, still
    // has `sources`.
    const sources = inc && Array.isArray(inc.sources) ? inc.sources : [];
    const sourceNames =
      inc && Array.isArray(inc.source_names) && inc.source_names.length
        ? inc.source_names
        : sources;
    if (sourceNames.length) {
      const src = document.createElement("span");
      src.className = "incident-addr";
      src.textContent = this._t("card.incident.sources", {
        sources: sourceNames.join(", "),
      });
      badge.appendChild(src);
    }

    return badge;
  }

  /**
   * Minimal, self-contained failure card so Lovelace never breaks.
   *
   * The message is deliberately left in English. It renders exactly when the
   * card is broken -- possibly BECAUSE the catalogue fetch or `_t` is what
   * failed -- so translating it would be circular: the one string that must
   * always appear would depend on the machinery that just did not work.
   */
  _renderFatal(err) {
    const msg = err && err.message ? err.message : String(err);
    this.shadowRoot.innerHTML =
      "<ha-card><div style='padding:16px;color:var(--error-color,#db4437)'>" +
      "BlueSight Card error: " +
      msg.replace(/[<>&]/g, "") +
      "</div></ha-card>";
    this._built = false; // allow a clean rebuild once state recovers
  }

  _css() {
    return `
      ha-card {
        display: block;
        padding: 12px 16px 16px;
      }
      .card-header {
        font-size: 1.25rem;
        font-weight: 500;
        color: var(--primary-text-color);
        padding: 4px 0 12px;
      }
      .proxies {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .proxy {
        border: 1px solid var(--divider-color, #e0e0e0);
        border-radius: 8px;
        padding: 10px 12px;
        background: var(--card-background-color, transparent);
      }
      .proxy.offline {
        opacity: 0.5;
      }
      .proxy-name {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        font-weight: 500;
        color: var(--primary-text-color);
        gap: 8px;
      }
      .proxy-count {
        font-variant-numeric: tabular-nums;
        color: var(--secondary-text-color, var(--primary-text-color));
        font-weight: 400;
        font-size: 0.95rem;
      }
      .proxy-meta {
        margin-top: 4px;
        font-size: 0.8rem;
        color: var(--secondary-text-color, #888);
      }
      .slot-rack {
        display: flex;
        flex-direction: column;
        gap: 4px;
        margin-top: 8px;
      }
      .pips {
        /* \`show_devices: false\`: the rack's pips on one line. Wraps, so a
           proxy with more slots than fit stays inside the tile. */
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
        margin-top: 8px;
      }
      .pips .pip {
        /* The rack nudges each pip down to sit on its label's first line;
           there is no label here, so the nudge would only be a tilt. */
        margin-top: 0;
      }
      .slot {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        /* A free slot's row is a bare 14px pip; a held one is a line of text.
           The floor keeps the rack evenly spaced either way, which is what
           makes it readable as a gauge. */
        min-height: 20px;
      }
      .pip {
        width: 14px;
        height: 14px;
        border-radius: 3px;
        border: 1px solid var(--divider-color, #b0b0b0);
        background: transparent;
        box-sizing: border-box;
        flex: 0 0 auto;
        /* Optically centres the pip on the label's first line. */
        margin-top: 3px;
      }
      .pip.filled {
        background: var(--success-color, #43a047);
        border-color: var(--success-color, #43a047);
      }
      .pip.filled.overflow {
        /* A row past the end of the rack: more occupants than the proxy says
           it has slots. Amber, not green, so it does not read as one more
           slot the proxy turns out to have. */
        background: var(--warning-color, #ffa600);
        border-color: var(--warning-color, #ffa600);
      }
      .slot-label {
        display: flex;
        flex-direction: column;
        gap: 1px;
        min-width: 0;
        font-size: 0.85rem;
        color: var(--secondary-text-color, #888);
      }
      .device-name {
        color: var(--primary-text-color);
      }
      .device-addr {
        font-family: var(--code-font-family, monospace);
        word-break: break-all;
      }
      .device-unknown {
        font-style: italic;
      }
      .slot-path {
        font-size: 0.8rem;
        opacity: 0.8;
        font-variant-numeric: tabular-nums;
      }
      .slot-history {
        font-size: 0.8rem;
        opacity: 0.8;
        font-variant-numeric: tabular-nums;
      }
      /* The band is carried by a mark before the line, not by the text
         colour: amber text on a light card fails contrast, a dot does not. */
      .slot-path.signal-good::before,
      .slot-path.signal-fair::before,
      .slot-path.signal-weak::before {
        content: "";
        display: inline-block;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        margin-right: 5px;
        vertical-align: 1px;
      }
      .slot-path.signal-good::before {
        background: var(--success-color, #43a047);
      }
      .slot-path.signal-fair::before {
        background: var(--warning-color, #ffa600);
      }
      .slot-path.signal-weak::before {
        background: var(--error-color, #db4437);
      }
      .pip-hint {
        margin-top: 8px;
        font-size: 0.8rem;
        color: var(--secondary-text-color, #888);
        font-style: italic;
      }
      .incidents {
        margin-top: 14px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .incident-heading {
        font-weight: 500;
        color: var(--primary-text-color);
        margin-bottom: 2px;
      }
      .incident-ok {
        color: var(--success-color, #43a047);
        font-size: 0.95rem;
        padding: 4px 0;
      }
      .incident-ok.muted {
        color: var(--secondary-text-color, #888);
      }
      .incident {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 8px;
        border-radius: 6px;
        padding: 6px 10px;
        color: var(--bluesight-on-status, #fff);
      }
      .incident.critical {
        background: var(--error-color, #db4437);
      }
      .incident.warning {
        background: var(--warning-color, #ffa600);
        color: var(--bluesight-on-warning, #1a1a1a);
      }
      .incident-kind {
        font-weight: 700;
      }
      .incident-device {
        font-weight: 600;
      }
      .incident-addr {
        font-family: var(--code-font-family, monospace);
        font-size: 0.9rem;
        opacity: 0.95;
      }
      /* An address shown beside a name it agrees with: still readable, but no
         longer competing with the name for the eye. */
      .incident-addr.subtle {
        font-size: 0.8rem;
        opacity: 0.75;
      }
      .incident-detail {
        font-size: 0.9rem;
        opacity: 0.95;
      }
      /* layout: tile -- one row, aligned with Home Assistant's own tile cards */
      .tile {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 12px;
        cursor: pointer;
      }
      .tile:focus-visible {
        outline: 2px solid var(--primary-color, #03a9f4);
        outline-offset: -2px;
      }
      .tile .dot {
        flex: 0 0 auto;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: var(--disabled-text-color, #9e9e9e);
      }
      .tile .dot.green {
        background: var(--success-color, #43a047);
      }
      .tile .dot.amber {
        background: var(--warning-color, #ffa600);
      }
      .tile .dot.red {
        background: var(--error-color, #db4437);
      }
      .tile-name {
        font-weight: 600;
        color: var(--primary-text-color);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .tile-meta {
        margin-left: auto;
        font-size: 0.9rem;
        color: var(--secondary-text-color, #888);
        white-space: nowrap;
      }
      /* layout: rings -- a ring of slots per proxy, the colours are the signal
         bands'. The panel follows the theme rather than carrying a dark face of
         its own, so it reads in a light or a dark dashboard alike. */
      .fleet {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 12px;
      }
      .proxies.rings {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        gap: 10px;
      }
      .ring {
        background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
        border-radius: 12px;
        padding: 10px 6px;
        text-align: center;
        min-width: 0;
      }
      .ring.offline {
        opacity: 0.5;
      }
      .ring[role="button"] {
        cursor: pointer;
      }
      .ring[role="button"]:focus-visible {
        outline: 2px solid var(--primary-color, #03a9f4);
        outline-offset: 1px;
      }
      .ring-gauge {
        display: block;
        width: 96px;
        height: 96px;
        margin: 0 auto;
      }
      .ring-gauge path {
        fill: none;
      }
      .ring-gauge .track {
        stroke: var(--divider-color, #cfcfcf);
        stroke-width: 1.5;
      }
      .ring-gauge .sat {
        stroke: var(--secondary-text-color, #727272);
        stroke-width: 2.5;
        stroke-linecap: round;
      }
      .ring-gauge .seg {
        stroke: var(--divider-color, #cfcfcf);
        stroke-width: 7;
        stroke-linecap: round;
      }
      .ring-gauge .seg.held {
        stroke: var(--info-color, #2196f3);
      }
      .ring-gauge .seg.good {
        stroke: var(--success-color, #43a047);
      }
      .ring-gauge .seg.fair {
        stroke: var(--warning-color, #ffa600);
      }
      .ring-gauge .seg.weak {
        stroke: var(--error-color, #db4437);
      }
      .ring-gauge .seg.overflow {
        stroke: var(--warning-color, #ffa600);
        stroke-dasharray: 3 3;
      }
      .ring-count {
        fill: var(--primary-text-color);
        font-size: 16px;
        text-anchor: middle;
        font-variant-numeric: tabular-nums;
      }
      .ring-name {
        font-weight: 500;
        color: var(--primary-text-color);
        padding: 0 4px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .ring-sub {
        font-size: 0.75rem;
        color: var(--secondary-text-color, #888);
      }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        max-width: 100%;
        box-sizing: border-box;
        font-size: 0.72rem;
        color: var(--primary-text-color);
        border: 1px solid var(--divider-color, #e0e0e0);
        border-radius: 999px;
        padding: 2px 8px 2px 6px;
      }
      .chip-dot {
        flex: 0 0 auto;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--disabled-text-color, #9e9e9e);
      }
      .chip-dot.signal-good {
        background: var(--success-color, #43a047);
      }
      .chip-dot.signal-fair {
        background: var(--warning-color, #ffa600);
      }
      .chip-dot.signal-weak {
        background: var(--error-color, #db4437);
      }
      .chip-name {
        min-width: 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .devices {
        margin-top: 14px;
      }
      .devices-heading {
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--secondary-text-color, #888);
        margin-bottom: 8px;
      }
      .device-tiles {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 8px;
      }
      .device-tile {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
        box-sizing: border-box;
        padding: 9px 10px;
        border-radius: 12px;
        border: 1px solid transparent;
        background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
      }
      .device-tile.flagged {
        border-color: var(--warning-color, #ffa600);
      }
      .device-tile {
        cursor: pointer;
      }
      .device-tile:focus-visible {
        outline: 2px solid var(--primary-color, #03a9f4);
        outline-offset: 1px;
      }
      .device-badge {
        --c: var(--disabled-text-color, #9e9e9e);
        flex: 0 0 auto;
        width: 36px;
        height: 36px;
        border-radius: 50%;
        display: grid;
        place-items: center;
        background: color-mix(in srgb, var(--c) 18%, transparent);
      }
      .device-badge.band-good {
        --c: var(--success-color, #43a047);
      }
      .device-badge.band-fair {
        --c: var(--warning-color, #ffa600);
      }
      .device-badge.band-weak {
        --c: var(--error-color, #db4437);
      }
      .bars {
        display: inline-flex;
        align-items: flex-end;
        gap: 2px;
        height: 14px;
      }
      .bars i {
        display: block;
        width: 4px;
        border-radius: 1px;
        background: var(--divider-color, #cfcfcf);
      }
      .bars i:nth-child(1) {
        height: 5px;
      }
      .bars i:nth-child(2) {
        height: 9px;
      }
      .bars i:nth-child(3) {
        height: 14px;
      }
      .bars i.on {
        background: var(--c);
      }
      .device-text {
        flex: 1 1 auto;
        min-width: 0;
      }
      .device-title {
        font-size: 14px;
        font-weight: 600;
        line-height: 20px;
        color: var(--primary-text-color);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .device-sub {
        font-size: 12px;
        line-height: 16px;
        color: var(--secondary-text-color, #888);
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      /* The outline carries the amber; the words stay in the text colour, since
         amber text on a light card fails contrast. */
      .device-flag {
        font-weight: 600;
        color: var(--primary-text-color);
      }
      .device-spark {
        flex: 0 0 auto;
        display: block;
      }
      .device-spark rect.off {
        fill: var(--divider-color, #cfcfcf);
      }
      .device-spark rect.on {
        fill: var(--secondary-text-color, #727272);
      }
      .more {
        width: 100%;
        margin-top: 8px;
        padding: 8px;
        font: inherit;
        font-size: 0.8rem;
        font-weight: 600;
        color: var(--primary-color, #03a9f4);
        background: transparent;
        border: 1px dashed var(--divider-color, #cfcfcf);
        border-radius: 10px;
        cursor: pointer;
      }
      .more:focus-visible {
        outline: 2px solid var(--primary-color, #03a9f4);
      }
      /* The device detail: a popup of its own, or the second screen of the
         one-line card's popup. */
      .detail-bar {
        padding: 8px 12px 0;
      }
      .detail-back {
        padding: 4px 8px 4px 0;
        font: inherit;
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--primary-color, #03a9f4);
        background: transparent;
        border: 0;
        cursor: pointer;
      }
      .detail-back:focus-visible {
        outline: 2px solid var(--primary-color, #03a9f4);
        outline-offset: 2px;
      }
      .detail-body {
        padding: 14px 16px 16px;
      }
      .detail-title {
        font-size: 1.05rem;
        font-weight: 600;
        color: var(--primary-text-color);
      }
      .detail-sub {
        --c: var(--disabled-text-color, #9e9e9e);
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 2px;
        font-size: 0.82rem;
        color: var(--secondary-text-color, #888);
        font-variant-numeric: tabular-nums;
      }
      .detail-sub.band-good {
        --c: var(--success-color, #43a047);
      }
      .detail-sub.band-fair {
        --c: var(--warning-color, #ffa600);
      }
      .detail-sub.band-weak {
        --c: var(--error-color, #db4437);
      }
      .detail-kv {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
        margin: 12px 0;
      }
      .detail-kv div {
        padding: 8px 10px;
        border-radius: 10px;
        background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
      }
      .detail-kv b {
        display: block;
        font-size: 1.15rem;
        font-weight: 600;
        color: var(--primary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .detail-kv span {
        font-size: 0.72rem;
        color: var(--secondary-text-color, #888);
      }
      .detail-track {
        position: relative;
        height: 22px;
        overflow: hidden;
        border-radius: 5px;
        background-color: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
        background-image: repeating-linear-gradient(
          90deg,
          transparent 0,
          transparent calc(100% / 24 - 1px),
          var(--divider-color, #cfcfcf) calc(100% / 24 - 1px),
          var(--divider-color, #cfcfcf) calc(100% / 24)
        );
      }
      .detail-sub + .devices-heading {
        margin-top: 16px;
      }
      .proxy-dev-name {
        margin: 12px 0 4px;
        font-size: 0.85rem;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .proxy-dev-track {
        height: 12px;
      }
      .detail-seg {
        position: absolute;
        top: 0;
        bottom: 0;
        border-radius: 3px;
      }
      .detail-unobserved {
        position: absolute;
        top: 0;
        bottom: 0;
        background-image: repeating-linear-gradient(
          45deg,
          transparent 0,
          transparent 4px,
          var(--divider-color, #cfcfcf) 4px,
          var(--divider-color, #cfcfcf) 5px
        );
      }
      .detail-seg.proxy-0,
      .detail-legend i.proxy-0 {
        background: var(--bluesight-proxy-1, #4c9be0);
      }
      .detail-seg.proxy-1,
      .detail-legend i.proxy-1 {
        background: var(--bluesight-proxy-2, #9a78e0);
      }
      .detail-seg.proxy-2,
      .detail-legend i.proxy-2 {
        background: var(--bluesight-proxy-3, #2fb3a5);
      }
      .detail-seg.proxy-3,
      .detail-legend i.proxy-3 {
        background: var(--bluesight-proxy-4, #e8913a);
      }
      .detail-seg.proxy-4,
      .detail-legend i.proxy-4 {
        background: var(--bluesight-proxy-5, #d65f9c);
      }
      .detail-axis {
        display: flex;
        justify-content: space-between;
        margin-top: 3px;
        font-size: 0.7rem;
        color: var(--secondary-text-color, #888);
      }
      .detail-legend {
        display: flex;
        flex-wrap: wrap;
        gap: 6px 12px;
        margin-top: 8px;
        font-size: 0.75rem;
        color: var(--secondary-text-color, #888);
      }
      .detail-legend span {
        display: inline-flex;
        align-items: center;
      }
      .detail-legend i {
        display: inline-block;
        width: 14px;
        height: 8px;
        margin-right: 6px;
        border-radius: 2px;
      }
      .detail-note {
        margin-top: 8px;
        font-size: 0.8rem;
        font-style: italic;
        color: var(--secondary-text-color, #888);
      }
      .empty {
        color: var(--secondary-text-color, #888);
        font-size: 0.95rem;
        line-height: 1.4;
        padding: 8px 0;
      }
    `;
  }

  /**
   * How many rack rows a proxy will draw, from `hass` alone.
   *
   * `getCardSize` is called without rendering, so this reproduces
   * `_renderSlotRack`'s row count rather than measuring the DOM. It shares
   * `_reachability` with the tile so the two cannot disagree about which
   * proxies draw a rack at all.
   */
  _slotRowCount(entityId, states) {
    const allocated = this._drawnOccupants(entityId, states);
    if (allocated === null) {
      return 0;
    }
    const stateObj = states[entityId];
    const attrs = stateObj.attributes || {};
    return this._slotCounts(
      this._toInt(stateObj.state, 0),
      this._toInt(attrs.total, 0),
      allocated
    ).rows;
  }

  /**
   * The occupant list a proxy's rack will draw, or `null` when it draws no
   * rack at all (missing, offline, unavailable) -- the same rule the tile
   * applies, through the same `_reachability`.
   */
  _drawnOccupants(entityId, states) {
    const stateObj = states[entityId];
    if (!stateObj) {
      return null; // the "missing" tile: name and a word, no rack
    }
    const { offline, unavailable } = this._reachability(
      stateObj,
      states,
      this._healthEntities(entityId)
    );
    if (offline || unavailable) {
      return null;
    }
    const attrs = stateObj.attributes || {};
    return Array.isArray(attrs.allocated_devices) ? attrs.allocated_devices : [];
  }

  /**
   * How many text lines the rack's labels add beyond each row's first one:
   * the signal line, the connections line, the "unknown" marker.
   *
   * A row is 20px for a pip and one line of text; every further line in its
   * label pushes it down by ~16px, and a fleet where every row shows a signal
   * and a history is half again as tall as the bare rack. The same predicates
   * decide what `_renderConnectedDevice` draws, so the count cannot drift
   * from the drawing. Zero under `show_devices: false`, which draws no label.
   */
  _labelLineCount(entityId, states) {
    if (!this._showDevices()) {
      return 0;
    }
    const allocated = this._drawnOccupants(entityId, states) || [];
    let lines = 0;
    for (const device of allocated) {
      const entry = device || {};
      if (!entry.address && !entry.name) continue;
      if (this._pathDrawn(entry.path)) lines += 1;
      if (this._historyDrawn(entry.history)) lines += 1;
      if (!entry.device_id) lines += 1;
    }
    return lines;
  }

  /**
   * The masonry units one proxy tile costs, from its slot count and the
   * layout, in pixels of the CSS this file ships over the 50px Lovelace
   * assumes:
   *
   *   - the tile's fixed part -- borders, padding, the name line, the health
   *     line, the gap to the next tile: ~64px, one unit;
   *   - with the rack, each row is 20px plus a 4px gap: two rows to a unit,
   *     and each label line past a row's first (`_labelLineCount`) ~16px,
   *     three to a unit -- both counted in one pixel sum so a half-filled
   *     unit is not rounded up twice;
   *   - without it, the whole slot area is one 14px line of pips plus its 8px
   *     margin -- 22px, the same as a pair of rack rows, so one unit, and the
   *     same one whether the proxy has two slots or eight. That fixed height
   *     is the point of the option.
   *
   * A proxy that draws no pips at all (offline, missing, a passive scanner)
   * has no slot area in either layout and costs only the fixed part.
   *
   * The one thing this cannot see is `.pips` wrapping: a proxy with more slots
   * than fit the card's width takes a second line, and how many fit depends on
   * a width `getCardSize` is called without. Counted as one line deliberately
   * -- the alternative is a hardcoded guess at the viewer's column width, and
   * masonry re-asks on every state change anyway.
   */
  _proxyUnits(rows, labelLines = 0) {
    if (!this._showDevices()) {
      return 1 + (rows > 0 ? 1 : 0);
    }
    return 1 + Math.ceil((rows * 24 + labelLines * 16) / 48);
  }

  /**
   * How many ~50px masonry rows this card occupies.
   *
   * Worth computing rather than guessing now that the rack has a fixed height:
   * a proxy tile is no longer "about one row" -- an 8-slot proxy is four times
   * a 2-slot one, and the old `1 + proxies + 1` under-reported both. The
   * arithmetic below is in pixels of the CSS this file ships, divided by the
   * 50px Lovelace assumes:
   *
   *   - the card header, ~39px, plus the card's own padding: one unit;
   *   - the incident area: one unit, which is the "no incidents" line or a
   *     badge or two. A long incident feed still under-reports, but that is a
   *     transient state and masonry re-asks on every state change;
   *   - each proxy tile: `_proxyUnits`, which is where the per-layout pixels
   *     are counted -- the tile is a fixed height under `show_devices: false`
   *     and grows with the slot count under the rack.
   */
  getCardSize() {
    if (this._layout() === "tile") return 1;
    const hass = this._hass;
    const states = (hass && hass.states) || {};
    const proxies = this._discoverProxyEntities(hass || {});
    if (!proxies.length) {
      // The empty-state paragraph is three lines of small text, in either
      // layout: there is no proxy to lay out.
      return 3;
    }
    if (this._layout() === "rings") {
      // Header, fleet strip and incident area, then the rings two to a row on a
      // phone-width card (~170px each), then the device tiles: a heading, a
      // ~64px tile per device shown (one column is the worst case) and the
      // "show the others" button. Masonry re-asks on every state change, so an
      // over-estimate corrects itself.
      let rowsSize = 3 + 4 * Math.ceil(proxies.length / 2);
      if (this._showDevices()) {
        let devices = 0;
        for (const entityId of proxies) {
          devices += (this._drawnOccupants(entityId, states) || []).length;
        }
        if (devices > 0) {
          const drawn = this._devicesExpanded
            ? devices
            : Math.min(devices, DEVICE_TILES_SHOWN);
          rowsSize +=
            1 + Math.ceil((drawn * 64) / 50) + (devices > DEVICE_TILES_SHOWN ? 1 : 0);
        }
      }
      return rowsSize;
    }
    let size = 2; // card header + incident area
    for (const entityId of proxies) {
      size += this._proxyUnits(
        this._slotRowCount(entityId, states),
        this._labelLineCount(entityId, states)
      );
    }
    return size;
  }

  /**
   * Size in a sections view, which does not ask `getCardSize`.
   *
   * The one-line tile is a tile: half a section wide, one row high, like Home
   * Assistant's own. The full card's height is its content -- a rack grows a
   * row per slot -- so `rows` is left to its "auto" default, and only the
   * width is set: the full section, never narrower than half, below which
   * a device name and its signal line no longer fit beside the pip.
   */
  getGridOptions() {
    if (this._layout() === "tile") {
      return { columns: 6, rows: 1, min_columns: 3, min_rows: 1, max_rows: 1 };
    }
    return { columns: 12, min_columns: 6 };
  }

  // Used by the "add card" UI to seed a default config.
  static getStubConfig() {
    return {};
  }

  /**
   * The visual editor: Home Assistant renders this schema with its own form
   * controls, so there is no editor element to maintain.
   *
   * A select whose key is absent from the config shows blank: `ha-form` hands
   * `default` to the control as a placeholder only, and the dropdown ignores
   * it. The two selects therefore say in their help text what "not set" means,
   * which is what the card does when the key is missing.
   *
   * `proxies` is deliberately not in it. Auto-discovery is the default and
   * the right answer for nearly everyone, and an entity picker cannot be
   * narrowed to *slot* sensors -- it would offer every BlueSight sensor and
   * let a saturation sensor be drawn as a proxy. A list written in YAML is
   * kept as it is: the form only rewrites the keys it shows.
   *
   * Labels come from the card's own catalogue in the language the frontend
   * is displayed in. This is static -- there is no card instance and no
   * `hass` to read the viewer's language from -- so it reads the document's,
   * which Home Assistant sets to the same thing.
   */
  static getConfigForm() {
    const language = baseLanguage(
      (typeof document !== "undefined" &&
        document.documentElement &&
        document.documentElement.lang) ||
        "en"
    );
    loadCatalogue(language, () => {});
    if (language !== "en") loadCatalogue("en", () => {});
    const t = (key) => lookupKey(language, key) || key;
    const option = (value, key) => ({ value, label: t(key) });
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          name: "layout",
          default: "full",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                option("full", "card.editor.layout_full"),
                option("tile", "card.editor.layout_tile"),
                option("rings", "card.editor.layout_rings"),
              ],
            },
          },
        },
        { name: "show_devices", default: true, selector: { boolean: {} } },
        {
          name: "bounce_threshold",
          default: DEFAULT_BOUNCE_THRESHOLD,
          selector: { number: { min: 1, max: 200, mode: "box" } },
        },
        {
          name: "tile_tap",
          default: "popup",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                option("popup", "card.editor.tile_tap_popup"),
                option("more-info", "card.editor.tile_tap_more_info"),
              ],
            },
          },
        },
        {
          name: "popup_layout",
          default: "full",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                option("full", "card.editor.layout_full"),
                option("rings", "card.editor.layout_rings"),
              ],
            },
          },
        },
        {
          name: "incident_entity",
          selector: {
            entity: { filter: { domain: "binary_sensor", integration: "bluesight" } },
          },
        },
      ],
      computeLabel: (schema) => t(`card.editor.${schema.name}`),
      computeHelper: (schema) =>
        [
          "layout",
          "tile_tap",
          "show_devices",
          "popup_layout",
          "bounce_threshold",
        ].includes(schema.name)
          ? t(`card.editor.${schema.name}_helper`)
          : undefined,
    };
  }
}

// Define once. Before 0.4.0 the card was copied into `config/www/` by hand, so
// an upgraded install can still carry a stale `/local/bluesight-card.js`
// resource alongside the one the integration now serves. `define()` throws on
// a repeated name, and that exception would break the whole Lovelace view --
// not just this card.
if (!customElements.get("bluesight-card")) {
  customElements.define("bluesight-card", BlueSightCard);

  // Advertise the card in the Lovelace card picker.
  //
  // Name and description are deliberately left in English: this runs at module
  // load, before any `hass` exists, so there is no viewer language to render
  // them in and no repaint once one arrives.
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "bluesight-card",
    name: "BlueSight Card",
    description:
      "Per-proxy GATT slot usage and BLE incident banner for the BlueSight integration.",
    preview: false,
    documentationURL: "https://github.com/dasimon135/ha-bluesight",
  });
}
