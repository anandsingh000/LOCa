const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "webpage.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function element() {
    return {
        content: "",
        innerHTML: "",
        children: [],
        style: {},
        scrollTop: 0,
        scrollLeft: 0,
        set textContent(value) {
            this.content = value;
            this.innerHTML = "";
            this.children = [];
        },
        get textContent() { return this.content; },
        appendChild(child) { this.children.push(child); },
        addEventListener() {},
    };
}

function dashboard(fetch) {
    const elements = new Map();
    const document = {
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, element());
            return elements.get(id);
        },
        createElement: element,
        addEventListener() {},
    };
    const context = vm.createContext({ document, fetch, window: {} });
    vm.runInContext(script, context);
    // Column sizing and row DOM event wiring are exercised in the real page.
    // These tests focus on data-request races and failure-state transitions.
    vm.runInContext(`
        setupResizableColumns = function(index) {
            activeTableIndex = index;
            activeTable = {};
        };
        enableRowSelection = function() {};
    `, context);
    return { context, get: (id) => document.getElementById(id) };
}

function response(payload, ok = true) {
    return Promise.resolve({ ok, json: async () => payload });
}

const snapshot = {
    table_html: "<table>SENSEX option chain</table>",
    source: "Upstox live",
    source_timestamp: "2026-10-18T09:00:00Z",
    expiry: "2026-10-22",
    pcr: [["10", "20", "0.5", "VOLUME"]],
};

test("quote updates keep the table status and Refresh now control", async () => {
    const app = dashboard((url) => response(url === "/quotes" ? {
        values: { SENSEX: "76,120.00" },
        sources: { SENSEX: "Upstox live" },
    } : snapshot));
    await app.context.fetchTable("SENSEX", { skipQuotes: true });
    const status = app.get("live-status").textContent;
    await app.context.fetchQuotes();
    assert.equal(app.get("live-status").textContent, status);
    assert.equal(app.get("live-status").children[0].textContent, "Refresh now");
    assert.equal(app.get("sensex_value").textContent, "76,120.00");
    assert.match(app.get("quote-status").textContent, /Upstox live/);
});

test("failed index switches clear the previous index table and PCR", async () => {
    const app = dashboard((url) => url.endsWith("SENSEX")
        ? response({ error: "Upstox token expired" }, false)
        : response({ ...snapshot, table_html: "<table>NIFTY</table>" }));
    await app.context.fetchTable("NIFTY", { skipQuotes: true });
    await app.context.fetchTable("SENSEX", { skipQuotes: true });
    assert.equal(app.get("optionChainTable").innerHTML, "");
    assert.equal(app.get("optionChainTable").textContent, "Upstox token expired");
    assert.equal(app.get("put_call_ratio").textContent, "");
    assert.equal(app.get("table-tools").style.display, "none");
    assert.equal(app.get("live-status").children[0].textContent, "Refresh now");
});

test("failed refresh keeps the last table and marks it as previous data", async () => {
    let requests = 0;
    const app = dashboard(() => ++requests === 1
        ? response(snapshot)
        : response({ error: "Upstox temporarily unavailable" }, false));
    await app.context.fetchTable("SENSEX", { skipQuotes: true });
    vm.runInContext('selectedStrikeKey = "76100";', app.context);
    await app.context.fetchTable("SENSEX", { skipQuotes: true, silent: true });
    assert.equal(app.get("optionChainTable").innerHTML, snapshot.table_html);
    assert.match(app.get("live-status").textContent, /Showing last successful table/);
    assert.equal(vm.runInContext("selectedStrikeKey", app.context), "76100");
});
