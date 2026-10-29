import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { clearSession, getRefreshToken, getToken, setRefreshToken, setToken } from './auth';
import { getApiBaseUrl } from './serverAddress';

const api = axios.create({
  baseURL: getApiBaseUrl(),
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

// 请求拦截器：自动加 JWT
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/**
 * Single in-flight refresh.
 *
 * The access token lasts 24h and every request that sees a 401 wants to renew
 * it. Without coalescing, a page that fires six requests at once would send six
 * refresh calls — and because refresh tokens rotate, five of them would be
 * rejected and the whole session revoked as a suspected leak.
 */
let refreshing: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;
  try {
    // Bare axios: going through `api` would recurse into this same interceptor.
    const res = await axios.post(`${api.defaults.baseURL}/auth/refresh`, { refresh_token: refreshToken });
    const nextAccess = res.data?.access_token as string | undefined;
    if (!nextAccess) return null;
    setToken(nextAccess);
    setRefreshToken(res.data?.refresh_token);
    return nextAccess;
  } catch {
    return null;
  }
}

// 响应拦截器：401 → 先尝试静默续期，失败才跳登录
api.interceptors.response.use(
  (res) => res,
  async (err: AxiosError) => {
    if (err.response?.status === 402 && window.location.pathname !== '/membership') {
      window.location.href = '/membership';
      return Promise.reject(err);
    }
    const original = err?.config as
      | (InternalAxiosRequestConfig & { __retriedWithoutV1?: boolean; __retriedAuth?: boolean })
      | undefined;

    // Compatibility fallback: some local deployments expose /api/* instead of /api/v1/*
    if (err.response?.status === 405 && original && !original.__retriedWithoutV1) {
      const oldBase = String(original.baseURL || api.defaults.baseURL || '');
      if (oldBase.includes('/api/v1')) {
        original.__retriedWithoutV1 = true;
        original.baseURL = oldBase.replace('/api/v1', '/api');
        return api.request(original);
      }
    }

    if (err.response?.status === 401 && original && !original.__retriedAuth) {
      original.__retriedAuth = true;
      refreshing = refreshing ?? refreshAccessToken();
      const nextToken = await refreshing;
      refreshing = null;
      if (nextToken) {
        original.headers = original.headers ?? ({} as InternalAxiosRequestConfig['headers']);
        original.headers.Authorization = `Bearer ${nextToken}`;
        return api.request(original);
      }
      // Silent renewal failed: drop the session without a scary dialog. A popup
      // here reads as "the app is broken" when all it needs is a re-login.
      clearSession();
      window.location.href = '/login';
      return Promise.reject(err);
    }

    return Promise.reject(err);
  },
);

export default api;
