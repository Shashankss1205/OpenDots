/* Bundled by live_react.py into the protected owner validation harness. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { transform } = require('sucrase');
const { Window } = require('happy-dom');
const { computeAccessibleName } = require('dom-accessibility-api');
const React = require('react');
const { createRoot } = require('react-dom/client');
const { renderToStaticMarkup } = require('react-dom/server');

async function main() {
  const window = new Window({ url: 'http://localhost/' });
  global.window = window;
  global.document = window.document;
  global.HTMLElement = window.HTMLElement;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const source = fs.readFileSync('src/SearchButton.jsx', 'utf8');
  const compiled = transform(source, { transforms: ['jsx', 'imports'], production: false }).code;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(name => {
    assert.equal(name, 'react', 'Only the owner-provided React import is allowed');
    return React;
  }, module, module.exports);
  const { SearchButton } = module.exports;
  assert.equal(typeof SearchButton, 'function');
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  let clicked = 0;
  await React.act(async () => root.render(React.createElement(SearchButton, { onSearch: () => clicked++ })));
  const button = container.querySelector('button');
  assert.ok(button, 'Real client render must produce a button');
  assert.equal(computeAccessibleName(button), 'Search', 'Rendered button must have a Search accessible name');
  assert.equal(button.type, 'button', 'Button must avoid implicit form submission');
  assert.equal(button.querySelector('svg').getAttribute('aria-hidden'), 'true', 'Decorative SVG must be hidden from accessibility');
  await React.act(async () => button.dispatchEvent(new window.MouseEvent('click', { bubbles: true })));
  assert.equal(clicked, 1, 'Actual DOM click must call onSearch exactly once');
  const server = document.createElement('div');
  server.innerHTML = renderToStaticMarkup(React.createElement(SearchButton));
  assert.equal(computeAccessibleName(server.querySelector('button')), 'Search', 'Actual server-rendered button must retain its accessible name');
  await React.act(async () => root.unmount());
  window.happyDOM.abort();
  console.log(`PASS: React ${React.version}; client render, accessible name, non-submit type, decorative SVG, click callback, server render`);
}
main().then(() => process.exit(0)).catch(error => { console.error(error.message); process.exit(1); });
