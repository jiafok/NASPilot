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
export async function probeServer(address: string): Promise<{ native: ProbeResult; browser: ProbeResult }> {
  const url = `${address}/api/v1/membership/catalog`;
  return { native: await probeNative(url), browser: await probeBrowser(url) };
}

async function probeNative(url: string): Promise<ProbeResult> {
  if (!isNativeApp()) return { state: 'unreachable', reason: 'web' };
  try {
    const response = await CapacitorHttp.request({ url, method: 'GET', connectTimeout: 10000, readTimeout: 10000 });
    const status = response.status;
    return status >= 200 && status < 300 ? { state: 'ok', status } : { state: 'http-error', status };
  } catch (reason) {
    return { state: 'unreachable', reason: reason instanceof Error ? reason.message : 'network' };
  }
}

async function probeBrowser(url: string): Promise<ProbeResult> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    return response.ok ? { state: 'ok', status: response.status } : { state: 'http-error', status: response.status };
  } catch (reason) {
    return { state: 'unreachable', reason: reason instanceof Error && reason.name === 'AbortError' ? 'timeout' : 'blocked' };
  } finally {
    window.clearTimeout(timeout);
  }
}