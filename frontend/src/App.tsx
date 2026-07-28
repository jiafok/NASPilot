import { BrowserRouter, Routes, Route } from 'react-router-dom';
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
import PluginList from './pages/plugins/PluginList';
import NotificationCenter from './pages/notifications/NotificationCenter';
import LogCenter from './pages/system/LogCenter';
import LogFullPage from './pages/system/LogFullPage';
import SystemSettings from './pages/system/SystemSettings';
import AIAssistant from './pages/AIAssistant';
import LogCleanup from './pages/plugins/LogCleanup';
import FileBrowser from './pages/system/FileBrowser';

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
                <Route path="tasks" element={<TaskList />} />
                <Route path="containers" element={<ContainerManager />} />
                <Route path="tools/pt-rss" element={<PT_RSS />} />
                <Route path="tools/alist" element={<AlistUpload />} />
                <Route path="tools/cloudflare" element={<CloudflarePages />} />
                <Route path="tools/cloudflare-ddns" element={<CloudflareDDNSPage />} />
                <Route path="tools/docker-backup" element={<DockerBackup />} />
                <Route path="tools/containers" element={<ContainerManager />} />
                <Route path="tools/log-cleanup" element={<LogCleanup />} />
                <Route path="tools/file-browser" element={<FileBrowser />} />
                <Route path="plugins" element={<PluginList />} />
                <Route path="notifications" element={<NotificationCenter />} />
                <Route path="logs" element={<LogCenter />} />
                <Route path="files" element={<FileBrowser />} />
                <Route path="settings" element={<SystemSettings />} />
                <Route path="ai" element={<AIAssistant />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;
