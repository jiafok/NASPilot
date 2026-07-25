import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import LogViewer from '../components/LogViewer';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';
import { Tag, Descriptions, List, Typography, Collapse, Table, Button, Modal, Select, Input, Space, message, Tooltip } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined, InfoCircleOutlined, UnorderedListOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';

const FIELDS: PluginField[] = [
  { key: 'rss_urls', label: 'RSS URLs', type: 'textarea', placeholder: 'https://example.com/rss.xml', required: true, help: '每行一个 RSS 地址。支持 M-Team 等 PT 站 RSS 链接。' },
  { key: 'qbittorrent', label: 'qBittorrent', type: 'object', fields: [
    { key: 'url', label: 'URL', type: 'string', placeholder: 'http://10.0.0.5:8080', required: true, help: 'qBittorrent Web UI 地址' },
    { key: 'username', label: 'Username', type: 'string', default: 'admin' },
    { key: 'password', label: 'Password', type: 'password' },
  ]},
  { key: 'download_dir', label: 'Download Directory', type: 'string', placeholder: '/downloads/pt', help: 'qBitorrent 内的下载路径' },
  { key: 'min_free_gb', label: 'Min Free Space (GB)', type: 'number', default: 50, help: '低于此值将暂停添加新任务并启动空间清理' },
  { key: 'max_active_downloads', label: 'Max Active Downloads', type: 'number', default: 15 },
  { key: 'free_check', label: 'Free Check', type: 'boolean', default: false, help: '启用后只下载 Free 种子' },
  { key: 'cleanup', label: 'Cleanup', type: 'object', fields: [
    { key: 'seed_days', label: 'Seed Days', type: 'number', default: 2, help: '做种超过此天数后自动删除（空间不足时）' },
    { key: 'stuck_download_days', label: 'Stuck Download Days', type: 'number', default: 3, help: '卡住超过此天数的下载自动删除' },
    { key: 'emergency_threshold_gb', label: 'Emergency Threshold (GB)', type: 'number', default: 20, help: '紧急清理触发线：空间低于此值立即清理' },
    { key: 'emergency_target_gb', label: 'Emergency Target (GB)', type: 'number', default: 30, help: '紧急清理目标：清理到此空间量即停止' },
  ]},
  { key: 'free_ttl_hours', label: 'Free TTL (hours)', type: 'number', default: 48, help: 'Free 种子限时下载窗口' },
  { key: 'rss_missing_threshold', label: 'RSS Missing Threshold', type: 'number', default: 2, help: '连续多少次不在 RSS 中后移除' },
  { key: 'enable_rss_eviction', label: 'Enable RSS Eviction', type: 'boolean', default: true },
  { key: 'gc', label: 'Processed GC', type: 'object', fields: [
    { key: 'evicted_days', label: 'Evicted Retention (days)', type: 'number', default: 15, help: '已驱逐记录的保留天数，超时自动清除（completed/expired_free 不清理）' },
  ]},
];

