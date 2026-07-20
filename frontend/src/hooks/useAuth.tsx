import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import api from '../utils/api';
import { getToken, setToken, setUser, getUser, logout } from '../utils/auth';
import type { UserInfo } from '../utils/auth';

interface AuthContextType {
  user: UserInfo | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  login: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<UserInfo | null>(getUser());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (getToken()) {
      api.get('/auth/me')
        .then((res) => {
          setUserState(res.data);
          setUser(res.data);
        })
        .catch(() => logout())
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const loginFn = useCallback(async (username: string, password: string) => {
    const res = await api.post('/auth/login', { username, password });
    setToken(res.data.access_token);
    const me = await api.get('/auth/me');
    setUserState(me.data);
    setUser(me.data);
  }, []);

  const logoutFn = useCallback(() => {
    setUserState(null);
    logout();
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login: loginFn, logout: logoutFn }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
