import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
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
import AIAssistant from './pages/AIAssistant';
import LogCleanup from './pages/plugins/LogCleanup';
import FileBrowser from './pages/system/FileBrowser';
import BtrfsCleanup from './pages/plugins/BtrfsCleanup';
import RcloneMount from './pages/plugins/RcloneMount';

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
              <Route path="/logs/full" element={<LogFullPage />} />
              <Route path="/" element={<AuthGuard><MainLayout /></AuthGuard>}>
                <Route index element={<Dashboard />} />
                <Route path="automation" element={<TaskList />} />
                <Route path="applications" element={<Navigate to="/applications/pt-rss" replace />} />
                <Route path="containers" element={<ContainerManager />} />
                <Route path="files" element={<FileBrowser />} />
                <Route path="logs" element={<LogCenter />} />
                <Route path="settings" element={<SystemSettings />} />
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
              </Route>
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;
