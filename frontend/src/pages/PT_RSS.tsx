import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Descriptions, Divider, Form, Input, InputNumber, message, Space, Spin, Switch, Typography } from 'antd';
import { CloudUploadOutlined, PlayCircleOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import api from '../utils/api';

const { Title, Paragraph, Text } = Typography;

interface PluginInfo {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  version: string;
  enabled: boolean;
  config_schema?: Record<string, unknown> | null;
}

interface PluginInstance {
  id: number;
  plugin_id: number;
  name: string;
  config: Record<string, any>;
  enabled: boolean;
}

const defaultConfig = {
  rss_urls: [],
  qbittorrent: { url: '', username: '', password: '' },
  download_dir: '',
  min_free_gb: 50,
  max_active_downloads: 15,
  free_check: false,
  cleanup: { seed_days: 2, stuck_download_days: 3 },
  free_ttl_hours: 48,
  rss_missing_threshold: 2,
  enable_rss_eviction: true,
  gc: { evicted_days: 5, expired_days: 5 },
};

export default function PT_RSS() {
  const [plugin, setPlugin] = useState<PluginInfo | null>(null);
  const [instance, setInstance] = useState<PluginInstance | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [form] = Form.useForm();

  const configText = Form.useWatch('configText', form) as string | undefined;
  const parsedConfig = useMemo(() => {
    if (!configText) return defaultConfig;
    try {
      return JSON.parse(configText);
    } catch {
      return null;
    }
  }, [configText]);

  const load = async () => {
    setLoading(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const pt = (pluginsRes.data as PluginInfo[]).find((item) => item.slug === 'pt_rss');
      setPlugin(pt || null);
      if (!pt) {
        message.error('未找到 PT RSS 插件');
        return;
      }

      const instancesRes = await api.get(`/plugins/${pt.id}/instances`);
      const inst = (instancesRes.data as PluginInstance[])[0] || null;
      setInstance(inst);
      const merged = { ...defaultConfig, ...(inst?.config || {}) };
      form.setFieldsValue({
        name: inst?.name || 'PT RSS 默认实例',
        enabled: inst ? inst.enabled : true,
        configText: JSON.stringify(merged, null, 2),
      });
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '加载 PT RSS 配置失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    const values = await form.validateFields();
    if (!plugin) return;
    if (!parsedConfig) {
      message.error('配置 JSON 格式错误');
      return;
    }

    setSaving(true);
    try {
      const payload = {
        name: values.name,
        config: parsedConfig,
        enabled: values.enabled,
      };

      if (instance) {
        await api.put(`/plugins/instances/${instance.id}`, payload);
      } else {
        await api.post(`/plugins/${plugin.id}/instances`, payload);
      }
      message.success('PT RSS 配置已保存');
      await load();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    if (!plugin) return;
    setRunning(true);
    try {
      const res = await api.post(`/plugins/${plugin.id}/run`);
      message.success(res.data?.message || '已触发运行');
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '触发失败');
    } finally {
      setRunning(false);
    }
  };

  if (loading) {
    return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;
  }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <Card style={{ marginBottom: 24 }}>
        <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <Title level={2} style={{ marginBottom: 8 }}>PT RSS 自动下载</Title>
            <Paragraph style={{ marginBottom: 0 }}>
              这是你的脚本 <Text code>pt_rss_auto.py</Text> 的 Web 化控制面板，支持 RSS 源、qBittorrent、空间清理和 RSS 驱逐配置。
            </Paragraph>
          </div>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button icon={<PlayCircleOutlined />} type="primary" onClick={handleRun} loading={running}>
              立即执行
            </Button>
          </Space>
        </Space>
      </Card>

      {plugin && (
        <Card style={{ marginBottom: 24 }}>
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="插件状态">{plugin.enabled ? '启用' : '禁用'}</Descriptions.Item>
            <Descriptions.Item label="版本">{plugin.version}</Descriptions.Item>
            <Descriptions.Item label="描述" span={2}>{plugin.description || '-'}</Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      <Card>
        <Form layout="vertical" form={form} onFinish={handleSave}>
          <Form.Item name="name" label="实例名称" rules={[{ required: true, message: '请输入实例名称' }]}>
            <Input placeholder="PT RSS 默认实例" />
          </Form.Item>

          <Form.Item name="enabled" label="启用状态" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Divider orientation="left">运行配置</Divider>

          <Alert
            style={{ marginBottom: 16 }}
            type={parsedConfig ? 'info' : 'error'}
            showIcon
            message={parsedConfig ? '配置 JSON 解析成功' : '配置 JSON 存在语法错误'}
            description="你可以直接编辑 JSON，也可以后续再拆成更细的表单字段。当前实现先保证控制面能落地。"
          />

          <Form.Item
            name="configText"
            label="实例配置 JSON"
            rules={[{ required: true, message: '请输入 JSON 配置' }]}
          >
            <Input.TextArea rows={22} style={{ fontFamily: 'monospace' }} />
          </Form.Item>

          <Space>
            <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
              保存配置
            </Button>
            <Button icon={<CloudUploadOutlined />} onClick={() => void load()}>
              恢复最新配置
            </Button>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
