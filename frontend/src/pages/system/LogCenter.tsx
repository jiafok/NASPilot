import { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Select, Input, Space, Typography, Button, Divider } from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import api from '../../utils/api';
import LogViewer from '../../components/LogViewer';

const { Title } = Typography;

interface LogEntry {
  id: number;
  logger: string;
  level: string;
  source: string;
  message: string;
  timestamp: string;
}

const levelColors: Record<string, string> = {
  DEBUG: 'default',
  INFO: 'blue',
  WARNING: 'orange',
  ERROR: 'red',
  CRITICAL: 'magenta',
};

const SOURCE_OPTIONS = [
  { label: '系统', value: 'system' },
  { label: '调度器', value: 'scheduler' },
  { label: '任务', value: 'task' },
  { label: '插件:pt_rss', value: 'plugin:pt_rss' },
  { label: '插件:ddns', value: 'plugin:cloudflare_ddns' },
  { label: '插件:备份', value: 'plugin:docker_backup' },
  { label: '插件:alist', value: 'plugin:alist_upload' },
];

export default function LogCenter() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [level, setLevel] = useState<string | undefined>(undefined);
  const [source, setSource] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState('');

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = { limit: 200 };
      if (level) params.level = level;
      if (source) params.source = source;
      const res = await api.get('/system/logs', { params });
      setLogs(res.data);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [level, source]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  // Auto-refresh every 5s
  useEffect(() => {
    const t = setInterval(() => fetchLogs(), 5000);
    return () => clearInterval(t);
  }, [fetchLogs]);

  const filtered = search
    ? logs.filter((l) => l.message.toLowerCase().includes(search.toLowerCase()))
    : logs;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>日志中心</Title>
        <Space>
          <Input
            placeholder="搜索日志..."
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 200 }}
            allowClear
          />
          <Select
            placeholder="级别"
            allowClear
            style={{ width: 100 }}
            value={level}
            onChange={setLevel}
            options={['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map((l) => ({ label: l, value: l }))}
          />
          <Select
            placeholder="来源"
            allowClear
            style={{ width: 140 }}
            value={source}
            onChange={setSource}
            options={SOURCE_OPTIONS}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchLogs}>刷新</Button>
        </Space>
      </div>

      <Table
        dataSource={filtered}
        rowKey="id"
        size="small"
        loading={loading}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
        columns={[
          {
            title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 170,
            render: (t: string) => new Date(t).toLocaleString(),
          },
          {
            title: '级别', dataIndex: 'level', key: 'level', width: 80,
            render: (l: string) => <Tag color={levelColors[l] || 'default'}>{l}</Tag>,
          },
          { title: '来源', dataIndex: 'source', key: 'source', width: 120 },
          { title: 'Logger', dataIndex: 'logger', key: 'logger', width: 140, ellipsis: true },
          { title: '消息', dataIndex: 'message', key: 'message', ellipsis: true },
        ]}
      />

      <Divider />

      <Typography.Title level={5} style={{ marginBottom: 8 }}>📡 实时日志流</Typography.Title>
      <LogViewer maxHeight={400} placeholder="连接中..." />
    </div>
  );
}
