import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { ConfigProvider, App as AntApp, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AuthProvider } from './hooks/useAuth';
import AuthGuard from './components/AuthGuard';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/Login';
import Dashboard from './pages/Dashboard';
import TaskList from './pages/tasks/TaskList';
import PT_RSS from './pages/PT_RSS';
import AlistUpload from './pages/AlistUpload';
import CloudflarePages from './pages/CloudflarePages';
import CloudflareDDNSPage from './pages/CloudflareDDNSPage';
import DockerBackup from './pages/DockerBackup';
import ContainerManager from './pages/ContainerManager';
import NotificationCenter from './pages/notifications/NotificationCenter';
import LogCenter from './pages/system/LogCenter';
import LogFullPage from './pages/system/LogFullPage';
import SystemSettings from './pages/system/SystemSettings';
import UserManager from './pages/system/UserManager';
import AIAssistant from './pages/AIAssistant';
import LogCleanup from './pages/plugins/LogCleanup';
import FileBrowser from './pages/system/FileBrowser';
import BtrfsCleanup from './pages/plugins/BtrfsCleanup';
import RcloneMount from './pages/plugins/RcloneMount';
import MediaCenter from './pages/media/MediaCenter';
import EnglishCenter from './pages/english/EnglishCenter';
import MobileApp from './pwa/MobileApp';
import MembershipPage from './pwa/MembershipPage';

/**
 * Phones get the PWA shell, not the admin-width table view.
 *
 * A phone screen cannot show the desktop course table without horizontal
 * scrolling, which the UI contract forbids. `?desktop=1` escapes the redirect
 * for the rare case someone wants the dense view on a tablet-sized window.
 */
function MobileGate({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const narrow = typeof window !== 'undefined' && window.matchMedia('(max-width: 640px)').matches;
  const forced = new URLSearchParams(location.search).get('desktop') === '1';
  if (narrow && !forced) return <Navigate to="/m" replace />;
  return <>{children}</>;
}

function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: { colorPrimary: '#667eea', borderRadius: 8 },
      }}
    >
      <AntApp>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<LoginPage registerMode />} />
              <Route path="/membership" element={<AuthGuard allowExpired><MembershipPage /></AuthGuard>} />
              <Route path="/logs/full" element={<AuthGuard adminOnly><LogFullPage /></AuthGuard>} />
              {/* Mobile PWA: its own shell (5 tabs) and its own full-screen player.
                  Standalone route so a phone never has to load the admin layout. */}
              <Route path="/m" element={<AuthGuard><MobileApp /></AuthGuard>} />
              <Route path="/" element={<AuthGuard adminOnly><MainLayout /></AuthGuard>}>
                <Route index element={<Dashboard />} />
                <Route path="automation" element={<TaskList />} />
                <Route path="applications" element={<Navigate to="/applications/pt-rss" replace />} />
                <Route path="containers" element={<ContainerManager />} />
                <Route path="files" element={<FileBrowser />} />
                <Route path="logs" element={<LogCenter />} />
                <Route path="settings" element={<SystemSettings />} />
                <Route path="users" element={<UserManager />} />
                <Route path="ai" element={<AIAssistant />} />

                {/* Backward-compatible redirects for legacy /tools/* bookmarks */}
                <Route path="tools/pt-rss" element={<Navigate to="/applications/pt-rss" replace />} />
                <Route path="tools/alist" element={<Navigate to="/applications/alist-upload" replace />} />
                <Route path="tools/cloudflare" element={<Navigate to="/applications/cloudflare-pages" replace />} />
                <Route path="tools/cloudflare-ddns" element={<Navigate to="/applications/cloudflare-ddns" replace />} />
                <Route path="tools/docker-backup" element={<Navigate to="/applications/docker-backup" replace />} />
                <Route path="tools/log-cleanup" element={<Navigate to="/applications/log-cleanup" replace />} />
                <Route path="tools/containers" element={<Navigate to="/containers" replace />} />
                <Route path="tools/file-browser" element={<Navigate to="/files" replace />} />
                <Route path="notifications" element={<NotificationCenter />} />

                {/* Application aliases */}
                <Route path="applications/pt-rss" element={<PT_RSS />} />
                <Route path="applications/alist-upload" element={<AlistUpload />} />
                <Route path="applications/docker-backup" element={<DockerBackup />} />
                <Route path="applications/cloudflare-ddns" element={<CloudflareDDNSPage />} />
                <Route path="applications/cloudflare-pages" element={<CloudflarePages />} />
                <Route path="applications/log-cleanup" element={<LogCleanup />} />
                <Route path="applications/btrfs-cleanup" element={<BtrfsCleanup />} />
                <Route path="applications/rclone-mount" element={<RcloneMount />} />

                {/* Media Download Center */}
                <Route path="media" element={<Navigate to="/media/subscriptions" replace />} />
                <Route path="media/subscriptions" element={<MediaCenter view="subscriptions" />} />
                <Route path="media/tasks" element={<MediaCenter view="tasks" />} />
                <Route path="media/history" element={<MediaCenter view="history" />} />
                {/* Legacy YouTube-only page → unified media center */}
                <Route path="media/youtube" element={<Navigate to="/media/subscriptions" replace />} />

                {/* English Learning Center */}
                <Route path="english" element={<MobileGate><EnglishCenter /></MobileGate>} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;
