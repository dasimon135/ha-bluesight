// Runs the shipped card under Node with just enough DOM to render it.
//
// Usage: node card_harness.js '<scenario json>'
// Scenario: { config, states, entities?, devices?, language?, tap?, key?, close? }
//   states  — the `hass.states` map to render against
//   tap     — also fire the tile's tap handler, and serialise what it opened
//   key     — press that key on the tile's row instead, through its listeners
//   close   — then close whatever the tap or the key opened
// Prints: { html, size, dialog, attrs, focused } — the card's rendered shadow
// tree, what it answers for `getCardSize()`, the tree of anything mounted on
// document.body, the attributes of every element that has any (keyed by class
// name), and the class name of whatever holds the focus.
//
// The card builds its DOM with createElement/appendChild and reads back only a
// handful of properties, so a fake element tree is enough to exercise its real
// render path without a browser. Serialisation is deliberately crude — a tag,
// its classes and its text — because that is what an assertion needs to see.
//
// By default no catalogue is served: `fetch` never resolves, so every string
// comes from the card's embedded English, which `test_card_locale.py` already
// pins to the shipped catalogue. That is the cold-load path, and it is what
// most scenarios want. A scenario asking for a `language` other than English
// gets the SHIPPED catalogues served from disk instead and one turn of the
// loop to apply them -- otherwise its assertions would be about English.

"use strict";

const fs = require("fs");
const path = require("path");

class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.listeners = {};
    this.attributes = {};
    this._text = "";
    this.classList = {
      add: (...names) => {
        const have = new Set(this.className.split(" ").filter(Boolean));
        names.forEach((n) => have.add(n));
        this.className = [...have].join(" ");
      },
      remove: (...names) => {
        const have = new Set(this.className.split(" ").filter(Boolean));
        names.forEach((n) => have.delete(n));
        this.className = [...have].join(" ");
      },
      toggle: (name, on) => (on ? this.classList.add(name) : this.classList.remove(name)),
    };
  }

  get textContent() {
    return this._text;
  }

  set textContent(value) {
    this._text = value == null ? "" : String(value);
    this.children = [];
  }

  // The card only ever assigns "" — a reset before redrawing a container.
  set innerHTML(value) {
    this._text = value ? String(value) : "";
    this.children = [];
  }

  get innerHTML() {
    return serialise(this);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((c) => c !== child);
    return child;
  }

  remove() {}
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  focus() {
    global.__focused = this;
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  removeEventListener() {}
  dispatchEvent() {
    return true;
  }
  attachShadow() {
    this.shadowRoot = new FakeElement("#shadow");
    return this.shadowRoot;
  }
  querySelector() {
    return null;
  }
  querySelectorAll() {
    return [];
  }
}

/** `<tag class="…">text<child…></tag>`, one flat string per tree. */
function serialise(el) {
  if (!el || typeof el !== "object") return "";
  // The stylesheet is one long string of CSS that every assertion would have to
  // step around -- and a class name in a selector is not a class name on a node.
  if (el.tagName === "style") return "<style/>";
  const cls = el.className ? ` class="${el.className}"` : "";
  // A shadow root is where the popup keeps everything it draws, so it has to be
  // part of what an assertion can see.
  const shadow = el.shadowRoot ? serialise(el.shadowRoot) : "";
  const inner = shadow + el._text + el.children.map(serialise).join("");
  return `<${el.tagName}${cls}>${inner}</${el.tagName}>`;
}

/** Every element under `el`, shadow roots included. */
function walk(el, visit) {
  if (!el || typeof el !== "object") return;
  visit(el);
  if (el.shadowRoot) walk(el.shadowRoot, visit);
  el.children.forEach((child) => walk(child, visit));
}

const registry = {};
global.HTMLElement = FakeElement;
global.customElements = {
  get: (name) => registry[name],
  define: (name, cls) => {
    registry[name] = cls;
  },
};
global.document = {
  // A registered custom element is upgraded on creation, as a browser does:
  // the popup creates a `bluesight-card` and then calls setConfig on it.
  createElement: (tag) => {
    if (!registry[tag]) return new FakeElement(tag);
    // A custom element's constructor calls super() with no tag, so name it here
    // the way a browser does -- the popup is found by its tag.
    const el = new registry[tag]();
    el.tagName = tag;
    return el;
  },
  querySelectorAll: () => [],
  body: new FakeElement("body"),
};
global.window = global;
// The popup binds Escape on the window.
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.console = { ...console, info: () => {} };
// The card fetches its string catalogue and repaints when it lands. Here it
// never lands, which is the cold-load path: embedded English.
const localeDir = path.join(
  __dirname, "..", "custom_components", "bluesight", "frontend", "www", "locale"
);
const servesCatalogues = (scenarioLanguage) =>
  Boolean(scenarioLanguage) && scenarioLanguage !== "en";

global.fetch = (url) => {
  if (!servesCatalogues(JSON.parse(process.argv[2]).language)) {
    return new Promise(() => {});
  }
  // `/bluesight/locale/incidents.<lang>.json?v=...`
  const name = String(url).split("/").pop().split("?")[0];
  const file = path.join(localeDir, name);
  if (!fs.existsSync(file)) {
    return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
  }
  const body = JSON.parse(fs.readFileSync(file, "utf8"));
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
};

const cardFile = path.join(
  __dirname,
  "..",
  "custom_components",
  "bluesight",
  "frontend",
  "www",
  "bluesight-card.js"
);
// eslint-disable-next-line no-eval
(0, eval)(fs.readFileSync(cardFile, "utf8"));

const scenario = JSON.parse(process.argv[2]);
const Card = registry["bluesight-card"];
const card = new Card();
main();

async function main() {
card.setConfig(scenario.config || {});
card.hass = {
  language: scenario.language || "en",
  states: scenario.states || {},
  // The frontend's view of the entity and device registries. Left undefined
  // unless the scenario gives them, which is what an older Home Assistant
  // looks like to the card.
  entities: scenario.entities,
  devices: scenario.devices,
};

if (scenario.tap) {
  card._onTileTap();
}

if (scenario.key) {
  walk(card.shadowRoot, (el) => {
    if (!el.className.split(" ").includes("tile")) return;
    (el.listeners.keydown || []).forEach((fn) =>
      fn({ key: scenario.key, preventDefault: () => {} })
    );
  });
}

if (scenario.close) {
  card._closeCardDialog();
}

const attrs = {};
[card.shadowRoot, global.document.body].forEach((root) =>
  walk(root, (el) => {
    if (Object.keys(el.attributes).length) attrs[el.className] = el.attributes;
  })
);

// One macrotask is enough to drain every catalogue promise: they all resolve
// synchronously, and microtasks run to exhaustion before the next macrotask.
if (servesCatalogues(scenario.language)) {
  await new Promise((resolve) => setImmediate(resolve));
}

process.stdout.write(
  JSON.stringify({
    html: serialise(card.shadowRoot),
    size: card.getCardSize(),
    dialog: serialise(global.document.body),
    attrs,
    focused: global.__focused ? global.__focused.className : null,
  })
);
}
