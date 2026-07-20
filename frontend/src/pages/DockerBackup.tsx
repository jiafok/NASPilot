import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Divider, Form, Input,
  message, Space, Spin, Switch, Table, Tag, Typography,
} from 'antd';
import { CloudServerOutlined, PlayCircleOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import api from '../utils/api';

const { Title, Paragraph } = Typography;

interface PluginInfo { id: number; slug: string; name: string; version: string; enabled: boolean; }
interface PluginInstance { id: number; plugin_id: number; name: string; config: Record<string, any>; enabled: boolean; }

const DEFAULT_CONFIG = {
  backup_dir: '/app/data/docker_backup',
  containers: [],
  volumes: [],
  compress: true,
  keep_days: 7,
};

export default function DockerBackup() {
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
      const pt = (res.data as PluginInfo[]).find(p => p.slug === 'docker_backup');
      setPlugin(pt || null);
      if (!pt) return;
      const instRes = await api.get(`/plugins/${pt.id}/instances`);
      const inst = (instRes.data as PluginInstance[])[0] || null;
      setInstance(inst);
      form.setFieldsValue({
        name: inst?.name || 'Docker 备份默认实例',
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
      message.success('备份任务完成');
    } catch (err: any) { message.error(err?.response?.data?.detail || '执行失败'); }
    finally { setRunning(false); }
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '120px auto' }} />;

  const history: any[] = instance?.config?.state?.history || [];
  const historyColumns = [
    { title: '时间', dataIndex: 'time', key: 'time', width: 180 },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (s: string) => <Tag color={s === 'ok' ? 'green' : 'red'}>{s}</Tag>,
    },
    { title: '备份文件', dataIndex: 'archive', key: 'archive', ellipsis: true },
    { title: '大小', dataIndex: 'archive_size', key: 'archive_size', width: 90 },
    { title: '容器', dataIndex: 'containers', key: 'containers', width: 70 },
    { title: '数据卷', dataIndex: 'volumes', key: 'volumes', width: 70 },
  ];

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <Card style={{ marginBottom: 24 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <Title level={2} style={{ marginBottom: 8 }}>Docker 备份</Title>
            <Paragraph style={{ marginBottom: 0 }}>备份容器配置和数据卷，自动清理过期备份，附带恢复脚本。</Paragraph>
          </div>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleRun} loading={running}>立即备份</Button>
          </Space>
        </Space>
      </Card>

      {runResult && (
        <Card title="最近执行结果" style={{ marginBottom: 24 }}>
          <Descriptions bordered size="small" column={3}>
            <Descriptions.Item label="状态">{runResult.status}</Descriptions.Item>
            <Descriptions.Item label="备份文件">{runResult.archive || '-'}</Descriptions.Item>
            <Descriptions.Item label="大小">{runResult.archive_size || '-'}</Descriptions.Item>
            <Descriptions.Item label="容器数">{runResult.backed_up_containers ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="数据卷数">{runResult.backed_up_volumes ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="清理旧备份">{runResult.pruned_old ?? '-'}</Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      <Card title="备份历史" style={{ marginBottom: 24 }}>
        <Table dataSource={[...history].reverse()} columns={historyColumns} rowKey="time" size="small" pagination={{ pageSize: 10 }} />
      </Card>

      <Card>
        <Form layout="vertical" form={form} onFinish={handleSave}>
          <Form.Item name="name" label="实例名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
          <Divider orientation="left">运行配置</Divider>
          <Alert style={{ marginBottom: 16 }} type={parsedConfig ? 'info' : 'error'} showIcon
            message={parsedConfig ? 'JSON 格式正确' : 'JSON 存在语法错误'} />
          <Form.Item name="configText" label="实例配置 JSON" rules={[{ required: true }]}>
            <Input.TextArea rows={14} style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>保存配置</Button>
        </Form>
      </Card>
    </div>
  );
}
