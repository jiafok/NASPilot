import { useState, useEffect } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import LogViewer from '../components/LogViewer';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';
import { Tag, Descriptions, List, Typography, Collapse, Table, Spin } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined, InfoCircleOutlined, UnorderedListOutlined } from '@ant-design/icons';

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
    { key: 'seed_days', label: 'Seed Days', type: 'number', default: 2, help: '做种超过此天数后自动删除' },
    { key: 'stuck_download_days', label: 'Stuck Download Days', type: 'number', default: 3, help: '卡住超过此天数的下载自动删除' },
  ]},
  { key: 'free_ttl_hours', label: 'Free TTL (hours)', type: 'number', default: 48, help: 'Free 种子限时下载窗口' },
  { key: 'rss_missing_threshold', label: 'RSS Missing Threshold', type: 'number', default: 2, help: '连续多少次不在 RSS 中后移除' },
  { key: 'enable_rss_eviction', label: 'Enable RSS Eviction', type: 'boolean', default: true },
];

export default function PT_RSS() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);
  const [processed, setProcessed] = useState<Record<string, any>>({});
  const [processedLoading, setProcessedLoading] = useState(false);

  const loadProcessed = async () => {
    setProcessedLoading(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const pt = (pluginsRes.data as any[]).find((x: any) => x.slug === 'pt_rss');
      if (!pt) return;
      const instRes = await api.get(`/plugins/${pt.id}/instances`);
      const inst = (instRes.data as any[])[0];
      if (inst?.config?.state?.processed) {
        setProcessed(inst.config.state.processed);
      }
    } catch {}
    finally { setProcessedLoading(false); }
  };

  useEffect(() => { loadProcessed(); }, []);

  const handleRun = async () => {
    setRunning(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const pt = (pluginsRes.data as any[]).find((x: any) => x.slug === 'pt_rss');
      if (!pt) return;
      const res = await api.post(`/plugins/${pt.id}/run`);
      setRunResult(res.data?.result || res.data);
      await loadProcessed();
    } catch (err: any) {
      const body = err?.response?.data;
      const detail = typeof body === 'string' ? body : body?.detail || body?.result?.error || err?.message || 'Unknown error';
      setRunResult({ status: 'error', error: detail });
    }
    finally { setRunning(false); }
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
            <InfoCircleOutlined /> No new items in RSS. Everything is up to date.
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

  const processedColumns = [
    { title: 'TID', dataIndex: 'tid', width: 80, ellipsis: true },
    { title: 'Title', dataIndex: 'title', ellipsis: true },
    { title: 'Status', dataIndex: 'status', width: 110,
      render: (s: string) => {
        const color = s === 'added' ? 'blue' : s === 'completed' ? 'green' : s === 'evicted' ? 'red' : s === 'expired_free' ? 'orange' : 'default';
        return <Tag color={color}>{s}</Tag>;
      },
    },
    { title: 'Missing', dataIndex: 'missingCount', width: 70 },
    { title: 'First Seen', dataIndex: 'firstSeen', width: 160, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
    { title: 'Added', dataIndex: 'addedTime', width: 160, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
    { title: 'Evicted Reason', dataIndex: 'evictedReason', width: 160, ellipsis: true, render: (v: string) => v || '-' },
  ];

  const processedPanel = (
    <Collapse
      style={{ marginBottom: 16 }}
      defaultActiveKey={processedEntries.length > 0 ? ['processed'] : []}
      items={[{
        key: 'processed',
        label: <span><UnorderedListOutlined /> Processed Items ({processedEntries.length})</span>,
        children: processedLoading
          ? <Spin style={{ display: 'block', margin: '20px auto' }} />
          : processedEntries.length === 0
            ? <Typography.Text type="secondary">No items processed yet. Run the plugin once to populate this table.</Typography.Text>
            : <Table dataSource={processedEntries} columns={processedColumns} size="small" rowKey="tid" pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t: number) => `${t} items` }} />,
      }]}>
    </Collapse>
  );

  const logPanel = (
    <div style={{ marginBottom: 16 }}>
      <LogViewer source="plugin:pt_rss" maxHeight={400} placeholder="等待运行... 点击 Run Now 查看实时日志" collapsible defaultOpen={false} label="运行日志" />
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
    >
      {logPanel}
    </PluginConfigForm>
  );
}
