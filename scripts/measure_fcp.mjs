#!/usr/bin/env node

/**
 * Measure first contentful paint in a clean headless Chromium profile.
 *
 * Requirements: Node.js 22+ and Chrome, Edge, or Chromium.
 * Run: node scripts/measure_fcp.mjs $HALLITRAP_PUBLIC_URL
 * Optional: set CHROME_PATH to the browser executable.
 *
 * This script has no npm dependencies. It uses the browser DevTools protocol,
 * PerformanceNavigationTiming, and Paint Timing entries.
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';

const THRESHOLD_MS = 3000;
const START_TIMEOUT_MS = 20_000;
const LOAD_TIMEOUT_MS = 45_000;

function fail(message, code = 2) {
  console.error(message);
  process.exitCode = code;
}

function parseUrl(value) {
  if (!value) {
    throw new Error('Usage: node scripts/measure_fcp.mjs <http-or-https-url>');
  }
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('The URL must use HTTP or HTTPS.');
  }
  return url.toString();
}

function browserCandidates() {
  const configured = process.env.CHROME_PATH ? [process.env.CHROME_PATH] : [];
  const fixed = process.platform === 'win32'
    ? [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
        'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
      ]
    : process.platform === 'darwin'
      ? [
          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
          '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
          '/Applications/Chromium.app/Contents/MacOS/Chromium',
        ]
      : [];
  const executableNames = process.platform === 'win32'
    ? ['chrome.exe', 'msedge.exe', 'chromium.exe']
    : ['google-chrome', 'microsoft-edge', 'chromium', 'chromium-browser'];
  const fromPath = (process.env.PATH ?? '')
    .split(delimiter)
    .flatMap((directory) => executableNames.map((name) => join(directory, name)));
  return [...configured, ...fixed, ...fromPath].filter(Boolean);
}

function findBrowser() {
  const browser = browserCandidates().find((candidate) => existsSync(candidate));
  if (!browser) {
    throw new Error('No Chromium browser was found. Install Chrome, Edge, or Chromium, or set CHROME_PATH.');
  }
  return browser;
}

async function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : null;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function waitForDebugTarget(port) {
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json`);
      if (response.ok) {
        const targets = await response.json();
        const page = targets.find((target) => target.type === 'page');
        if (page?.webSocketDebuggerUrl) return page;
      }
    } catch {
      // The browser has not opened its debugging socket yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('The browser debugging endpoint did not start in 20 seconds.');
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', () => reject(new Error('DevTools WebSocket failed to open.')), { once: true });
    });
    this.socket.addEventListener('message', (event) => this.handleMessage(event));
  }

  handleMessage(event) {
    const message = JSON.parse(event.data);
    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
      return;
    }
    const listeners = this.events.get(message.method) ?? [];
    for (const listener of listeners) listener(message.params);
    this.events.delete(message.method);
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  waitFor(method, timeoutMs) {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(`${method} timed out.`)), timeoutMs);
      const listener = (params) => {
        clearTimeout(timeout);
        resolve(params);
      };
      const listeners = this.events.get(method) ?? [];
      listeners.push(listener);
      this.events.set(method, listeners);
    });
  }

  close() {
    if (this.socket.readyState === WebSocket.OPEN) this.socket.close();
  }
}

async function measure(url) {
  const browserPath = findBrowser();
  const port = await getFreePort();
  const profile = await mkdtemp(join(tmpdir(), 'hallitraap-fcp-'));
  const browser = spawn(browserPath, [
    '--headless=new',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-default-apps',
    '--disable-extensions',
    '--disable-features=Translate',
    '--disable-gpu',
    '--metrics-recording-only',
    '--no-first-run',
    '--no-default-browser-check',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: 'ignore' });

  let client;
  try {
    const target = await waitForDebugTarget(port);
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.open();
    await client.send('Page.enable');
    await client.send('Network.enable');
    await client.send('Network.setCacheDisabled', { cacheDisabled: true });
    const loaded = client.waitFor('Page.loadEventFired', LOAD_TIMEOUT_MS);
    await client.send('Page.navigate', { url });
    await loaded;
    await new Promise((resolve) => setTimeout(resolve, 500));

    const evaluation = await client.send('Runtime.evaluate', {
      returnByValue: true,
      expression: `(() => {
        const navigation = performance.getEntriesByType('navigation')[0];
        const fcp = performance.getEntriesByName('first-contentful-paint')[0];
        return {
          fcp: fcp ? fcp.startTime : null,
          navigation: navigation ? {
            responseStart: navigation.responseStart,
            domContentLoadedEventEnd: navigation.domContentLoadedEventEnd,
            loadEventEnd: navigation.loadEventEnd,
            transferSize: navigation.transferSize,
            encodedBodySize: navigation.encodedBodySize
          } : null,
          userAgent: navigator.userAgent
        };
      })()`,
    });
    const value = evaluation.result.value;
    if (!value || typeof value.fcp !== 'number') {
      throw new Error('The browser did not report a first-contentful-paint entry.');
    }
    const round = (number) => Math.round(number * 100) / 100;
    return {
      url,
      measured_at: new Date().toISOString(),
      first_contentful_paint_ms: round(value.fcp),
      threshold_ms: THRESHOLD_MS,
      pass: value.fcp < THRESHOLD_MS,
      navigation: value.navigation ? {
        response_start_ms: round(value.navigation.responseStart),
        dom_content_loaded_end_ms: round(value.navigation.domContentLoadedEventEnd),
        load_event_end_ms: round(value.navigation.loadEventEnd),
        transfer_size_bytes: value.navigation.transferSize,
        encoded_body_size_bytes: value.navigation.encodedBodySize,
      } : null,
      browser_user_agent: value.userAgent,
    };
  } finally {
    if (client) {
      try {
        await client.send('Browser.close');
      } catch {
        // The browser can close its socket before it sends the response.
      }
      client.close();
    }
    if (browser.exitCode === null) {
      const exited = new Promise((resolve) => browser.once('exit', resolve));
      browser.kill();
      await Promise.race([
        exited,
        new Promise((resolve) => setTimeout(resolve, 3000)),
      ]);
    }
    let removeError;
    for (let attempt = 1; attempt <= 30; attempt += 1) {
      try {
        await rm(profile, { recursive: true, force: true });
        removeError = undefined;
        break;
      } catch (error) {
        removeError = error;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    if (removeError) throw removeError;
  }
}

try {
  const url = parseUrl(process.argv[2]);
  const result = await measure(url);
  console.log(JSON.stringify(result, null, 2));
  if (!result.pass) {
    fail(`FCP ${result.first_contentful_paint_ms} ms is not below ${THRESHOLD_MS} ms.`, 1);
  }
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}
