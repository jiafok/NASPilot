import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Divider, Form, Input,
  message, Space, Spin, Switch, Table, Tag, Typography,
} from 'antd';
import { GlobalOutlined, PlayCircleOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import api from '../utils/api';

const { Title, Paragraph } = Typography;

interface PluginInfo { id: number; slug: string; name: string; version: string; enabled: boolean; }
interface PluginInstance { id: number; plugin_id: number; name: string; config: Record<string, any>; enabled: boolean; }

const DEFAULT_CONFIG = {
  api_token: '',
  iface: '',
  zones: [
    { zone_id: '', records: ['home.example.com'], ip_type: 'both', proxied: false },
  ],
};

export default function CloudflareDDNS() {
  const [plugin, setPlugin] = useState<PluginInfo | null>(null);
  const [instance, setInstance] = useState<PluginInstance | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);
  const [form] = Form.useForm();

  const configText = Form.useWatch('configText', form) as string | undefined;
  const parsedConfig = useMemo(() => {
    if (!configText) return DEFAULT_CONFIG;
    try { return JSON.parse(configText); } catch { return null; }
  }, [configText]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/plugins');
      const pt = (res.data as PluginInfo[]).find(p => p.slug === 'cloudflare_ddns');
      setPlugin(pt || null);
      if (!pt) return;
      const instRes = await api.get(`/plugins/${pt.id}/instances`);
      const inst = (instRes.data as PluginInstance[])[0] || null;
      setInstance(inst);
      form.setFieldsValue({
        name: inst?.name || 'Cloudflare DDNS 默认实例',
        enabled: inst ? inst.enabled : true,
        configText: JSON.stringify({ ...DEFAULT_CONFIG, ...(inst?.config || {}) }, null, 2),
      });
    } catch { message.error('加载配置失败'); }
    finally { setLoading(false); }
  }, [form]);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    const values = await form.validateFields();
    if (!plugin || !parsedConfig) { message.error('JSON 格式错误'); return; }
    setSaving(true);
    try {
      const payload = { name: values.name, config: parsedConfig, enabled: values.enabled };
      if (instance) await api.put(`/plugins/instances/${instance.id}`, payload);
      else await api.post(`/plugins/${plugin.id}/instances`, payload);
      message.success('配置已保存'); await load();
    } catch (err: any) { message.error(err?.response?.data?.detail || '保存失败'); }
    finally { setSaving(false); }
  };

  const handleRun = async () => {
    if (!plugin) return;
    setRunning(true); setRunResult(null);
    try {
      const res = await api.post(`/plugins/${plugin.id}/run`);
      setRunResult(res.data?.result);
      message.success('DDNS 更新完成');
    } catch (err: any) { message.error(err?.response?.data?.detail || '执行失败'); }
    finally { setRunning(false); }
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;

  const historyData: any[] = instance?.config?.state?.history || [];
  const historyColumns = [
    { title: '记录', dataIndex: 'record', key: 'record' },
    { title: '类型', dataIndex: 'type', key: 'type', width: 70 },
    { title: 'IP', dataIndex: 'ip', key: 'ip' },
    {
      title: '结果', dataIndex: 'result', key: 'result', width: 100,
      render: (r: string) => <Tag color={r === 'updated' || r === 'created' ? 'green' : r === 'unchanged' ? 'blue' : 'red'}>{r}</Tag>,
    },
    { title: '时间', dataIndex: 'time', key: 'time', width: 180 },
  ];

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <Card style={{ marginBottom: 24 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <Title level={2} style={{ marginBottom: 8 }}>Cloudflare DDNS</Title>
            <Paragraph style={{ marginBottom: 0 }}>IPv4/IPv6 自动更新 Cloudflare DNS 记录，当 IP 变化时触发更新。</Paragraph>
          </div>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleRun} loading={running}>立即检测更新</Button>
          </Space>
        </Space>
      </Card>

      {runResult && (
        <Card title="最近执行结果" style={{ marginBottom: 24 }}>
          <Descriptions bordered size="small" column={3}>
            <Descriptions.Item label="IPv4">{runResult.ipv4 || '-'}</Descriptions.Item>
            <Descriptions.Item label="IPv6">{runResult.ipv6 || '-'}</Descriptions.Item>
            <Descriptions.Item label="已更新">{runResult.updated ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="未变化">{runResult.unchanged ?? '-'}</Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      <Card title="DNS 更新历史" style={{ marginBottom: 24 }}>
        <Table dataSource={[...historyData].reverse()} columns={historyColumns} rowKey={(r) => r.time + r.record + r.type} size="small" pagination={{ pageSize: 10 }} />
      </Card>

      <Card>
        <Form layout="vertical" form={form} onFinish={handleSave}>
          <Form.Item name="name" label="实例名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
          <Divider orientation="left">运行配置</Divider>
          <Alert style={{ marginBottom: 16 }} type={parsedConfig ? 'info' : 'error'} showIcon
            message={parsedConfig ? 'JSON 格式正确' : 'JSON 存在语法错误'} />
          <Form.Item name="configText" label="实例配置 JSON" rules={[{ required: true }]}>
            <Input.TextArea rows={18} style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>保存配置</Button>
        </Form>
      </Card>
    </div>
  );
}
