import { useState, useEffect, useCallback } from 'react';
import { Card, Tag, Button, Space, message, Typography, Row, Col, Spin, Modal, Descriptions } from 'antd';
import { PoweroffOutlined, ReloadOutlined } from '@ant-design/icons';
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
}

export default function PluginList() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null);

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
    } catch (err: any) {
      message.error('操作失败');
    }
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
              extra={
                <Tag color={p.enabled ? 'green' : 'default'}>{p.enabled ? '运行中' : '已禁用'}</Tag>
              }
              title={
                <Space>
                  <span style={{ fontSize: 18 }}>
                    {p.slug === 'pt_rss' ? '📥' : p.slug === 'alist_upload' ? '📤' : p.slug === 'cloudflare_ddns' ? '🌐' : '💾'}
                  </span>
                  {p.name}
                </Space>
              }
            >
              <Paragraph ellipsis={{ rows: 2 }} type="secondary">{p.description}</Paragraph>
              <Space>
                <Tag>{p.version}</Tag>
                <Tag>{p.category}</Tag>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Modal
        title={selectedPlugin?.name}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={500}
      >
        {selectedPlugin && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="标识">{selectedPlugin.slug}</Descriptions.Item>
            <Descriptions.Item label="版本">{selectedPlugin.version}</Descriptions.Item>
            <Descriptions.Item label="作者">{selectedPlugin.author}</Descriptions.Item>
            <Descriptions.Item label="分类">{selectedPlugin.category}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={selectedPlugin.enabled ? 'green' : 'red'}>
                {selectedPlugin.enabled ? '运行中' : '已禁用'}
              </Tag>
            </Descriptions.Item>
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
              </Space>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
