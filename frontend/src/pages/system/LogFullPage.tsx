import { useSearchParams } from 'react-router-dom';
import { useEffect, useState, useCallback } from 'react';
import { Typography, Spin, Select, Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getToken } from '../../utils/auth';

const { Title } = Typography;

const LEVEL_OPTIONS = [
  { label: '全部级别', value: '' },
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
];

const SOURCE_OPTIONS = [
  { label: '全部来源', value: '' },
  { label: '系统', value: 'system' },
  { label: '调度器', value: 'scheduler' },
  { label: '任务', value: 'task' },
  { label: 'PT RSS', value: 'plugin:pt_rss' },
  { label: 'DDNS', value: 'plugin:cloudflare_ddns' },
  { label: '备份', value: 'plugin:docker_backup' },
  { label: 'AList', value: 'plugin:alist_upload' },
];

export default function LogFullPage() {
  const [params] = useSearchParams();
  const [source, setSource] = useState(params.get('source') || '');
  const [level, setLevel] = useState('');
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchLogs = useCallback(async (showSpinner: boolean) => {
    if (showSpinner) setLoading(true);
    try {
      const token = getToken();
      const url = new URL('/api/v1/system/logs/raw', window.location.origin);
      if (source) url.searchParams.set('source', source);
      if (level) url.searchParams.set('level', level);
      url.searchParams.set('limit', '20000');
      const res = await fetch(url.toString(), {
        headers: { Authorization: `Bearer ${token}` },
      });
      setText(await res.text());
    } catch {}
    finally { setLoading(false); }
  }, [source, level]);

  useEffect(() => { fetchLogs(true); }, [fetchLogs]);
  useEffect(() => {
    const t = setInterval(() => fetchLogs(false), 3000);
    return () => clearInterval(t);
  }, [fetchLogs]);

  const lineCount = text ? text.split('\n').filter(l => l.trim()).length : 0;

  return (
    <div style={{ padding: 8, height: '100vh', display: 'flex', flexDirection: 'column', background: '#1e1e1e' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
        <Title level={5} style={{ margin: 0, color: '#e0e0e0' }}>📄 原始日志文件</Title>
        <Select size="small" style={{ width: 130 }} value={source || undefined}
          onChange={(v) => setSource(v || '')} placeholder="来源" options={SOURCE_OPTIONS} />
        <Select size="small" style={{ width: 110 }} value={level || undefined}
          onChange={(v) => setLevel(v || '')} placeholder="级别" options={LEVEL_OPTIONS} />
        <Button size="small" icon={<ReloadOutlined />} onClick={() => fetchLogs(true)}>刷新</Button>
        {loading && <Spin size="small" />}
        <span style={{ color: '#888', fontSize: 11 }}>{lineCount} 行 · 3s 自动刷新</span>
      </div>
      <pre style={{
        flex: 1, overflow: 'auto', margin: 0, padding: '8px 12px', border: 0,
        fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace", fontSize: 11,
        lineHeight: '18px', color: '#d4d4d4', background: '#1a1a1a',
        borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
      }}>
        {text || (loading ? '' : '暂无日志')}
      </pre>
    </div>
  );
}
