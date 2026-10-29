import { Capacitor, CapacitorHttp } from '@capacitor/core';
import { Preferences } from '@capacitor/preferences';

const SERVER_ADDRESS_KEY = 'naspilot_server_address';
let savedServerAddress: string | null = null;

export function isNativeApp(): boolean {
  return Capacitor.isNativePlatform();
}

export function normalizeServerAddress(value: string): string {
  const trimmed = value.trim();
  const candidate = /^[a-z][a-z\d+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
  const address = new URL(candidate);
  if (!['http:', 'https:'].includes(address.protocol)) throw new Error('地址必须使用 http:// 或 https://');
  if (address.username || address.password || address.search || address.hash) throw new Error('地址不能包含账号、密码、查询参数或锚点');
  if (address.hostname === 'localhost' || address.hostname === '::1' || address.hostname.startsWith('127.')) {
    throw new Error('localhost 指向手机本身，请填写 NAS 的局域网 IP 或域名');
  }
  return `${address.origin}${address.pathname.replace(/\/+$/, '')}`;
}

export async function loadServerAddress(): Promise<string | null> {
  if (!isNativeApp()) return null;
  const { value } = await Preferences.get({ key: SERVER_ADDRESS_KEY });
  try {
    savedServerAddress = value ? normalizeServerAddress(value) : null;
  } catch {
    savedServerAddress = null;
  }
  return savedServerAddress;
}

export async function saveServerAddress(value: string): Promise<string> {
  const address = normalizeServerAddress(value);
  await Preferences.set({ key: SERVER_ADDRESS_KEY, value: address });
  savedServerAddress = address;
  return address;
}

export function getServerAddress(): string | null {
  return savedServerAddress;
}

export function getApiBaseUrl(): string {
  if (!isNativeApp()) return '/api/v1';
  return savedServerAddress ? `${savedServerAddress}/api/v1` : '/api/v1';
}

export function resolveServerPath(path: string): string {
  if (!isNativeApp() || !savedServerAddress || !path.startsWith('/')) return path;
  return `${savedServerAddress}${path}`;
}

export type ProbeResult =
  | { state: 'ok'; status: number }
  | { state: 'http-error'; status: number }
  | { state: 'unreachable'; reason: string };

/**
 * Ask the NAS whether it is reachable, from two very different channels.
 *
 * A WebView `fetch` cannot tell "server down" from "server rejected the
 * origin": both surface as an opaque `TypeError`. The native channel reports
 * the real status, so the UI can say *which* one is wrong instead of a generic
 * "连接失败" that sends the user hunting for the wrong problem.
 */
/** Per-attempt ceiling. Was 10s, which a slow reverse proxy exceeds. */
const PROBE_TIMEOUT_MS = 15000;
/** One retry: a proxy hiccup (522, a dropped tunnel) is genuinely transient. */
const PROBE_ATTEMPTS = 2;

/** Statuses a reverse proxy returns when it cannot reach the origin at all. */
const GATEWAY_STATUSES = new Set([502, 503, 504, 520, 521, 522, 523, 524]);

/** True when the address answered, but the proxy could not reach the NAS. */
export function isGatewayStatus(status: number): boolean {
  return GATEWAY_STATUSES.has(status);
}

export async function probeServer(address: string): Promise<{ native: ProbeResult; browser: ProbeResult }> {
  const url = `${address}/api/v1/membership/catalog`;
  return { native: await probeNative(url), browser: await probeBrowser(url) };
}

/**
 * Retry a probe before believing it.
 *
 * Measured on the user's HTTPS host, the same URL returned 200/0.7s, 200/4.7s,
 * HTTP 522 (Cloudflare could not reach the origin) and one 30s timeout within
 * six requests. Reporting failure from a single attempt made a working server
 * look broken.
 */
async function probeNative(url: string): Promise<ProbeResult> {
  if (!isNativeApp()) return { state: 'unreachable', reason: 'web' };
  let last: ProbeResult = { state: 'unreachable', reason: 'network' };
  for (let attempt = 0; attempt < PROBE_ATTEMPTS; attempt += 1) {
    try {
      const response = await CapacitorHttp.request({
        url,
        method: 'GET',
        connectTimeout: PROBE_TIMEOUT_MS,
        readTimeout: PROBE_TIMEOUT_MS,
      });
      const status = response.status;
      if (status >= 200 && status < 300) return { state: 'ok', status };
      last = { state: 'http-error', status };
      // A gateway error means "the tunnel dropped", not "wrong address": retry.
      if (!isGatewayStatus(status)) return last;
    } catch (reason) {
      last = { state: 'unreachable', reason: reason instanceof Error ? reason.message : 'network' };
    }
  }
  return last;
}

async function probeBrowser(url: string): Promise<ProbeResult> {
  let last: ProbeResult = { state: 'unreachable', reason: 'blocked' };
  for (let attempt = 0; attempt < PROBE_ATTEMPTS; attempt += 1) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (response.ok) return { state: 'ok', status: response.status };
      last = { state: 'http-error', status: response.status };
      if (!isGatewayStatus(response.status)) return last;
    } catch (reason) {
      last = {
        state: 'unreachable',
        reason: reason instanceof Error && reason.name === 'AbortError' ? 'timeout' : 'blocked',
      };
    } finally {
      window.clearTimeout(timeout);
    }
  }
  return last;
}