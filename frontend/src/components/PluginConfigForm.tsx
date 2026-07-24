import { useEffect, useState } from 'react';
import { Form, Input, Select, Switch, InputNumber, Button, Card, Space, Typography, message, Tag, Divider, Table, Collapse } from 'antd';
import { SaveOutlined, PlayCircleOutlined, ReloadOutlined, ClockCircleOutlined, SettingOutlined } from '@ant-design/icons';
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
  fields?: PluginField[];
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
  topContent?: React.ReactNode;
  children?: React.ReactNode;
  onInstanceLoad?: (instance: any) => void;
}

// Cache plugin list across page switches — avoids redundant HTTP call
let _pluginsCache: any[] | null = null;
let _cacheExpiry = 0;

export default function PluginConfigForm({ slug, title, description, fields, onRun, running, runResult, resultRenderer, topContent, children, onInstanceLoad }: Props) {
  const [plugin, setPlugin] = useState<any>(null);
  const [instance, setInstance] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      // Use cached plugin list if fresh (<30s)
      const now = Date.now();
      if (!_pluginsCache || now - _cacheExpiry > 30000) {
        const pluginsRes = await api.get('/plugins');
        _pluginsCache = pluginsRes.data as any[];
        _cacheExpiry = now;
      }
      const p = (_pluginsCache as any[]).find((x: any) => x.slug === slug);
      setPlugin(p || null);
      if (!p) { message.error(`Plugin ${slug} not found`); return; }
      const instRes = await api.get(`/plugins/${p.id}/instances`);
      const inst = (instRes.data as any[])[0] || null;
      setInstance(inst);
      onInstanceLoad?.(inst);
      const values: Record<string, any> = {};
      fields.forEach((f) => {
        if (f.type === 'object' && f.fields) {
          values[f.key] = inst?.config?.[f.key] ?? {};
        } else {
          values[f.key] = inst?.config?.[f.key] ?? f.default;
        }
      });
      form.setFieldsValue({ ...values, _name: inst?.name || title, _enabled: inst ? inst.enabled : true });
    } catch { message.error('Failed to load plugin'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  /** Recursively normalize form values before saving:
   *  - type:'array' comma-strings → string[]
   *  - type:'object' nested fields normalized
   */
  const normalizeConfig = (config: Record<string, any>, flds: PluginField[]): Record<string, any> => {
    const out: Record<string, any> = { ...config };
    for (const f of flds) {
      const v = out[f.key];
      if (f.type === 'array' && typeof v === 'string') {
        out[f.key] = v.split(',').map((s: string) => s.trim()).filter(Boolean);
      } else if (f.type === 'object' && f.fields && typeof v === 'object' && v !== null) {
        out[f.key] = normalizeConfig(v, f.fields);
      }
    }
    return out;
  };

  const handleSave = async () => {
    const values = await form.validateFields();
    if (!plugin) return;
    const { _name, _enabled, ...rawConfig } = values;
    const config = normalizeConfig(rawConfig, fields);
    setSaving(true);
    try {
      const existingState = instance?.config?.state;
      const mergedConfig = { ...config, ...(existingState ? { state: existingState } : {}) };
      const payload = { name: _name, config: mergedConfig, enabled: _enabled };
      if (instance) await api.put(`/plugins/instances/${instance.id}`, payload);
      else await api.post(`/plugins/${plugin.id}/instances`, payload);
      message.success('Saved');
      await load();
    } catch (err: any) { message.error(err?.response?.data?.detail || 'Save failed'); }
    finally { setSaving(false); }
  };

  const renderField = (field: PluginField) => {
    const common = { label: field.label, name: field.key, tooltip: field.help, rules: field.required ? [{ required: true }] : undefined };
    if (field.type === 'boolean') return <Form.Item {...common} valuePropName="checked"><Switch /></Form.Item>;
    if (field.type === 'number') return <Form.Item {...common}><InputNumber style={{ width: '100%' }} placeholder={field.placeholder} /></Form.Item>;
    if (field.type === 'password') return <Form.Item {...common}><Input.Password placeholder={field.placeholder} /></Form.Item>;
    if (field.type === 'textarea') return <Form.Item {...common}><Input.TextArea rows={3} placeholder={field.placeholder} /></Form.Item>;
    if (field.type === 'select' && field.options) return <Form.Item {...common}><Select options={field.options} placeholder={field.placeholder} /></Form.Item>;
    if (field.type === 'object' && field.fields) {
      return (
        <Card size="small" title={field.label} style={{ marginBottom: 16 }} key={field.key}>
          {field.fields.map((sf) => {
            const nc = { label: sf.label, name: [field.key, sf.key], tooltip: sf.help, rules: sf.required ? [{ required: true }] : undefined };
            if (sf.type === 'boolean') return <Form.Item {...nc} valuePropName="checked"><Switch /></Form.Item>;
            if (sf.type === 'number') return <Form.Item {...nc}><InputNumber style={{ width: '100%' }} placeholder={sf.placeholder} /></Form.Item>;
            if (sf.type === 'password') return <Form.Item {...nc}><Input.Password placeholder={sf.placeholder} /></Form.Item>;
            if (sf.type === 'textarea') return <Form.Item {...nc}><Input.TextArea rows={2} placeholder={sf.placeholder} /></Form.Item>;
            if (sf.type === 'select' && sf.options) return <Form.Item {...nc}><Select options={sf.options} placeholder={sf.placeholder} /></Form.Item>;
            return <Form.Item {...nc}><Input placeholder={sf.placeholder} /></Form.Item>;
          })}
        </Card>
      );
    }
    if (field.type === 'array') return <Form.Item {...common}><Input placeholder={field.placeholder || 'Comma-separated values'} /></Form.Item>;
    return <Form.Item {...common}><Input placeholder={field.placeholder} /></Form.Item>;
  };

  // Always render UI immediately; load fills in data asynchronously
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
            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>Refresh</Button>
            {onRun && <Button type="primary" icon={<PlayCircleOutlined />} onClick={async () => { await onRun(); await load(); }} loading={running}>Run Now</Button>}
          </Space>
        </Space>
      </Card>

      {topContent}

      {runResult && (
        <Card title="Run Result" style={{ marginBottom: 16 }}>
          {resultRenderer ? resultRenderer(runResult) : <pre style={{ fontFamily: 'monospace', fontSize: 12, maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(runResult, null, 2)}</pre>}
        </Card>
      )}

      {instance?.config?.state?.run_history?.length > 0 && (
        <Collapse
          style={{ marginBottom: 16 }}
          items={[{
            key: 'history',
            label: <span><ClockCircleOutlined /> 运行历史 ({instance.config.state.run_history.length})</span>,
            children: (
              <Table
                dataSource={instance.config.state.run_history}
                rowKey="time"
                size="small"
                pagination={{ pageSize: 5 }}
                columns={[
                  { title: 'Time', dataIndex: 'time', width: 170, render: (v: string) => new Date(v).toLocaleString() },
                  { title: 'Status', dataIndex: 'status', width: 80, render: (s: string) => <Tag color={s === 'ok' ? 'green' : 'red'}>{s}</Tag> },
                  { title: 'Added', dataIndex: 'added', width: 70 },
                  { title: 'Summary', dataIndex: 'summary', ellipsis: true },
                ]}
              />
            ),
          }]}>
        </Collapse>
      )}

      {children}

      <Collapse
        items={[{
          key: 'config',
          label: <span><SettingOutlined /> 插件配置</span>,
          children: (
            <Card style={{ boxShadow: 'none' }}>
              <Form form={form} layout="vertical" onFinish={handleSave}>
                <Form.Item name="_name" label="Instance Name" rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
                <Form.Item name="_enabled" label="Enabled" valuePropName="checked"><Switch /></Form.Item>
                {fields.map(renderField)}
                <Divider><Space><ClockCircleOutlined /> Schedule (Optional)</Space></Divider>
                <Form.Item name="schedule_enabled" label="Enable Schedule" valuePropName="checked" tooltip="开启后按 Cron 表达式定时自动执行此插件">
                  <Switch />
                </Form.Item>
                <Form.Item name="schedule_cron" label="Cron Expression" tooltip="分 时 日 月 周。留空则只手动执行。例如 */30 * * * * 每30分钟，0 3 * * * 每天凌晨3点">
                  <Input placeholder="*/30 * * * *" style={{ width: 220 }} />
                </Form.Item>
                <Divider />
                <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>Save Configuration</Button>
              </Form>
            </Card>
          ),
        }]}>
      </Collapse>
    </div>
  );
}
