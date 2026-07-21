import { useState, useEffect, useCallback } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, Switch, Space, Tag,
  Popconfirm, message, Typography, InputNumber, Tooltip, Alert,
} from 'antd';
import { PlusOutlined, PlayCircleOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import api from '../../utils/api';

const { Title } = Typography;
const { TextArea } = Input;

interface Task {
  id: number; name: string; description: string | null; task_type: string;
  command: string; args: string | null; cron_expr: string | null; timezone: string;
  timeout: number; enabled: boolean; working_dir: string | null; env_vars: Record<string, string> | null;
  next_run_at: string | null; last_run_at: string | null;
}

export default function TaskList() {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [form] = Form.useForm();

  const fetchTasks = useCallback(async () => { setLoading(true); try { const res = await api.get('/tasks'); setTasks(res.data); } catch { message.error(t('common.failed')); } finally { setLoading(false); } }, [t]);

  useEffect(() => { fetchTasks(); }, [fetchTasks]);

  const handleSave = async () => {
    const values = await form.validateFields();
    try {
      if (editingTask) { await api.put(`/tasks/${editingTask.id}`, values); message.success(t('common.success')); }
      else { await api.post('/tasks', values); message.success(t('common.success')); }
      setModalOpen(false); setEditingTask(null); form.resetFields(); fetchTasks();
    } catch (err: any) { message.error(err?.response?.data?.detail || t('common.failed')); }
  };

  const handleRun = async (id: number) => { try { await api.post(`/tasks/${id}/run`); message.success(t('tasks.runNow')); } catch (err: any) { message.error(err?.response?.data?.detail || t('common.failed')); } };
  const handleDelete = async (id: number) => { try { await api.delete(`/tasks/${id}`); message.success(t('common.success')); fetchTasks(); } catch { message.error(t('common.failed')); } };
  const handleToggle = async (id: number, enabled: boolean) => { try { await api.put(`/tasks/${id}`, { enabled }); fetchTasks(); } catch { message.error(t('common.failed')); } };

  const columns = [
    { title: t('tasks.name'), dataIndex: 'name', key: 'name', ellipsis: true },
    { title: t('common.type'), dataIndex: 'task_type', key: 'task_type', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    { title: t('tasks.cronExpr'), dataIndex: 'cron_expr', key: 'cron_expr', width: 110, ellipsis: true, render: (v: string|null) => v || <Tag color="default">manual</Tag> },
    { title: t('tasks.timeout'), dataIndex: 'timeout', key: 'timeout', width: 70, render: (v: number) => `${v}s` },
    { title: t('common.status'), dataIndex: 'enabled', key: 'enabled', width: 70, render: (enabled: boolean, r: Task) => <Switch size="small" checked={enabled} onChange={(v) => handleToggle(r.id, v)} /> },
    { title: t('tasks.lastRun'), dataIndex: 'last_run_at', key: 'last_run_at', width: 150, render: (v: string|null) => v ? new Date(v).toLocaleString() : '-' },
    { title: t('common.actions'), key: 'actions', width: 170, render: (_: any, r: Task) => (
      <Space size="small">
        <Tooltip title={t('tasks.runNow')}><Button size="small" icon={<PlayCircleOutlined />} onClick={() => handleRun(r.id)} /></Tooltip>
        <Button size="small" onClick={() => { setEditingTask(r); form.setFieldsValue(r); setModalOpen(true); }}>{t('common.edit')}</Button>
        <Popconfirm title={t('tasks.deleteConfirm')} onConfirm={() => handleDelete(r.id)}><Button size="small" danger icon={<DeleteOutlined />} /></Popconfirm>
      </Space>
    )},
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <Title level={4} style={{ margin: 0 }}>{t('tasks.title')}</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchTasks}>{t('common.refresh')}</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditingTask(null); form.resetFields(); form.setFieldsValue({ task_type: 'shell', timeout: 300, enabled: true }); setModalOpen(true); }}>
            {t('tasks.create')}
          </Button>
        </Space>
      </div>

      <Table dataSource={tasks} columns={columns} rowKey="id" loading={loading} size="small"
        locale={{ emptyText: t('tasks.noTasks') }} />

      <Modal title={editingTask ? t('tasks.edit') : t('tasks.create')} open={modalOpen}
        onOk={handleSave} onCancel={() => { setModalOpen(false); setEditingTask(null); }} width={640}>
        <Form form={form} layout="vertical" initialValues={{ task_type: 'shell', timeout: 300, enabled: true }}>
          <Form.Item name="name" label={t('tasks.name')} rules={[{ required: true }]} 
            tooltip={t('tasks.namePlaceholder')}>
            <Input placeholder={t('tasks.namePlaceholder')} />
          </Form.Item>
          <Form.Item name="description" label={t('common.description')}>
            <Input placeholder={t('common.optional')} />
          </Form.Item>
          <Form.Item name="task_type" label={t('common.type')} rules={[{ required: true }]} 
            tooltip="shell → /bin/sh -c | python → python3 | docker → docker exec">
            <Select options={[{ label: 'Shell', value: 'shell' }, { label: 'Python', value: 'python' }, { label: 'Docker', value: 'docker' }]} />
          </Form.Item>
          <Form.Item name="command" label={t('tasks.command')} rules={[{ required: true }]} 
            tooltip={t('tasks.commandHelp')}>
            <TextArea rows={2} placeholder={t('tasks.commandPlaceholder')} />
          </Form.Item>
          <Form.Item name="args" label="Args (JSON)"
            tooltip="额外参数，JSON 数组格式。例如 [&quot;--verbose&quot;, &quot;--config&quot;, &quot;/scripts/config.yaml&quot;]">
            <Input placeholder='["--config", "/scripts/config.yaml"]' />
          </Form.Item>
          <Form.Item name="working_dir" label="工作目录"
            tooltip="脚本执行时的工作目录。默认使用 $HOME。例如 /scripts">
            <Input placeholder="/scripts" />
          </Form.Item>
          <Form.Item name="cron_expr" label={t('tasks.cronExpr')}
            tooltip={t('tasks.cronHelp')}>
            <Input placeholder={t('tasks.cronPlaceholder')} />
          </Form.Item>
          <Form.Item name="env_vars" label="环境变量 (JSON)"
            tooltip='形如 {"KEY": "VALUE"}，会传递给脚本。例如 {"DOWNLOAD_DIR": "/downloads"}'>
            <TextArea rows={2} placeholder='{"QB_URL": "http://10.0.0.5:8080"}' />
          </Form.Item>
          <Space wrap>
            <Form.Item name="timeout" label={t('tasks.timeout')} tooltip={t('tasks.timeoutHelp')}>
              <InputNumber min={0} max={86400} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="enabled" label={t('common.enable')} valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Alert type="info" showIcon style={{ fontSize: 12 }}
            message="脚本放在 /scripts 目录下（容器内路径），通过 docker-compose volumes 挂载。输出日志保存在 /app/logs/{任务名}.log。" />
        </Form>
      </Modal>
    </div>
  );
}
