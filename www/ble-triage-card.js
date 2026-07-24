/**
 * BLE Triage Card
 * -----------------------------------------------------------------------------
 * A vanilla-JS Lovelace custom card that visualises the state exposed by the
 * BLE Triage integration:
 *
 *   - Per ESPHome/Bluetooth proxy: a tile with slot "pips" (filled = used,
 *     empty = free) and a "used/total" count. Auto-discovered from any
 *     `sensor.*_slots_used` entity that carries a `total` attribute.
 *   - A global incident banner driven by `binary_sensor.ble_triage_incident`.
 *     Each incident is rendered as a coloured badge (red for deadlock/ghost
 *     slot, amber for storm) with its kind, address and detail.
 *
 * No build step, no Lit, no external imports -- a single self-contained file
 * that HA can serve as a `module` dashboard resource.
 *
 * Config (all optional):
 *   type: custom:ble-triage-card
 *   proxies: [sensor.foo_slots_used, ...]   # override auto-discovery
 *   incident_entity: binary_sensor.ble_triage_incident
 *   title: BLE Triage                        # card header text
 */

const CARD_VERSION = "0.1.0";

// eslint-disable-next-line no-console
console.info(
  `%c BLE-TRIAGE-CARD %c v${CARD_VERSION} `,
  "color: white; background: #03a9f4; font-weight: 700;",
  "color: #03a9f4; background: white; font-weight: 700;"
);

const DEFAULT_INCIDENT_ENTITY = "binary_sensor.ble_triage_incident";

// Incident kinds that are considered critical (red). Anything else is amber.
const CRITICAL_KINDS = new Set(["deadlock", "ghost_slot"]);

class BleTriageCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    // Signature of the last render so we can skip redundant DOM rebuilds.
    this._lastSignature = null;
    this._built = false;
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
      console.error("ble-triage-card render error", err);
      this._renderFatal(err);
    }
  }

  get hass() {
    return this._hass;
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
      // The `total` attribute is what marks this as a BLE Triage slot sensor.
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

    const proxyEntities = this._discoverProxyEntities(hass);
    const incidentEntity =
      this._config.incident_entity || DEFAULT_INCIDENT_ENTITY;
    const incidentState = hass.states ? hass.states[incidentEntity] : undefined;
    const title = this._config.title || "BLE Triage";

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
    const parts = [title, incidentEntity];
    for (const id of proxyEntities) {
      const s = hass.states ? hass.states[id] : undefined;
      if (!s) {
        parts.push(`${id}:missing`);
        continue;
      }
      const a = s.attributes || {};
      parts.push(`${id}:${s.state}:${a.total}:${a.free}`);
    }
    if (incidentState) {
      parts.push(`inc:${incidentState.state}`);
      const incidents =
        (incidentState.attributes && incidentState.attributes.incidents) || [];
      // Length + a hash-ish join is enough to detect changes cheaply.
      parts.push(`incN:${incidents.length}`);
      for (const inc of incidents) {
        parts.push(`${inc.kind}|${inc.address}|${inc.detail}`);
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
      empty.textContent =
        "No BLE Triage proxies found. Once the integration is set up, " +
        "sensor.<proxy>_slots_used entities appear automatically. You can " +
        "also list them explicitly with the `proxies:` config option.";
      container.appendChild(empty);
      return;
    }

    for (const entityId of proxyEntities) {
      const stateObj = hass.states ? hass.states[entityId] : undefined;
      container.appendChild(this._renderProxyTile(entityId, stateObj));
    }
  }

  _renderProxyTile(entityId, stateObj) {
    const tile = document.createElement("div");
    tile.className = "proxy";

    const name = document.createElement("div");
    name.className = "proxy-name";

    const count = document.createElement("span");
    count.className = "proxy-count";

    if (!stateObj) {
      tile.classList.add("offline");
      name.textContent = entityId;
      count.textContent = "missing";
      name.appendChild(count);
      tile.appendChild(name);
      return tile;
    }

    const unavailable =
      stateObj.state === "unavailable" || stateObj.state === "unknown";
    const attrs = stateObj.attributes || {};
    const total = this._toInt(attrs.total, 0);
    const used = this._toInt(stateObj.state, 0);

    name.textContent = this._proxyName(stateObj, entityId);
    if (unavailable) {
      tile.classList.add("offline");
      count.textContent = "offline";
    } else {
      count.textContent = `${used}/${total}`;
    }
    name.appendChild(count);
    tile.appendChild(name);

    const pips = document.createElement("div");
    pips.className = "pips";
    if (total > 0 && !unavailable) {
      // Clamp used into [0, total] so a stale value can't over/under-draw.
      const filled = Math.max(0, Math.min(used, total));
      for (let i = 0; i < total; i += 1) {
        const pip = document.createElement("span");
        pip.className = i < filled ? "pip filled" : "pip";
        pips.appendChild(pip);
      }
    } else if (!unavailable) {
      // total is 0/unknown -- show a hint rather than an empty row.
      const hint = document.createElement("span");
      hint.className = "pip-hint";
      hint.textContent = "no slot data";
      pips.appendChild(hint);
    }
    tile.appendChild(pips);

    return tile;
  }

  _renderIncidents(incidentEntity, incidentState) {
    const container = this._incidentsEl;
    container.innerHTML = "";

    // Missing entity -> neutral note (integration may still be loading).
    if (!incidentState) {
      const note = document.createElement("div");
      note.className = "incident-ok muted";
      note.textContent = `Incident sensor ${incidentEntity} not found.`;
      container.appendChild(note);
      return;
    }

    const isOn = incidentState.state === "on";
    if (!isOn) {
      const ok = document.createElement("div");
      ok.className = "incident-ok";
      ok.textContent = "No incidents";
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
    heading.textContent = `${count} incident${count === 1 ? "" : "s"}`;
    container.appendChild(heading);

    if (!incidents.length) {
      // On, but no detail available -- still surface the alert.
      const badge = document.createElement("div");
      badge.className = "incident critical";
      badge.textContent = "Incident active (no detail available)";
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
    label.textContent = kind.replace(/_/g, " ");
    badge.appendChild(label);

    if (address) {
      const addr = document.createElement("span");
      addr.className = "incident-addr";
      addr.textContent = address;
      badge.appendChild(addr);
    }

    if (detail) {
      const det = document.createElement("span");
      det.className = "incident-detail";
      det.textContent = detail;
      badge.appendChild(det);
    }

    return badge;
  }

  /** Minimal, self-contained failure card so Lovelace never breaks. */
  _renderFatal(err) {
    const msg = err && err.message ? err.message : String(err);
    this.shadowRoot.innerHTML =
      "<ha-card><div style='padding:16px;color:var(--error-color,#db4437)'>" +
      "BLE Triage Card error: " +
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
        text-transform: capitalize;
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

customElements.define("ble-triage-card", BleTriageCard);

// Advertise the card in the Lovelace card picker.
window.customCards = window.customCards || [];
window.customCards.push({
  type: "ble-triage-card",
  name: "BLE Triage Card",
  description:
    "Per-proxy GATT slot usage and BLE incident banner for the BLE Triage integration.",
  preview: false,
  documentationURL: "https://github.com/dasimon135/ha-ble-triage",
});
