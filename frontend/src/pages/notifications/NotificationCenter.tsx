import { useState, useEffect, useCallback } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, Switch, Space, Tag,
  Popconfirm, message, Typography, Badge,
} from 'antd';
import { PlusOutlined, SendOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '../../utils/api';

const { Title } = Typography;

interface NotificationChannel {
  id: number;
  name: string;
  channel_type: string;
  enabled: boolean;
  is_default: boolean;
}

export default function NotificationCenter() {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<NotificationChannel | null>(null);
  const [form] = Form.useForm();
  const [testing, setTesting] = useState<number | null>(null);

  const fetchChannels = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/notifications/channels');
      setChannels(res.data);
    } catch { message.error('获取通知渠道失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchChannels(); }, [fetchChannels]);

  const handleSave = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await api.put(`/notifications/channels/${editing.id}`, values);
        message.success('已更新');
      } else {
        await api.post('/notifications/channels', values);
        message.success('已创建');
      }
      setModalOpen(false); setEditing(null); form.resetFields(); fetchChannels();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '保存失败');
    }
  };

  const handleTest = async (id: number) => {
    setTesting(id);
    try {
      await api.post(`/notifications/channels/${id}/test`);
      message.success('测试通知已发送');
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '测试失败');
    } finally { setTesting(null); }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/notifications/channels/${id}`);
      message.success('已删除');
      fetchChannels();
    } catch { message.error('删除失败'); }
  };

  const typeColor = (t: string) => {
    switch (t) {
      case 'feishu': return 'blue';
      case 'wechat_work': return 'green';
      case 'telegram': return 'cyan';
      case 'email': return 'orange';
      default: return 'default';
    }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '类型', dataIndex: 'channel_type', key: 'channel_type', width: 100,
      render: (t: string) => <Tag color={typeColor(t)}>{t}</Tag>,
    },
    {
      title: '默认', dataIndex: 'is_default', key: 'is_default', width: 60,
      render: (v: boolean) => v ? <Badge status="success" text="" /> : null,
    },
    {
      title: '启用', dataIndex: 'enabled', key: 'enabled', width: 70,
      render: (enabled: boolean, record: NotificationChannel) => (
        <Switch size="small" checked={enabled} onChange={async (v) => {
          await api.put(`/notifications/channels/${record.id}`, { enabled: v });
          fetchChannels();
        }} />
      ),
    },
    {
      title: '操作', key: 'actions', width: 200,
      render: (_: any, record: NotificationChannel) => (
        <Space>
          <Button size="small" icon={<SendOutlined />} loading={testing === record.id}
            onClick={() => handleTest(record.id)}>测试</Button>
          <Button size="small" onClick={() => { setEditing(record); form.setFieldsValue(record); setModalOpen(true); }}>编辑</Button>
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
        <Title level={4} style={{ margin: 0 }}>通知中心</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchChannels}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setModalOpen(true); }}>
            新建渠道
          </Button>
        </Space>
      </div>
      <Table dataSource={channels} columns={columns} rowKey="id" loading={loading} size="small" />

      <Modal
        title={editing ? '编辑渠道' : '新建渠道'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); setEditing(null); }}
        width={600}
      >
        <Form form={form} layout="vertical" initialValues={{ channel_type: 'feishu', enabled: true, is_default: false, config: {} }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：飞书通知" />
          </Form.Item>
          <Form.Item name="channel_type" label="类型" rules={[{ required: true }]}>
            <Select options={[
              { label: '飞书', value: 'feishu' },
              { label: '企业微信', value: 'wechat_work' },
              { label: 'Telegram', value: 'telegram' },
              { label: '邮件', value: 'email' },
            ]} />
          </Form.Item>
          <Form.Item name={['config', 'webhook']} label="Webhook URL">
            <Input placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
          </Form.Item>
          <Form.Item name={['config', 'secret']} label="Secret">
            <Input placeholder="签名密钥（可选）" />
          </Form.Item>
          <Form.Item name={['config', 'bot_token']} label="Telegram Bot Token">
            <Input placeholder="123456:ABCDEF..." />
          </Form.Item>
          <Form.Item name={['config', 'chat_id']} label="Telegram Chat ID">
            <Input placeholder="123456789" />
          </Form.Item>
          <Form.Item name="is_default" label="设为默认渠道" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
