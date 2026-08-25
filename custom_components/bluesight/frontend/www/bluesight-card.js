/**
 * BlueSight Card
 * -----------------------------------------------------------------------------
 * A vanilla-JS Lovelace custom card that visualises the state exposed by the
 * BlueSight integration:
 *
 *   - Per ESPHome/Bluetooth proxy: a tile with slot "pips" (filled = used,
 *     empty = free), a "used/total" count, and a line per occupied slot naming
 *     the device that holds it. Auto-discovered from any
 *     `sensor.*_slots_used` entity that carries a `total` attribute.
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
 */

// Kept equal to `manifest.json`'s version by tests/test_card_locale.py. The
// card has no build step, so there is nowhere to inject the real version at
// package time; a checked constant is the cheap way to make the drift loud.
const CARD_VERSION = "0.5.0";

// eslint-disable-next-line no-console
console.info(
  `%c BLUESIGHT-CARD %c v${CARD_VERSION} `,
  "color: white; background: #03a9f4; font-weight: 700;",
  "color: #03a9f4; background: white; font-weight: 700;"
);

const DEFAULT_INCIDENT_ENTITY = "binary_sensor.bluesight_incident";

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
  "card.proxy.last_advert": "last advert {age} ago",
  "card.proxy.last_advert_with_devices.one": "last advert {age} ago · {count} device seen",
  "card.proxy.last_advert_with_devices.other": "last advert {age} ago · {count} devices seen",
  "card.incidents.none": "No incidents",
  "card.incidents.one": "{count} incident",
  "card.incidents.other": "{count} incidents",
  "card.incidents.no_detail": "Incident active (no detail available)",
  "card.incidents.sensor_missing": "Incident sensor {entity} not found.",
  "card.incident.sources": "on {sources}",
  "card.kind.deadlock": "Deadlock",
  "card.kind.ghost_slot": "Ghost slot",
  "card.kind.storm": "Storm",
  "card.kind.bond_lost": "Bond lost",
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
   * Re-render on every state change, but cheaply: we compute a small signature
   * string from the values we actually draw and bail out if it is unchanged.
   * This getter/setter must NEVER throw -- a thrown error from a card's
   * `set hass` tears down the whole dashboard view.
   */
  set hass(hass) {
    this._hass = hass;
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
    const language = this._language();
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

  /** Best-effort friendly proxy name, stripping the " Slots Used" suffix. */
  _proxyName(stateObj, entityId) {
    const friendly =
      (stateObj && stateObj.attributes && stateObj.attributes.friendly_name) ||
      entityId;
    return String(friendly).replace(/\s*Slots Used$/i, "").trim() || entityId;
  }

  /**
   * The proxy-health companions of a `sensor.<proxy>_slots_used` entity.
   * BlueSight names them off the same device slug, so they are derivable
   * without a second discovery pass. Either may legitimately be absent.
   */
  _healthEntities(entityId) {
    const slug = entityId.replace(/^sensor\./, "").replace(/_slots_used$/, "");
    return {
      online: `binary_sensor.${slug}_online`,
      lastSeen: `sensor.${slug}_last_device_seen`,
    };
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

    if (!this._built) {
      this._buildSkeleton();
      this._built = true;
    }

    this._headerEl.textContent = title;
    this._renderProxies(proxyEntities, hass);
    this._renderIncidents(incidentEntity, incidentState);
  }

  _computeSignature(proxyEntities, hass, incidentEntity, incidentState, title) {
    // The language belongs in the signature: every drawn string depends on it,
    // and a viewer who changes their profile language moves nothing else.
    const parts = [title, incidentEntity, this._language()];
    for (const id of proxyEntities) {
      const s = hass.states ? hass.states[id] : undefined;
      if (!s) {
        parts.push(`${id}:missing`);
        continue;
      }
      const a = s.attributes || {};
      parts.push(`${id}:${s.state}:${a.total}:${a.free}`);
      // Which devices hold the slots, not just how many. `used/total` can sit
      // perfectly still while one device disconnects and another connects, or
      // while a device is renamed in the registry; left out here, the list
      // would keep showing the previous fleet until something else moved.
      const allocated = Array.isArray(a.allocated_devices)
        ? a.allocated_devices
        : [];
      parts.push(
        `dev:${allocated
          .map((d) => `${(d || {}).address}=${(d || {}).name}`)
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
    }
    if (incidentState) {
      parts.push(`inc:${incidentState.state}`);
      const incidents =
        (incidentState.attributes && incidentState.attributes.incidents) || [];
      // Length + a hash-ish join is enough to detect changes cheaply.
      parts.push(`incN:${incidents.length}`);
      for (const inc of incidents) {
        // `sources` is rendered for deadlocks, so a change of the proxy set
        // must invalidate the signature too.
        const sources = Array.isArray(inc.sources) ? inc.sources.join(",") : "";
        parts.push(`${inc.kind}|${inc.address}|${inc.detail}|${sources}`);
      }
    } else {
      parts.push("inc:missing");
    }
    return parts.join("~");
  }

  _buildSkeleton() {
    const style = document.createElement("style");
    style.textContent = this._css();

    const card = document.createElement("ha-card");

    this._headerEl = document.createElement("div");
    this._headerEl.className = "card-header";

    this._proxiesEl = document.createElement("div");
    this._proxiesEl.className = "proxies";

    this._incidentsEl = document.createElement("div");
    this._incidentsEl.className = "incidents";

    card.appendChild(this._headerEl);
    card.appendChild(this._proxiesEl);
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
      container.appendChild(this._renderProxyTile(entityId, stateObj, hass));
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
    const onlineState = states[health.online];
    // The dedicated online sensor is authoritative when present; fall back to
    // the slot sensor's own availability for a pre-0.2 install.
    const offline = onlineState
      ? onlineState.state !== "on"
      : stateObj.state === "unavailable" || stateObj.state === "unknown";
    const unavailable =
      stateObj.state === "unavailable" || stateObj.state === "unknown";
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

    const pips = document.createElement("div");
    pips.className = "pips";
    const drawable = !offline && !unavailable;
    if (total > 0 && drawable) {
      // Clamp used into [0, total] so a stale value can't over/under-draw.
      const filled = Math.max(0, Math.min(used, total));
      for (let i = 0; i < total; i += 1) {
        const pip = document.createElement("span");
        pip.className = i < filled ? "pip filled" : "pip";
        pips.appendChild(pip);
      }
    } else if (drawable) {
      // A passive (non-connectable) scanner reports slots=0: it can see
      // devices but never hold a connection, so there is nothing to draw.
      const hint = document.createElement("span");
      hint.className = "pip-hint";
      hint.textContent = this._t("card.proxy.scan_only");
      pips.appendChild(hint);
    }
    tile.appendChild(pips);

    // Who holds each slot -- the part that says what to blame when a proxy is
    // saturated. Drawn under the pips and only where there is something to
    // say: a proxy at 0/3 contributes no lines, so the card grows only where
    // it informs. Deliberately not gated on saturation, either: at 2/3 the
    // list is the warning you still have time to act on.
    //
    // The names arrive resolved from the backend. Re-deriving them here from
    // `hass.devices` would mean reimplementing `build_device_index`'s rule in
    // JavaScript -- which registry evidence may speak for a BLE address --
    // for no benefit.
    const allocated = drawable && Array.isArray(attrs.allocated_devices)
      ? attrs.allocated_devices
      : [];
    if (allocated.length) {
      const list = document.createElement("div");
      list.className = "proxy-devices";
      for (const device of allocated) {
        const row = this._renderConnectedDevice(device);
        if (row) {
          list.appendChild(row);
        }
      }
      if (list.childElementCount) {
        tile.appendChild(list);
      }
    }

    return tile;
  }

  /**
   * One line for one occupied slot, or `null` for an entry with nothing in it.
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

    const row = document.createElement("div");
    row.className = "proxy-device";

    const label = document.createElement("span");
    label.className = name ? "device-name" : "device-addr";
    label.textContent = name || address;
    row.appendChild(label);

    if (!entry.device_id) {
      const marker = document.createElement("span");
      marker.className = "device-unknown";
      marker.textContent = this._t("card.proxy.unknown_device");
      row.appendChild(marker);
    }

    return row;
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

    if (address) {
      const addr = document.createElement("span");
      addr.className = "incident-addr";
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
    const sources = inc && Array.isArray(inc.sources) ? inc.sources : [];
    if (sources.length) {
      const src = document.createElement("span");
      src.className = "incident-addr";
      src.textContent = this._t("card.incident.sources", {
        sources: sources.join(", "),
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
      .pips {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
        margin-top: 8px;
      }
      .pip {
        width: 14px;
        height: 14px;
        border-radius: 3px;
        border: 1px solid var(--divider-color, #b0b0b0);
        background: transparent;
        box-sizing: border-box;
      }
      .pip.filled {
        background: var(--success-color, #43a047);
        border-color: var(--success-color, #43a047);
      }
      .proxy-devices {
        margin-top: 8px;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .proxy-device {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 6px;
        font-size: 0.85rem;
        color: var(--secondary-text-color, #888);
      }
      .device-name {
        color: var(--primary-text-color);
      }
      .device-addr {
        font-family: var(--code-font-family, monospace);
      }
      .device-unknown {
        font-style: italic;
      }
      .pip-hint {
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
        color: #fff;
      }
      .incident.critical {
        background: var(--error-color, #db4437);
      }
      .incident.warning {
        background: var(--warning-color, #ffa600);
        color: #1a1a1a;
      }
      .incident-kind {
        font-weight: 700;
      }
      .incident-addr {
        font-family: var(--code-font-family, monospace);
        font-size: 0.9rem;
        opacity: 0.95;
      }
      .incident-detail {
        font-size: 0.9rem;
        opacity: 0.95;
      }
      .empty {
        color: var(--secondary-text-color, #888);
        font-size: 0.95rem;
        line-height: 1.4;
        padding: 8px 0;
      }
    `;
  }

  // HA calls this to size the card in masonry layouts.
  getCardSize() {
    const proxies = this._discoverProxyEntities(this._hass || {});
    // ~1 row header + 1 per proxy + 1 for the incident area.
    return 1 + Math.max(1, proxies.length) + 1;
  }

  // Used by the "add card" UI to seed a default config.
  static getStubConfig() {
    return {};
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
