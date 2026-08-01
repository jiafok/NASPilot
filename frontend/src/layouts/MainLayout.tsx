import { useEffect, useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Dropdown, Avatar, theme, Select, Grid } from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined,
  ThunderboltOutlined,
  DatabaseOutlined,
  AppstoreOutlined,
  FileTextOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  UserOutlined,
  LogoutOutlined,
  RobotOutlined,
  MenuOutlined,
  FolderOpenOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../hooks/useAuth';

const { Header, Sider, Content } = Layout;
const { useBreakpoint } = Grid;

const locales = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en-US', label: 'English' },
];

export default function MainLayout() {
  const { t, i18n } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const { token: { colorBgContainer } } = theme.useToken();

  useEffect(() => { setMobileOpen(false); }, [location.pathname]);

  const menuItems: MenuProps['items'] = [
    { key: '/', icon: <DashboardOutlined />, label: t('nav.dashboard') },
    { key: '/automation', icon: <ThunderboltOutlined />, label: '任务中心' },
    {
      key: '/applications',
      icon: <AppstoreOutlined />,
      label: '集成工具',
      children: [
        { key: '/applications/pt-rss', label: 'PT RSS 下载' },
        { key: '/applications/alist-upload', label: 'AList 上传' },
        { key: '/applications/cloudflare-ddns', label: 'Cloudflare DDNS' },
        { key: '/applications/cloudflare-pages', label: 'Cloudflare Pages' },
        { key: '/applications/docker-backup', label: 'Docker 备份' },
        { key: '/applications/log-cleanup', label: '日志清理' },
        { key: '/applications/btrfs-cleanup', label: 'Btrfs 清理' },
        { key: '/applications/rclone-mount', label: 'Rclone 挂载' },
      ],
    },
    { key: '/containers', icon: <DatabaseOutlined />, label: t('nav.containerManager') },
    { key: '/files', icon: <FolderOpenOutlined />, label: 'File Manager' },
    { key: '/logs', icon: <FileTextOutlined />, label: t('nav.logs') },
    { key: '/settings', icon: <SettingOutlined />, label: t('nav.settings') },
    { key: '/ai', icon: <RobotOutlined />, label: t('nav.aiAssistant') },
  ];

  const userMenuItems: MenuProps['items'] = [
    { key: 'user', label: user?.username || 'Admin', disabled: true },
    { type: 'divider' },
    { key: 'logout', icon: <LogoutOutlined />, label: t('auth.logout'), onClick: logout },
  ];

  const selectedKey = (() => {
    const p = location.pathname;
    if (p === '/' || p === '') return '/';
    if (p.startsWith('/automation') || p.startsWith('/tasks')) return '/automation';
    if (p === '/applications' || p.startsWith('/plugins')) return '/applications/pt-rss';
    if (p.startsWith('/applications/pt-rss') || p.startsWith('/tools/pt-rss')) return '/applications/pt-rss';
    if (p.startsWith('/applications/alist-upload') || p.startsWith('/tools/alist')) return '/applications/alist-upload';
    if (p.startsWith('/applications/cloudflare-pages') || p.startsWith('/tools/cloudflare')) return '/applications/cloudflare-pages';
    if (p.startsWith('/applications/docker-backup') || p.startsWith('/tools/docker-backup')) return '/applications/docker-backup';
    if (p.startsWith('/applications/log-cleanup') || p.startsWith('/tools/log-cleanup')) return '/applications/log-cleanup';
    if (p.startsWith('/applications/cloudflare-ddns') || p.startsWith('/tools/cloudflare-ddns')) return '/applications/cloudflare-ddns';
    if (p.startsWith('/applications/btrfs-cleanup')) return '/applications/btrfs-cleanup';
    if (p.startsWith('/applications/rclone-mount')) return '/applications/rclone-mount';
    if (p.startsWith('/containers')) return '/containers';
    if (p.startsWith('/files')) return '/files';
    if (p.startsWith('/logs')) return '/logs';
    if (p.startsWith('/settings') || p.startsWith('/notifications')) return '/settings';
    if (p.startsWith('/ai')) return '/ai';
    const m = menuItems.find((x: any) => x.key === p);
    return (m as any)?.key || '/';
  })();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!isMobile && (
        <Sider trigger={null} collapsible collapsed={collapsed} theme="dark"
          style={{ background: 'linear-gradient(180deg, #1a1a2e 0%, #16213e 100%)' }}>
          <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: collapsed ? 16 : 20, fontWeight: 700 }}>
            {collapsed ? '🚀' : '🚀 NASPilot'}
          </div>
          <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]}
            items={menuItems} onClick={({ key }) => navigate(key)} style={{ background: 'transparent' }} />
        </Sider>
      )}
      <Layout>
        <Header style={{ padding: '0 16px', background: colorBgContainer, display: 'flex', alignItems: 'center', justifyContent: 'space-between', boxShadow: '0 1px 4px rgba(0,0,0,0.08)', position: 'sticky', top: 0, zIndex: 100 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {isMobile && <Button type="text" icon={<MenuOutlined />} onClick={() => setMobileOpen(true)} />}
            {!isMobile && <Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} />}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Select size="small" value={i18n.language} onChange={(v) => { i18n.changeLanguage(v); localStorage.setItem('lang', v); }}
              options={locales} style={{ width: 90 }} popupMatchSelectWidth={false} />
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
              <Avatar style={{ cursor: 'pointer', backgroundColor: '#667eea' }} icon={<UserOutlined />} />
            </Dropdown>
          </div>
        </Header>
        {isMobile && (
          <div style={{ display: mobileOpen ? 'block' : 'none', position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 200 }}>
            <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.4)' }} onClick={() => setMobileOpen(false)} />
            <div style={{ position: 'absolute', top: 0, left: 0, bottom: 0, width: 260, background: 'linear-gradient(180deg, #1a1a2e 0%, #16213e 100%)', overflow: 'auto' }}>
              <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 20, fontWeight: 700 }}>🚀 NASPilot</div>
              <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]}
                items={menuItems} onClick={({ key }) => { navigate(key); setMobileOpen(false); }} style={{ background: 'transparent' }} />
            </div>
          </div>
        )}
        <Content style={{ padding: isMobile ? 12 : 24, minHeight: 280, background: '#f5f5f5' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
