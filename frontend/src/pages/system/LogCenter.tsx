import { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Select, Input, Space, Typography, Button, Divider, Collapse } from 'antd';
import { ReloadOutlined, SearchOutlined, ExportOutlined, HistoryOutlined } from '@ant-design/icons';
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

const LEVEL_COLORS: Record<string, string> = { DEBUG: 'default', INFO: 'blue', WARNING: 'orange', ERROR: 'red', CRITICAL: 'magenta' };

const SOURCE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '系统', value: 'system' },
  { label: '调度器', value: 'scheduler' },
  { label: '任务', value: 'task' },
  { label: '插件:pt_rss', value: 'plugin:pt_rss' },
  { label: '插件:ddns', value: 'plugin:cloudflare_ddns' },
  { label: '插件:备份', value: 'plugin:docker_backup' },
  { label: '插件:alist', value: 'plugin:alist_upload' },
  { label: '插件:清理', value: 'plugin:log_cleanup' },
];

export default function LogCenter() {
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

  useEffect(() => { fetchLogs(true); }, []); // Initial load only

  return (
    <div>
      {/* ── Real-time stream ── */}
      <Title level={4} style={{ marginBottom: 8 }}>📡 实时日志</Title>
      <div style={{ marginBottom: 16, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <LogViewer maxHeight={400} maxLines={8000} placeholder="连接中... 等待日志产生" showOpenWindow />
      </div>

      <Divider />

      {/* ── History table (collapsible) ── */}
      <Collapse
        items={[{
          key: 'history',
          label: <span><HistoryOutlined /> 历史日志 · {logs.length} 条</span>,
          children: (
            <div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
                <Space wrap>
                  <Input placeholder="搜索消息..." prefix={<SearchOutlined />} value={search}
                    onChange={(e) => setSearch(e.target.value)} style={{ width: 180 }} allowClear />
                  <Select placeholder="级别" allowClear style={{ width: 100 }} value={level} onChange={setLevel}
                    options={['DEBUG','INFO','WARNING','ERROR','CRITICAL'].map(l=>({label:l,value:l}))} />
                  <Select placeholder="来源" allowClear style={{ width: 140 }} value={source} onChange={setSource}
                    options={SOURCE_OPTIONS} />
                  <Select style={{ width: 90 }} value={limit} onChange={setLimit}
                    options={[50,100,200,500,1000].map(n=>({label:`${n} 条`,value:n}))} />
                  <Button icon={<ReloadOutlined />} onClick={() => fetchLogs(true)}>刷新</Button>
                  <Button icon={<ExportOutlined />}
                    onClick={() => window.open('/logs/full', '_blank', 'width=1100,height=800')}>全屏日志</Button>
                </Space>
              </div>

              <Table
                dataSource={logs} rowKey="id" size="small" loading={loading}
                pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t: number) => `共 ${t} 条` }}
                columns={[
                  { title: '时间', dataIndex: 'timestamp', width: 170,
                    render: (t: string) => new Date(t).toLocaleString() },
                  { title: '级别', dataIndex: 'level', width: 80,
                    render: (l: string) => <Tag color={LEVEL_COLORS[l]||'default'}>{l}</Tag> },
                  { title: '来源', dataIndex: 'source', width: 130 },
                  { title: 'Logger', dataIndex: 'logger', width: 150, ellipsis: true },
                  { title: '消息', dataIndex: 'message', ellipsis: true,
                    render: (m: string) => <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{m}</span> },
                ]}
              />
            </div>
          ),
        }]} />
    </div>
  );
}
