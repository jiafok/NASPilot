export interface UserInfo {
  id: number;
  username: string;
  email: string | null;
  is_admin: boolean;
}

export function getToken(): string | null {
  return localStorage.getItem('token');
}

export function setToken(token: string): void {
  localStorage.setItem('token', token);
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

export function logout(): void {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  window.location.href = '/login';
}

export function isAuthenticated(): boolean {
  return !!getToken();
}
