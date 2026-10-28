import { Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { Spin } from 'antd';
import { useEffect, useState } from 'react';

export default function AuthGuard({ children, adminOnly = false, allowExpired = false }: { children: React.ReactNode; adminOnly?: boolean; allowExpired?: boolean }) {
  const { user, loading } = useAuth();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!user?.membership?.expires_at) return;
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, [user?.membership?.expires_at]);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" tip="Loading..." />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (adminOnly && !user.is_admin) return <Navigate to="/m" replace />;
  const membership = user.membership;
  if (!allowExpired && !user.is_admin && membership && (!membership.can_learn || (membership.expires_at && Date.parse(membership.expires_at) <= now))) {
    return <Navigate to="/membership" replace />;
  }

  return <>{children}</>;
}
