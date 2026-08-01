import axios from 'axios';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

// 请求拦截器：自动加 JWT
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器：401 跳登录
api.interceptors.response.use(
  (res) => res,
  async (err) => {
    const original = err?.config as any;
    // Compatibility fallback: some local deployments expose /api/* instead of /api/v1/*
    if (err.response?.status === 405 && original && !original.__retriedWithoutV1) {
      const oldBase = String(original.baseURL || api.defaults.baseURL || '');
      if (oldBase.includes('/api/v1')) {
        original.__retriedWithoutV1 = true;
        original.baseURL = oldBase.replace('/api/v1', '/api');
        return api.request(original);
      }
    }

    if (err.response?.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    }
    return Promise.reject(err);
  },
);

export default api;
