import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Table, Tag, Select, Input, Space, Typography, Button } from 'antd';
import { ReloadOutlined, SearchOutlined, ExportOutlined, FilterOutlined } from '@ant-design/icons';
import api from '../../utils/api';

const { Title } = Typography;

interface LogEntry {
  id: number;
  logger: string;
  level: string;
  source: string;
  message: string;
  timestamp: string;
}

const LEVEL_COLORS: Record<string, string> = { DEBUG: 'default', INFO: 'blue', WARNING: 'orange', ERROR: 'red', CRITICAL: 'magenta' };

const SOURCE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '系统', value: 'system' },
  { label: '调度器', value: 'scheduler' },
  { label: '任务', value: 'task' },
  { label: '插件:pt_rss', value: 'pt_rss' },
  { label: '插件:ddns', value: 'cloudflare_ddns' },
  { label: '插件:备份', value: 'docker_backup' },
  { label: '插件:alist', value: 'alist_upload' },
  { label: '插件:清理', value: 'log_cleanup' },
];

export default function LogCenter() {
  const { t } = useTranslation();
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [level, setLevel] = useState<string | undefined>(undefined);
  const [source, setSource] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(200);

  const fetchLogs = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    try {
      const params: any = { limit };
      if (level) params.level = level;
      if (source) params.source = source;
      if (search) params.search = search;
      const res = await api.get('/system/logs', { params });
      setLogs(res.data);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [level, source, search, limit]);

  // Auto-refresh when filters change (debounced for text input)
  useEffect(() => {
    const t = setTimeout(() => fetchLogs(false), 350);
    return () => clearTimeout(t);
  }, [level, source, search, limit, fetchLogs]);

  useEffect(() => { fetchLogs(true); }, []); // Initial load only

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <Title level={4} style={{ margin: 0 }}>📋 {t('system.logs')}</Title>
        <Space wrap>
          <Input placeholder={t('common.search')} prefix={<SearchOutlined />} value={search}
            onChange={(e) => setSearch(e.target.value)} style={{ width: 180 }} allowClear />
          <Select placeholder={t('logs.level')} allowClear style={{ width: 100 }} value={level} onChange={setLevel}
            options={['DEBUG','INFO','WARNING','ERROR','CRITICAL'].map(l=>({label:l,value:l}))} />
          <Select placeholder={t('logs.source')} allowClear style={{ width: 140 }} value={source} onChange={setSource}
            options={SOURCE_OPTIONS} />
          <Button icon={<ReloadOutlined />} onClick={() => fetchLogs(true)}>{t('common.refresh')}</Button>
          <Button icon={<ExportOutlined />}
            onClick={() => window.open('/logs/full', '_blank', 'width=1100,height=800')}>{t('system.fullscreenLogs')}</Button>
        </Space>
      </div>

      <Space wrap style={{ marginBottom: 12 }}>
        <FilterOutlined />
        <Tag color="red" style={{ cursor: 'pointer' }} onClick={() => setLevel(level === 'ERROR' ? undefined : 'ERROR')}>ERROR {level === 'ERROR' ? '✓' : ''}</Tag>
        <Tag color="orange" style={{ cursor: 'pointer' }} onClick={() => setLevel(level === 'WARNING' ? undefined : 'WARNING')}>WARNING {level === 'WARNING' ? '✓' : ''}</Tag>
        <Tag color="blue" style={{ cursor: 'pointer' }} onClick={() => setLevel(level === 'INFO' ? undefined : 'INFO')}>INFO {level === 'INFO' ? '✓' : ''}</Tag>
        <Tag style={{ cursor: 'pointer' }} onClick={() => setLevel(undefined)}>全部</Tag>
      </Space>

      <Table
        dataSource={logs} rowKey="id" size="small" loading={loading}
        scroll={{ x: 700 }}
        pagination={{ defaultPageSize: 200, showSizeChanger: true, pageSizeOptions: [50,100,200,500,1000], showTotal: (total: number) => t('common.items', { count: total }),
          onChange: (_page: number, pageSize: number) => { if (pageSize !== limit) setLimit(pageSize); } }}
        columns={[
          { title: t('common.time'), dataIndex: 'timestamp', width: 170,
            render: (ts: string) => new Date(ts).toLocaleString('zh-CN', { hour12: false }) },
          { title: t('logs.level'), dataIndex: 'level', width: 80,
            render: (l: string) => <Tag color={LEVEL_COLORS[l]||'default'}>{l}</Tag> },
          { title: t('logs.source'), dataIndex: 'source', width: 130 },
          { title: 'Logger', dataIndex: 'logger', width: 150, ellipsis: true },
          { title: t('common.message'), dataIndex: 'message', ellipsis: true,
            render: (m: string) => <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{m}</span> },
        ]}
      />
    </div>
  );
}
