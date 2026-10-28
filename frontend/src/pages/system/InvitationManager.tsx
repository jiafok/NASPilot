import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, InputNumber, Space, Table, Tag, Typography } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '../../utils/api';

interface Invitation {
  id: string;
  code_hint: string;
  expires_at: string;
  status: string;
  used_by_username: string | null;
}

const STATUS: Record<string, string> = { available: '未使用', used: '已使用', orphaned: '账号已删除', expired: '已过期', revoked: '已撤销' };

export default function InvitationManager() {
  const { message, modal } = App.useApp();
  const [rows, setRows] = useState<Invitation[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [validDays, setValidDays] = useState(7);
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState<{ code: string; expires_at: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/membership/admin/invitations', { params: { page, page_size: 10 } });
      setRows(data.items); setTotal(data.total);
    } catch { message.error('邀请码列表加载失败'); }
    finally { setLoading(false); }
  }, [page, message]);

  useEffect(() => { void load(); }, [load]);

  const generate = async () => {
    setGenerating(true);
    try {
      const { data } = await api.post('/membership/admin/invitations', { valid_days: validDays });
      setGenerated(data); message.success('邀请码已生成');
      if (page !== 1) setPage(1); else await load();
    } catch { message.error('邀请码生成失败'); }
    finally { setGenerating(false); }
  };

  const remove = (row: Invitation) => modal.confirm({
    title: row.status === 'available' ? '撤销并删除这个邀请码？' : '删除这个邀请码记录？',
    content: row.status === 'available' ? '确认后会立即从列表删除，邀请码也将失效。' : '确认后将从列表永久删除该记录。',
    okText: '确认删除', cancelText: '取消',
    onOk: async () => {
      try { await api.delete(`/membership/admin/invitations/${row.id}`); await load(); }
      catch (error) { message.error('删除失败；已使用邀请码请先删除对应账号'); throw error; }
    },
  });

  return (
    <div style={{ marginBottom: 24 }}>
      <Space wrap style={{ marginBottom: 16 }}>
        <span>邀请码有效期</span><InputNumber aria-label="邀请码有效天数" min={1} max={365} value={validDays} onChange={(value) => setValidDays(value || 7)} /><span>天</span>
        <Button type="primary" icon={<PlusOutlined />} loading={generating} onClick={() => void generate()}>生成邀请码</Button>
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新列表</Button>
      </Space>
      {generated && <Alert type="success" showIcon style={{ marginBottom: 16 }} message="完整邀请码仅本次展示，请复制后发给用户" description={<>
        <Typography.Paragraph copyable={{ text: generated.code }} style={{ overflowWrap: 'anywhere', marginBottom: 4 }}><span data-testid="generated-invite">{generated.code}</span></Typography.Paragraph>
        <span>有效期至 {new Date(generated.expires_at).toLocaleString()}；用户在 /register 填写即可注册并试用 3 天。</span>
      </>} />}
      <Table<Invitation>
        rowKey="id" size="small" dataSource={rows} loading={loading} scroll={{ x: 650 }}
        pagination={{ current: page, total, pageSize: 10, showSizeChanger: false, onChange: setPage }}
        columns={[
          { title: '邀请码', dataIndex: 'code_hint' },
          { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'available' ? 'green' : 'default'}>{STATUS[value] || value}</Tag> },
          { title: '有效期至', dataIndex: 'expires_at', render: (value: string) => new Date(value).toLocaleString() },
          { title: '使用者', dataIndex: 'used_by_username', render: (value: string | null) => value || '—' },
          { title: '操作', render: (_, row) => row.status === 'used' ? '—' : <Button danger type="link" onClick={() => remove(row)}>{row.status === 'available' ? '撤销并删除' : '删除'}</Button> },
        ]}
      />
    </div>
  );
}