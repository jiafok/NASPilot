export interface UserInfo {
  id: number;
  username: string;
  email: string | null;
  is_admin: boolean;
  membership?: {
    plan: string;
    can_learn: boolean;
    expires_at: string | null;
    days_remaining: number | null;
  } | null;
}

export function getToken(): string | null {
  return localStorage.getItem('token');
}

export function setToken(token: string): void {
  localStorage.setItem('token', token);
}

/**
 * Long-lived credential used to renew the 24h access token.
 *
 * Without it the user had to retype their password every morning — the single
 * worst retention problem for a commute-time app.
 */
export function getRefreshToken(): string | null {
  return localStorage.getItem('refresh_token');
}

export function setRefreshToken(token: string | null | undefined): void {
  if (token) localStorage.setItem('refresh_token', token);
  else localStorage.removeItem('refresh_token');
}

export function setUser(user: UserInfo): void {
  localStorage.setItem('user', JSON.stringify(user));
}

export function getUser(): UserInfo | null {
  const raw = localStorage.getItem('user');
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function clearSession(): void {
  localStorage.removeItem('token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user');
}

export function logout(): void {
  clearSession();
  window.location.href = '/login';
}

export function isAuthenticated(): boolean {
  return !!getToken();
}