export default function PT_RSS() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);
  const [processed, setProcessed] = useState<Record<string, any>>({});
  const [instId, setInstId] = useState<number | null>(null);
  // Edit modal state
  const [editTid, setEditTid] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Record<string, any>>({});
  const [editVisible, setEditVisible] = useState(false);
  const [editSaving, setEditSaving] = useState(false);

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const pluginsRes = await api.get('/plugins');
      const pt = (pluginsRes.data as any[]).find((x: any) => x.slug === 'pt_rss');
      if (!pt) return;
      const res = await api.post(`/plugins/${pt.id}/run`, null, { timeout: 300000 });
      setRunResult(res.data?.result || res.data);
    } catch (err: any) {
      if (err?.code === 'ECONNABORTED' || err?.message?.includes('timeout')) {
        setRunResult({ status: 'error', error: '请求超时（任务可能仍在后台运行，查看日志确认进度）' });
      } else {
        const body = err?.response?.data;
        const detail = typeof body === 'string' ? body : body?.detail || body?.result?.error || err?.message || 'Unknown error';
        setRunResult({ status: 'error', error: detail });
      }
    }
    finally { setRunning(false); }
  };

  // Persist processed state to backend
  const saveProcessed = async (newProcessed: Record<string, any>) => {
    if (!instId) return;
    try {
      await api.put(`/plugins/instances/${instId}`, {
        config: { state: { processed: newProcessed } },
      });
      setProcessed({ ...newProcessed });
    } catch { message.error('保存失败'); }
  };

  // Delete a processed entry
  const handleDelete = (tid: string) => {
    const next = { ...processed };
    delete next[tid];
    saveProcessed(next);
  };

  // Open edit modal
  const handleEdit = (tid: string, rec: Record<string, any>) => {
    setEditTid(tid);
    setEditForm({
      title: rec.title || '',
      status: rec.status || '',
      evicted_reason: rec.evicted_reason || '',
      evicted_time: rec.evicted_time || '',
    });
    setEditVisible(true);
  };

  // Save edited entry
  const handleEditSave = async () => {
    if (!editTid) return;
    setEditSaving(true);
    const next = { ...processed };
    const existing = { ...next[editTid] };
    existing.title = editForm.title;
    existing.status = editForm.status;
    existing.evicted_reason = editForm.evicted_reason;
    if (editForm.evicted_time && editForm.evicted_time !== existing.evicted_time) {
      existing.evicted_time = editForm.evicted_time;
    }
    next[editTid] = existing;
    await saveProcessed(next);
    setEditSaving(false);
    setEditVisible(false);
    setEditTid(null);
  };

  const resultRenderer = (r: any) => {
    if (!r) return <Typography.Text type="secondary">No result</Typography.Text>;
    if (r.status === 'error' || r.status === 'failed') {
      return (
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="Status"><Tag color="red">Failed</Tag></Descriptions.Item>
          <Descriptions.Item label="Error">{r.error}</Descriptions.Item>
        </Descriptions>
      );
    }
    return (
      <div>
        <Descriptions bordered size="small" column={4}>
          <Descriptions.Item label="Status"><Tag color={r.status === 'ok' ? 'green' : 'red'}>{r.status}</Tag></Descriptions.Item>
          <Descriptions.Item label="RSS Sources">{r.rss_sources ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="Items Found">{r.rss_items_found ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="Added">{r.added ?? '-'}</Descriptions.Item>
          {r.rss_failed_sources?.length > 0 && (
            <Descriptions.Item label="Failed Sources" span={4}>{r.rss_failed_sources.join(', ')}</Descriptions.Item>
          )}
        </Descriptions>
        {r.added_messages?.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Typography.Title level={5}><CheckCircleOutlined style={{ color: 'green' }} /> Added ({r.added_messages.length})</Typography.Title>
            <List size="small" dataSource={r.added_messages} renderItem={(m: string) => <List.Item style={{ fontSize: 12 }}>{m}</List.Item>} />
          </div>
        )}
        {r.failed_messages?.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Typography.Title level={5}><CloseCircleOutlined style={{ color: 'red' }} /> Failed ({r.failed_messages.length})</Typography.Title>
            <List size="small" dataSource={r.failed_messages} renderItem={(m: string) => <List.Item style={{ fontSize: 12 }}>{m}</List.Item>} />
          </div>
        )}
        {r.deleted_messages?.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Typography.Title level={5}><ExclamationCircleOutlined style={{ color: 'orange' }} /> Deleted ({r.deleted_messages.length})</Typography.Title>
            <List size="small" dataSource={r.deleted_messages} renderItem={(m: string) => <List.Item style={{ fontSize: 12 }}>{m}</List.Item>} />
          </div>
        )}
        {!r.added_messages?.length && !r.failed_messages?.length && (
          <div style={{ marginTop: 12, padding: 16, textAlign: 'center', color: '#888' }}>
            <InfoCircleOutlined /> 暂无追踪记录。执行一次插件运行即可填充此表格。
          </div>
        )}
      </div>
    );
  };

  const processedEntries = Object.entries(processed).map(([tid, rec]: [string, any]) => ({
    key: tid, tid,
    title: rec.title || '-',
    status: rec.status || '-',
    firstSeen: rec.first_seen,
    missingCount: rec.rss_missing_count || 0,
    addedTime: rec.added_time,
    completedTime: rec.completed_time,
    evictedTime: rec.evicted_time,
    evictedReason: rec.evicted_reason,
  }));

  const processedColumns: any[] = [
    { title: 'TID', dataIndex: 'tid', width: 70, ellipsis: true, responsive: ['md'] },
    { title: '标题', dataIndex: 'title', ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 85, responsive: ['md'],
      render: (s: string) => {
        const color = s === 'added' ? 'blue' : s === 'completed' ? 'green' : s === 'evicted' ? 'red' : s === 'expired_free' ? 'orange' : 'default';
        const label = { pending_free: '待免费', added: '已添加', completed: '已完成', evicted: '已驱逐', expired_free: '已过期' }[s] || s;
        return <Tag color={color} style={{ fontSize: 11, lineHeight: '16px', margin: 0 }}>{label}</Tag>;
      },
    },
    { title: '缺失', dataIndex: 'missingCount', width: 50, align: 'center' as const },
    { title: '首次发现', dataIndex: 'firstSeen', width: 130,
      responsive: ['md'],
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' },
    { title: '驱逐时间', dataIndex: 'evictedTime', width: 130,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' },
    { title: '原因', dataIndex: 'evictedReason', width: 100, ellipsis: true,
      responsive: ['md'], render: (v: string) => v || '-' },
    {
      title: '操作', key: 'actions', width: 60, fixed: 'right' as const,
      render: (_: any, record: any) => (
        <Space size={2}>
          <Tooltip title="编辑">
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record.tid, record)} />
          </Tooltip>
          <Tooltip title="删除">
            <Button type="link" danger size="small" icon={<DeleteOutlined />}
              onClick={() => Modal.confirm({
                title: `确认删除 TID: ${record.tid}?`,
                okText: '删除', okType: 'danger', cancelText: '取消',
                onOk: () => handleDelete(record.tid),
              })}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  const processedPanel = (
    <Collapse
      style={{ marginBottom: 16 }}
      defaultActiveKey={processedEntries.length > 0 ? ['processed'] : []}
      destroyInactivePanel={false}
      items={[{
        key: 'processed',
        label: <span><UnorderedListOutlined /> 追踪记录 ({processedEntries.length})</span>,
        children: processedEntries.length === 0
          ? <Typography.Text type="secondary">暂无追踪记录。执行一次插件运行即可填充此表格。</Typography.Text>
          : <div style={{ overflow: 'visible' }}>
            <Table dataSource={processedEntries} columns={processedColumns} size="small" rowKey="tid"
              scroll={{ x: 600 }}
              pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (t: number) => `${t} 条` }} />
          </div>,
      }]}>
    </Collapse>
  );

  const logPanel = (
    <div style={{ marginBottom: 16 }}>
      <LogViewer source="pt_rss" maxHeight={400} placeholder="等待运行... 点击 Run Now 查看实时日志" collapsible defaultOpen={false} label="运行日志" />
    </div>
  );

  return (
    <PluginConfigForm
      slug="pt_rss"
      title="PT RSS Auto Download"
      description="Monitor RSS feeds, auto-add torrents to qBittorrent, manage disk space and seeding."
      fields={FIELDS}
      onRun={handleRun}
      running={running}
      runResult={runResult}
      resultRenderer={resultRenderer}
      topContent={processedPanel}
      onInstanceLoad={(inst) => {
        if (inst?.id) setInstId(inst.id);
        if (inst?.config?.state?.processed) {
          setProcessed(inst.config.state.processed);
        }
      }}
    >
      {logPanel}
      {/* ── Edit Processed Modal ── */}
      <Modal
        title={`编辑记录: ${editTid}`}
        open={editVisible}
        onOk={handleEditSave}
        onCancel={() => { setEditVisible(false); setEditTid(null); }}
        confirmLoading={editSaving}
        okText="保存"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <Typography.Text strong>标题</Typography.Text>
            <Input value={editForm.title || ''} onChange={(e) => setEditForm({ ...editForm, title: e.target.value })} placeholder="种子标题" />
          </div>
          <div>
            <Typography.Text strong>状态</Typography.Text>
            <Select style={{ width: '100%' }} value={editForm.status || 'pending_free'}
              onChange={(v) => setEditForm({ ...editForm, status: v })}
              options={[
                { label: 'pending_free（待免费）', value: 'pending_free' },
                { label: 'added（已添加）', value: 'added' },
                { label: 'completed（已完成）', value: 'completed' },
                { label: 'evicted（已驱逐）', value: 'evicted' },
                { label: 'expired_free（免费过期）', value: 'expired_free' },
              ]}
            />
          </div>
          <div>
            <Typography.Text strong>驱逐原因</Typography.Text>
            <Input value={editForm.evicted_reason || ''} onChange={(e) => setEditForm({ ...editForm, evicted_reason: e.target.value })} placeholder="可选" />
          </div>
          <div>
            <Typography.Text strong>驱逐时间 (ISO)</Typography.Text>
            <Input value={editForm.evicted_time || ''} onChange={(e) => setEditForm({ ...editForm, evicted_time: e.target.value })} placeholder="2026-07-25T10:00:00+08:00" />
          </div>
        </Space>
      </Modal>
    </PluginConfigForm>
  );
}
