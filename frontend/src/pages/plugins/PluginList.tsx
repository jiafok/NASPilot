import { useState, useEffect, useCallback } from 'react';
import { Card, Tag, Button, Space, message, Typography, Row, Col, Spin, Modal, Descriptions } from 'antd';
import { PoweroffOutlined, ReloadOutlined, SettingOutlined, CheckCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import api from '../../utils/api';

const { Title, Paragraph } = Typography;

interface Plugin {
  id: number;
  slug: string;
  name: string;
  description: string;
  version: string;
  author: string;
  category: string;
  enabled: boolean;
  instance_count: number;
}

const TOOL_PAGE_MAP: Record<string, string> = {
  pt_rss: '/tools/pt-rss',
  alist_upload: '/tools/alist',
  cloudflare_ddns: '/tools/cloudflare',
  docker_backup: '/tools/docker-backup',
};

export default function PluginList() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null);
  const navigate = useNavigate();

  const fetchPlugins = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/plugins');
      setPlugins(res.data);
    } catch { message.error('获取插件列表失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchPlugins(); }, [fetchPlugins]);

  const handleToggle = async (id: number, enabled: boolean) => {
    const endpoint = enabled ? `/plugins/${id}/enable` : `/plugins/${id}/disable`;
    try {
      await api.post(endpoint);
      message.success(enabled ? '已启用' : '已禁用');
      fetchPlugins();
    } catch { message.error('操作失败'); }
  };

  const getStatusTag = (p: Plugin) => {
    if (!p.enabled) return <Tag color="default">已禁用</Tag>;
    if (p.instance_count > 0) return <Tag color="green" icon={<CheckCircleOutlined />}>已配置</Tag>;
    return <Tag color="orange" icon={<ExclamationCircleOutlined />}>待配置</Tag>;
  };

  const getPluginIcon = (slug: string) => {
    const icons: Record<string, string> = {
      pt_rss: '📥', alist_upload: '📤', cloudflare_ddns: '🌐',
      docker_backup: '💾', log_cleanup: '🧹', btrfs_cleanup: '🗑️',
      rclone_mount: '📁', cloudflare_pages: '🏠',
    };
    return icons[slug] || '🔌';
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>插件中心</Title>
        <Button icon={<ReloadOutlined />} onClick={fetchPlugins}>刷新</Button>
      </div>

      <Row gutter={[16, 16]}>
        {plugins.map((p) => (
          <Col xs={24} sm={12} lg={8} key={p.id}>
            <Card
              hoverable
              onClick={() => { setSelectedPlugin(p); setDetailOpen(true); }}
              extra={getStatusTag(p)}
              title={
                <Space>
                  <span style={{ fontSize: 18 }}>{getPluginIcon(p.slug)}</span>
                  {p.name}
                </Space>
              }
            >
              <Paragraph ellipsis={{ rows: 2 }} type="secondary">{p.description}</Paragraph>
              <Space wrap>
                <Tag>{p.version}</Tag>
                <Tag>{p.category}</Tag>
                {p.instance_count > 0 && <Tag color="blue">{p.instance_count} 个实例</Tag>}
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Modal
        title={selectedPlugin?.name}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={
          selectedPlugin && TOOL_PAGE_MAP[selectedPlugin.slug] ? (
            <Button type="primary" icon={<SettingOutlined />}
              onClick={() => { setDetailOpen(false); navigate(TOOL_PAGE_MAP[selectedPlugin.slug]); }}>
              打开配置
            </Button>
          ) : null
        }
        width={500}
      >
        {selectedPlugin && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="标识">{selectedPlugin.slug}</Descriptions.Item>
            <Descriptions.Item label="描述">{selectedPlugin.description}</Descriptions.Item>
            <Descriptions.Item label="版本">{selectedPlugin.version}</Descriptions.Item>
            <Descriptions.Item label="作者">{selectedPlugin.author || '-'}</Descriptions.Item>
            <Descriptions.Item label="分类">{selectedPlugin.category}</Descriptions.Item>
            <Descriptions.Item label="状态">{getStatusTag(selectedPlugin)}</Descriptions.Item>
            <Descriptions.Item label="实例数">{selectedPlugin.instance_count}</Descriptions.Item>
            <Descriptions.Item label="操作">
              <Space>
                <Button
                  type={selectedPlugin.enabled ? 'default' : 'primary'}
                  danger={selectedPlugin.enabled}
                  icon={<PoweroffOutlined />}
                  onClick={() => { handleToggle(selectedPlugin.id, !selectedPlugin.enabled); setDetailOpen(false); }}
                >
                  {selectedPlugin.enabled ? '禁用' : '启用'}
                </Button>
                {TOOL_PAGE_MAP[selectedPlugin.slug] && (
                  <Button type="primary" icon={<SettingOutlined />}
                    onClick={() => { setDetailOpen(false); navigate(TOOL_PAGE_MAP[selectedPlugin.slug]); }}>
                    配置
                  </Button>
                )}
              </Space>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
