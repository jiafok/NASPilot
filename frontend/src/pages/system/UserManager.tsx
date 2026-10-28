import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Form, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { ClockCircleOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined, TeamOutlined } from '@ant-design/icons';
import api from '../../utils/api';
import { useAuth } from '../../hooks/useAuth';

interface ManagedUser {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  is_admin: boolean;
  is_active: boolean;
  last_login_at: string | null;
  phone: string | null;
  membership: { plan: string; can_learn: boolean; expires_at: string | null } | null;
}

const PLAN_LABELS: Record<string, string> = { admin: '管理员', legacy: '原有账号', trial: '试用', monthly: '包月', quarterly: '包季', yearly: '包年', lifetime: '永久' };

interface NewUser {
  username: string;
  display_name?: string;
  password: string;
  role: 'learner' | 'admin';
}

function errorText(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean).join('；') || '输入内容无效';
  }
  return typeof detail === 'string' ? detail : '操作失败，请稍后重试';
}

export default function UserManager() {
  const { user: currentUser } = useAuth();
  const { message, modal } = App.useApp();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<NewUser>();
  const [grantUser, setGrantUser] = useState<ManagedUser | null>(null);
  const [grantSaving, setGrantSaving] = useState(false);
  const [plans, setPlans] = useState<Array<{ id: string; name: string; price: string; enabled: boolean }>>([]);
  const [grantForm] = Form.useForm<{ plan: string; confirmed: boolean; note: string }>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<{ items: ManagedUser[]; total: number }>('/users', { params: { page, page_size: 20, q: query } });
      setUsers(data.items);
      setTotal(data.total);
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setLoading(false);
    }
  }, [page, query, message]);

  useEffect(() => { void load(); }, [load]);

  const openGrant = async (user: ManagedUser) => {
    try {
      const { data } = await api.get('/membership/catalog');
      setPlans(data.plans.filter((plan: { enabled: boolean }) => plan.enabled));
      grantForm.resetFields(); setGrantUser(user);
    } catch (error) { message.error(errorText(error)); }
  };

  const grantMembership = async (values: { plan: string; confirmed: boolean; note?: string }) => {
    if (!grantUser) return;
    setGrantSaving(true);
    try {
      await api.post(`/membership/admin/users/${grantUser.id}/grant`, { ...values, note: values.note || '' });
      message.success('学习套餐已开通'); setGrantUser(null); await load();
    } catch (error) { message.error(errorText(error)); }
    finally { setGrantSaving(false); }
  };

  const addUser = async (values: NewUser) => {
    setSaving(true);
    try {
      const { role, ...account } = values;
      await api.post('/users', { ...account, is_admin: role === 'admin', display_name: values.display_name?.trim() || null });
      message.success('用户已创建');
      setOpen(false);
      form.resetFields();
      if (page !== 1) setPage(1);
      else void load();
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setSaving(false);
    }
  };

  const deleteUser = (user: ManagedUser) => {
    modal.confirm({
      title: `删除用户「${user.username}」？`,
      content: '该账号的学习进度、收藏、书签和登录会话将永久删除。媒体文件和共享课程不会删除；手机号占用记录会保留，不能重新领取试用。此操作不可恢复。',
      okText: '确认删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.delete(`/users/${user.id}`);
          message.success('用户已删除');
          if (users.length === 1 && page > 1) setPage(page - 1);
          else await load();
        } catch (error) {
          message.error(errorText(error));
          throw error;
        }
      },
    });
  };

  return (
    <div>
      <Space style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', marginBottom: 20 }}>
        <Typography.Title level={4} style={{ margin: 0 }}><TeamOutlined /> 用户管理</Typography.Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setOpen(true); }}>新增用户</Button>
        </Space>
      </Space>
      <Input.Search
        aria-label="搜索用户"
        placeholder="搜索用户名、昵称或邮箱"
        allowClear
        onSearch={(value) => { setQuery(value.trim()); setPage(1); }}
        style={{ maxWidth: 360, marginBottom: 16 }}
      />
      <Table<ManagedUser>
        rowKey="id"
        loading={loading}
        dataSource={users}
        scroll={{ x: 1150 }}
        pagination={{ current: page, pageSize: 20, total, onChange: setPage, showSizeChanger: false, showTotal: (count) => `共 ${count} 位用户` }}
        columns={[
          { title: '用户名', dataIndex: 'username', render: (name: string, record) => <Space><Typography.Text strong>{name}</Typography.Text>{record.id === currentUser?.id && <Tag>当前账号</Tag>}</Space> },
          { title: '昵称', dataIndex: 'display_name', render: (value: string | null) => value || '—' },
          { title: '手机号', dataIndex: 'phone', render: (value: string | null) => value || '未绑定' },
          { title: '邮箱', dataIndex: 'email', render: (value: string | null) => value || '—' },
          { title: '角色', dataIndex: 'is_admin', render: (admin: boolean) => <Tag color={admin ? 'gold' : 'green'}>{admin ? '管理员' : '学习用户'}</Tag> },
          { title: '状态', dataIndex: 'is_active', render: (active: boolean) => <Tag color={active ? 'success' : 'default'}>{active ? '正常' : '已停用'}</Tag> },
          { title: '学习权益', render: (_, record) => <Space direction="vertical" size={2}><Tag color={record.membership?.can_learn ? 'green' : 'red'}>{record.membership?.can_learn ? PLAN_LABELS[record.membership.plan] || record.membership.plan : '已到期'}</Tag><Typography.Text type="secondary" style={{ fontSize: 12 }}>{record.membership?.expires_at ? new Date(record.membership.expires_at).toLocaleString() : '长期有效'}</Typography.Text></Space> },
          { title: '最近登录', dataIndex: 'last_login_at', render: (value: string | null) => value ? new Date(value).toLocaleString() : '尚未登录' },
          { title: '操作', width: 110, fixed: 'right', render: (_, record) => <Space size={0}><Tooltip title="开通/续期学习套餐"><Button type="text" icon={<ClockCircleOutlined />} aria-label={`管理套餐 ${record.username}`} disabled={record.is_admin} onClick={() => void openGrant(record)} /></Tooltip><Tooltip title={record.id === currentUser?.id ? '不能删除当前账号' : '删除用户'}><Button danger type="text" icon={<DeleteOutlined />} aria-label={`删除用户 ${record.username}`} disabled={record.id === currentUser?.id} onClick={() => deleteUser(record)} /></Tooltip></Space> },
        ]}
      />
      <Modal title={`开通/续期：${grantUser?.username || ''}`} open={!!grantUser} onCancel={() => setGrantUser(null)} onOk={() => grantForm.submit()} confirmLoading={grantSaving} okButtonProps={{ disabled: plans.length === 0 }} okText="确认开通" cancelText="取消" destroyOnHidden>
        {plans.length === 0 && <Alert type="warning" message="请先在系统设置 → 注册与套餐中维护价格并启用套餐" style={{ marginBottom: 16 }} />}
        <Form form={grantForm} layout="vertical" preserve={false} onFinish={grantMembership}>
          <Form.Item name="plan" label="套餐" rules={[{ required: true, message: '请选择套餐' }]}><Select options={plans.map((plan) => ({ value: plan.id, label: `${plan.name} · ¥${Number(plan.price).toFixed(2)}` }))} /></Form.Item>
          <Form.Item name="note" label="收款/开通备注"><Input.TextArea rows={2} maxLength={500} /></Form.Item>
          <Form.Item name="confirmed" valuePropName="checked" rules={[{ validator: (_, value) => value === true ? Promise.resolve() : Promise.reject(new Error('请确认已完成收款')) }]}><Checkbox>我已确认收款，按当前价格记录并开通套餐</Checkbox></Form.Item>
        </Form>
      </Modal>
      <Modal title="新增用户" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={saving} okText="创建用户" cancelText="取消" destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={addUser} initialValues={{ role: 'learner' }} preserve={false}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }, { pattern: /^[A-Za-z0-9_][A-Za-z0-9_.-]{2,63}$/, message: '3–64 位字母、数字、下划线、点或短横线' }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="display_name" label="昵称" rules={[{ max: 128, message: '昵称不能超过 128 位' }]}><Input /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, message: '请输入初始密码' }, { min: 8, max: 64, message: '密码为 8–64 位' }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={[{ value: 'learner', label: '学习用户（仅英语 PWA）' }, { value: 'admin', label: '管理员（可访问 NAS 后台）' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}