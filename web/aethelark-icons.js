/* Aethelark icons: every icon in the agent's windows, drawn in the brand's own geometry (2026-11-05).
 *
 * The owner, 2026-11-05: "the svg icons should be Aethelark style". The style is the Aethelark typeface's, read from its generator
 * (Aethelark_Font/src/aethelark): letters are built from rounded rectangles, never circles; their bowls are wider
 * than tall, with elliptical corners (210 x 134 units); strokes end cut flat; joins are sharp. So here:
 *   - a 24 x 24 grid, one 2-unit stroke, flat ends (butt caps) and sharp joins (miter);
 *   - round things are rounded rectangles with wide corners (rr below), never circles;
 *   - dots are small filled squares; diagonals run at 45 degrees;
 *   - the colour is the text's (currentColor), so an icon is always as bright as the words beside it.
 * No emoji and no text glyphs as icons, anywhere in the app.
 *
 * Use: AeIcons.svg("gear", 17) returns the markup; <i data-ae-icon="gear"></i> is filled in when the page loads
 * (AeIcons.hydrate runs on DOMContentLoaded, and can be called again for markup added later).
 */
(function (root) {
  "use strict";

  /** A rounded rectangle with the typeface's wide, elliptical corners, as path data. */
  function rr(x, y, w, h, rx, ry) {
    rx = Math.min(rx == null ? 3.2 : rx, w / 2);
    ry = Math.min(ry == null ? 2.2 : ry, h / 2);
    var f = function (n) { return +n.toFixed(2); };
    return "M" + f(x + rx) + " " + f(y) + "H" + f(x + w - rx) +
      "A" + f(rx) + " " + f(ry) + " 0 0 1 " + f(x + w) + " " + f(y + ry) + "V" + f(y + h - ry) +
      "A" + f(rx) + " " + f(ry) + " 0 0 1 " + f(x + w - rx) + " " + f(y + h) + "H" + f(x + rx) +
      "A" + f(rx) + " " + f(ry) + " 0 0 1 " + f(x) + " " + f(y + h - ry) + "V" + f(y + ry) +
      "A" + f(rx) + " " + f(ry) + " 0 0 1 " + f(x + rx) + " " + f(y) + "Z";
  }
  /** A filled dot: a small square with the same corner, never a circle. */
  function dot(cx, cy, s) { s = s || 2.6; return { d: rr(cx - s / 2, cy - s / 2, s, s, 0.7, 0.6), fill: true }; }

  var ICONS = {
    // ---- window and controls
    close:   ["M6 6L18 18M18 6L6 18"],
    minus:   ["M5 12H19"],
    gear:    [rr(4.5, 6.5, 15, 11, 4.2, 3), rr(9.5, 10.2, 5, 3.6, 1.4, 1.1), "M8.5 3.5V6.5M15.5 3.5V6.5M8.5 17.5V20.5M15.5 17.5V20.5M1.5 12H4.5M19.5 12H22.5"],
    search:  [rr(3, 5, 13, 10, 3.6, 2.6), "M14.6 14.6L20.5 20.5"],
    send:    ["M4 12H18.5M13.5 7L18.5 12L13.5 17"],
    caret:   ["M9.5 7L14.5 12L9.5 17"],
    mark:    [{ d: "M12 8L19 12L12 16L5 12Z", fill: true }],
    clock:   [rr(4, 7, 16, 13.5, 4, 3), "M12 10.5V14H15M9.5 3.5H14.5M12 3.5V7"],
    check:   ["M6.5 12.5L10.5 16.5L18 8"],
    // ---- memory cards
    user:    [rr(8.5, 3.5, 7, 6.5, 2.6, 2.2), "M4.5 20.5V18.8A3.2 2.2 0 0 1 7.7 16.6H16.3A3.2 2.2 0 0 1 19.5 18.8V20.5"],
    pin:     [rr(5.5, 3, 13, 10.5, 3.8, 2.8), "M8.6 13.5L12 20.5L15.4 13.5", dot(12, 8.2)],
    work:    [rr(3, 8, 18, 12), "M9 8V5H15V8M3 13.5H21"],
    cube:    ["M12 3L20 7.5V16.5L12 21L4 16.5V7.5ZM4 7.5L12 12L20 7.5M12 12V21"],
    sliders: ["M4 7H20M4 12H20M4 17H20", { d: rr(6.5, 5, 4, 4, 0.9, 0.8), fill: true },
              { d: rr(13.5, 10, 4, 4, 0.9, 0.8), fill: true }, { d: rr(9, 15, 4, 4, 0.9, 0.8), fill: true }],
    star:    ["M12 3L13.9 10.1L21 12L13.9 13.9L12 21L10.1 13.9L3 12L10.1 10.1Z"],
    // ---- settings (PR #4 names)
    wave:    ["M3 11V13M7 8V16M11 5V19M15 8V16M19 10.5V13.5"],
    sun:     ["M3 18.5H21M7 18.5V15.8A3.4 2.6 0 0 1 10.4 13.2H13.6A3.4 2.6 0 0 1 17 15.8V18.5M12 4.5V9M4.8 8.4L7.6 11.2M19.2 8.4L16.4 11.2"],
    chat:    [rr(3, 4, 18, 12), "M8 16V20.5L12.8 16"],
    therm:   ["M10 14V5A2 1.8 0 0 1 14 5V14H14.4A3.2 2.6 0 0 1 17.6 16.6V18.4A3.2 2.6 0 0 1 14.4 21H9.6A3.2 2.6 0 0 1 6.4 18.4V16.6A3.2 2.6 0 0 1 9.6 14Z", "M12 8V17.5"],
    clip:    [rr(5, 5, 14, 16), rr(8.5, 3, 7, 4, 1.8, 1.3), "M8.5 11.5H15.5M8.5 15.5H13"],
    phone:   [rr(7, 2.5, 10, 19, 3, 2.4), "M10.5 18H13.5"],
    flask:   ["M9.5 3.5H14.5M10.5 3.5V9L5 19V20.5H19V19L13.5 9V3.5M7.4 15H16.6"],
    power:   ["M12 3V11M7.8 6.5H6.8A3.6 2.8 0 0 0 3.2 9.3V16.7A3.6 2.8 0 0 0 6.8 19.5H17.2A3.6 2.8 0 0 0 20.8 16.7V9.3A3.6 2.8 0 0 0 17.2 6.5H16.2"],
    redo:    ["M20.5 11V16.5A3.6 2.8 0 0 1 16.9 19.3H7.1A3.6 2.8 0 0 1 3.5 16.5V10.3A3.6 2.8 0 0 1 7.1 7.5H19M15.5 4L19 7.5L15.5 11"],
    exit:    ["M14 4H19V20H14M10 8L6 12L10 16M6 12H16"],
    link:    ["M10 8H6.1A3.6 2.8 0 0 0 2.5 10.8V13.2A3.6 2.8 0 0 0 6.1 16H10M14 8H17.9A3.6 2.8 0 0 1 21.5 10.8V13.2A3.6 2.8 0 0 1 17.9 16H14M8 12H16"],
    globe:   [rr(3, 4, 18, 16, 6, 5), rr(8.5, 4, 7, 16, 3.5, 5), "M3 12H21"],
    mail:    [rr(3, 5.5, 18, 13), "M3.5 7.5L12 13L20.5 7.5"],
    bubble:  [rr(3, 4, 18, 13, 4.5, 3.5), "M6.5 17L5 20.5L10.5 17"],
    play:    [rr(3, 5.5, 18, 13), "M10.5 9.5V14.5L14.8 12Z"],
    module:  [rr(3.5, 3.5, 7, 7, 1.8, 1.4), rr(13.5, 3.5, 7, 7, 1.8, 1.4), rr(3.5, 13.5, 7, 7, 1.8, 1.4), rr(13.5, 13.5, 7, 7, 1.8, 1.4)],
    // ---- onboarding, the phone remote, the demo recorder
    mic:     [rr(9, 3, 6, 11, 3, 2.6), "M5.5 10.5V11.8A3.6 2.8 0 0 0 9.1 14.6M18.5 10.5V11.8A3.6 2.8 0 0 1 14.9 14.6M9.1 14.6A3.6 2.8 0 0 0 12 17.5A3.6 2.8 0 0 0 14.9 14.6M12 17.5V21M8.5 21H15.5"],
    lock:    [rr(4.5, 10.5, 15, 10), "M8 10.5V7.6A3.2 2.6 0 0 1 11.2 5H12.8A3.2 2.6 0 0 1 16 7.6V10.5M12 14V17"],
    network: [rr(9, 3, 6, 5, 1.6, 1.2), rr(3, 15.5, 6, 5, 1.6, 1.2), rr(15, 15.5, 6, 5, 1.6, 1.2), "M12 8V11.7H6V15.5M12 11.7H18V15.5"],
    key:     [rr(2.5, 8.5, 8.5, 7, 2.8, 2.2), "M11 12H21.5M17.5 12V15.5M20.5 12V14.5"],
    desktop: [rr(2.5, 4, 19, 12.5), "M12 16.5V20.5M8 20.5H16"],
  };

  // Names other code already uses for the same pictures.
  var ALIAS = { briefcase: "work", settings: "gear", x: "close", ok: "check", "◆": "module", "↳": "link" };

  // Memory cards: Python sends one character per kind of fact (aethelark_web.py, _memory_facts).
  var MEMORY = { "◈": "user", "⌖": "pin", "✦": "work", "⬢": "cube", "≡": "sliders", "✧": "star" };

  function has(name) { return Object.prototype.hasOwnProperty.call(ICONS, ALIAS[name] || name); }

  function svg(name, size, cls) {
    var parts = ICONS[ALIAS[name] || name];
    if (!parts) return "";
    var s = size == null ? 17 : size;
    var body = parts.map(function (p) {
      return typeof p === "string"
        ? '<path d="' + p + '"/>'
        : '<path d="' + p.d + '" fill="currentColor" stroke="none"/>';
    }).join("");
    return '<svg class="ae-ico' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" width="' + s + '" height="' + s + '"' +
      ' fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="butt" stroke-linejoin="miter"' +
      ' stroke-miterlimit="4" aria-hidden="true" focusable="false">' + body + "</svg>";
  }

  /** Fill every <i data-ae-icon="name" data-size="17"> under `scope` (the document by default). */
  function hydrate(scope) {
    var els = (scope || document).querySelectorAll("[data-ae-icon]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.firstElementChild) continue;
      var size = el.getAttribute("data-size");
      el.innerHTML = svg(el.getAttribute("data-ae-icon"), size ? (isNaN(+size) ? size : +size) : "1em");
    }
  }

  var api = { svg: svg, has: has, hydrate: hydrate, names: Object.keys(ICONS), memory: MEMORY, rr: rr };
  root.AeIcons = api;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { hydrate(); });
    else hydrate();
  }
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
