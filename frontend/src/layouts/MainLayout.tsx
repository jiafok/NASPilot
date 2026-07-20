import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Dropdown, Avatar, theme } from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined,
  ThunderboltOutlined,
  CloudUploadOutlined,
  GlobalOutlined,
  ToolOutlined,
  BellOutlined,
  AppstoreOutlined,
  FileTextOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  UserOutlined,
  LogoutOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { useAuth } from '../hooks/useAuth';

const { Header, Sider, Content } = Layout;

const menuItems: MenuProps['items'] = [
  { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/tasks', icon: <ThunderboltOutlined />, label: '任务中心' },
  {
    key: '/tools',
    icon: <ToolOutlined />,
    label: '集成工具',
    children: [
      { key: '/tools/pt-rss', icon: <CloudUploadOutlined />, label: 'PT RSS' },
      { key: '/tools/alist', icon: <CloudUploadOutlined />, label: 'AList 上传' },
      { key: '/tools/cloudflare', icon: <GlobalOutlined />, label: 'DDNS' },
      { key: '/tools/docker-backup', icon: <ToolOutlined />, label: 'Docker 备份' },
    ],
  },
  { key: '/plugins', icon: <AppstoreOutlined />, label: '插件中心' },
  { key: '/notifications', icon: <BellOutlined />, label: '通知中心' },
  { key: '/logs', icon: <FileTextOutlined />, label: '日志' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
  { key: '/ai', icon: <RobotOutlined />, label: 'AI 助手' },
];

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const { token: { colorBgContainer, borderRadiusLG } } = theme.useToken();

  const userMenuItems: MenuProps['items'] = [
    { key: 'user', label: user?.username || 'Admin', disabled: true },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: logout,
    },
  ];

  // 查找当前匹配的菜单 key
  const findSelectedKey = () => {
    const path = location.pathname;
    const match = (menuItems ?? []).find((item: any) => {
      if (item.key === path) return true;
      if (item.children) {
        return item.children.some((child: any) => child.key === path);
      }
      return false;
    });
    return match ? match.key as string : '/';
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        style={{ background: 'linear-gradient(180deg, #1a1a2e 0%, #16213e 100%)' }}
        theme="dark"
      >
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          fontSize: collapsed ? 14 : 20,
          fontWeight: 700,
          letterSpacing: 1,
        }}>
          {collapsed ? '🚀' : '🚀 NASPilot'}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[findSelectedKey()]}
          defaultOpenKeys={['/tools']}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ background: 'transparent' }}
        />
      </Sider>
      <Layout>
        <Header style={{
          padding: '0 24px',
          background: colorBgContainer,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
        }}>
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            style={{ fontSize: 16, width: 48, height: 48 }}
          />
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Avatar
              style={{ cursor: 'pointer', backgroundColor: '#667eea' }}
              icon={<UserOutlined />}
            />
          </Dropdown>
        </Header>
        <Content style={{
          margin: 24,
          padding: 24,
          background: colorBgContainer,
          borderRadius: borderRadiusLG,
          minHeight: 280,
        }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
