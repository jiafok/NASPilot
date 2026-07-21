import { useEffect, useState } from 'react';
import { Form, Input, Select, Switch, InputNumber, Button, Card, Space, Typography, message, Spin, Tag, Divider } from 'antd';
import { SaveOutlined, PlayCircleOutlined, ReloadOutlined, ClockCircleOutlined } from '@ant-design/icons';
import api from '../utils/api';

const { Title, Paragraph } = Typography;

export interface PluginField {
  key: string;
  label: string;
  type: 'string' | 'number' | 'boolean' | 'select' | 'password' | 'textarea' | 'object' | 'array';
  default?: any;
  placeholder?: string;
  help?: string;
  options?: { label: string; value: any }[];
  fields?: PluginField[]; // for nested object
  required?: boolean;
}

interface Props {
  slug: string;
  title: string;
  description: string;
  fields: PluginField[];
  onRun?: () => void;
  running?: boolean;
  runResult?: any;
  resultRenderer?: (result: any) => React.ReactNode;
}

export default function PluginConfigForm({ slug, title, description, fields, onRun, running, runResult, resultRenderer }: Props) {
  const [plugin, setPlugin] = useState<any>(null);
  const [instance, setInstance] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === slug);
      setPlugin(p || null);
      if (!p) { message.error(`Plugin ${slug} not found`); return; }
      const instRes = await api.get(`/plugins/${p.id}/instances`);
      const inst = (instRes.data as any[])[0] || null;
      setInstance(inst);
      const values: Record<string, any> = {};
      fields.forEach((f) => { values[f.key] = inst?.config?.[f.key] ?? f.default; });
      form.setFieldsValue({ ...values, _name: inst?.name || title, _enabled: inst ? inst.enabled : true });
    } catch { message.error('Failed to load plugin'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    const values = await form.validateFields();
    if (!plugin) return;
    const { _name, _enabled, ...config } = values;
    setSaving(true);
    try {
      const payload = { name: _name, config, enabled: _enabled };
      if (instance) await api.put(`/plugins/instances/${instance.id}`, payload);
      else await api.post(`/plugins/${plugin.id}/instances`, payload);
      message.success('Saved');
      await load();
    } catch (err: any) { message.error(err?.response?.data?.detail || 'Save failed'); }
    finally { setSaving(false); }
  };

  const renderField = (field: PluginField) => {
    const common = { label: field.label, name: field.key, tooltip: field.help, rules: field.required ? [{ required: true }] : undefined };

    if (field.type === 'boolean') {
      return <Form.Item {...common} valuePropName="checked"><Switch /></Form.Item>;
    }
    if (field.type === 'number') {
      return <Form.Item {...common}><InputNumber style={{ width: '100%' }} placeholder={field.placeholder} /></Form.Item>;
    }
    if (field.type === 'password') {
      return <Form.Item {...common}><Input.Password placeholder={field.placeholder} /></Form.Item>;
    }
    if (field.type === 'textarea') {
      return <Form.Item {...common}><Input.TextArea rows={3} placeholder={field.placeholder} /></Form.Item>;
    }
    if (field.type === 'select' && field.options) {
      return <Form.Item {...common}><Select options={field.options} placeholder={field.placeholder} /></Form.Item>;
    }
    if (field.type === 'object' && field.fields) {
      return (
        <Card size="small" title={field.label} style={{ marginBottom: 16 }} key={field.key}>
          {field.fields.map(renderField)}
        </Card>
      );
    }
    if (field.type === 'array') {
      return <Form.Item {...common}><Input placeholder={field.placeholder || 'Comma-separated values'} /></Form.Item>;
    }
    return <Form.Item {...common}><Input placeholder={field.placeholder} /></Form.Item>;
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;

  return (
    <div>
      <Card style={{ marginBottom: 16 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div>
            <Title level={3} style={{ marginBottom: 4 }}>{title}</Title>
            <Paragraph style={{ marginBottom: 0 }}>{description}</Paragraph>
          </div>
          <Space>
            <Tag color={instance?.enabled ? 'green' : 'default'}>{instance?.enabled ? 'Active' : 'Disabled'}</Tag>
            <Button icon={<ReloadOutlined />} onClick={load}>Refresh</Button>
            {onRun && <Button type="primary" icon={<PlayCircleOutlined />} onClick={onRun} loading={running}>Run Now</Button>}
          </Space>
        </Space>
      </Card>

      {runResult && (
        <Card title="Run Result" style={{ marginBottom: 16 }}>
          {resultRenderer ? resultRenderer(runResult) : <pre style={{ fontFamily: 'monospace', fontSize: 12, maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(runResult, null, 2)}</pre>}
        </Card>
      )}

      <Card>
        <Form form={form} layout="vertical" onFinish={handleSave}>
          <Form.Item name="_name" label="Instance Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="_enabled" label="Enabled" valuePropName="checked"><Switch /></Form.Item>
          {fields.map(renderField)}

          <Divider>
            <Space><ClockCircleOutlined /> Schedule (Optional)</Space>
          </Divider>

          <Form.Item name="schedule_enabled" label="Enable Schedule" valuePropName="checked"
            tooltip="开启后按 Cron 表达式定时自动执行此插件">
            <Switch />
          </Form.Item>

          <Form.Item name="schedule_cron" label="Cron Expression"
            tooltip="分 时 日 月 周。留空则只手动执行。例如 */30 * * * * 每30分钟，0 3 * * * 每天凌晨3点">
            <Input placeholder="*/30 * * * *" style={{ width: 220 }} />
          </Form.Item>

          <Divider />

          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>Save Configuration</Button>
        </Form>
      </Card>
    </div>
  );
}
