import { useState, useEffect, useCallback } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, Switch, Space, Tag,
  Popconfirm, message, Typography, InputNumber,
} from 'antd';
import { PlusOutlined, PlayCircleOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '../../utils/api';

const { Title } = Typography;
const { TextArea } = Input;

interface Task {
  id: number;
  name: string;
  task_type: string;
  cron_expr: string | null;
  command: string;
  timeout: number;
  enabled: boolean;
  next_run_at: string | null;
}

export default function TaskList() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [form] = Form.useForm();

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/tasks');
      setTasks(res.data);
    } catch (err) {
      message.error('获取任务列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTasks(); }, [fetchTasks]);

  const handleSave = async () => {
    const values = await form.validateFields();
    try {
      if (editingTask) {
        await api.put(`/tasks/${editingTask.id}`, values);
        message.success('任务已更新');
      } else {
        await api.post('/tasks', values);
        message.success('任务已创建');
      }
      setModalOpen(false);
      setEditingTask(null);
      form.resetFields();
      fetchTasks();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '保存失败');
    }
  };

  const handleRun = async (id: number) => {
    try {
      await api.post(`/tasks/${id}/run`);
      message.success('任务已触发');
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '触发失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/tasks/${id}`);
      message.success('已删除');
      fetchTasks();
    } catch (err: any) {
      message.error('删除失败');
    }
  };

  const handleToggle = async (id: number, enabled: boolean) => {
    try {
      await api.put(`/tasks/${id}`, { enabled });
      fetchTasks();
    } catch (err: any) {
      message.error('操作失败');
    }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '类型', dataIndex: 'task_type', key: 'task_type', width: 80,
      render: (t: string) => <Tag>{t}</Tag>,
    },
    { title: 'Cron', dataIndex: 'cron_expr', key: 'cron_expr', width: 120, ellipsis: true },
    { title: '超时(s)', dataIndex: 'timeout', key: 'timeout', width: 80 },
    {
      title: '状态', dataIndex: 'enabled', key: 'enabled', width: 80,
      render: (enabled: boolean, record: Task) => (
        <Switch size="small" checked={enabled} onChange={(v) => handleToggle(record.id, v)} />
      ),
    },
    {
      title: '下次执行', dataIndex: 'next_run_at', key: 'next_run_at', width: 160,
      render: (t: string | null) => t ? new Date(t).toLocaleString() : '-',
    },
    {
      title: '操作', key: 'actions', width: 160,
      render: (_: any, record: Task) => (
        <Space>
          <Button size="small" icon={<PlayCircleOutlined />} onClick={() => handleRun(record.id)} />
          <Button size="small" onClick={() => { setEditingTask(record); form.setFieldsValue(record); setModalOpen(true); }}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>任务中心</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchTasks}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditingTask(null); form.resetFields(); setModalOpen(true); }}>
            新建任务
          </Button>
        </Space>
      </div>

      <Table dataSource={tasks} columns={columns} rowKey="id" loading={loading} size="small" />

      <Modal
        title={editingTask ? '编辑任务' : '新建任务'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); setEditingTask(null); }}
        width={600}
      >
        <Form form={form} layout="vertical" initialValues={{ task_type: 'shell', timeout: 300, enabled: true }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="任务名称" />
          </Form.Item>
          <Form.Item name="task_type" label="类型" rules={[{ required: true }]}>
            <Select options={[
              { label: 'Shell', value: 'shell' },
              { label: 'Python', value: 'python' },
              { label: 'Docker', value: 'docker' },
            ]} />
          </Form.Item>
          <Form.Item name="command" label="命令" rules={[{ required: true }]}>
            <TextArea rows={3} placeholder="Shell命令 / Python脚本路径 / Docker命令" />
          </Form.Item>
          <Form.Item name="cron_expr" label="Cron 表达式" extra="如：*/30 * * * * (每30分钟)">
            <Input placeholder="*/30 * * * *" />
          </Form.Item>
          <Space>
            <Form.Item name="timeout" label="超时(s)">
              <InputNumber min={10} max={86400} />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
