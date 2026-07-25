import { useState } from 'react';
import PluginConfigForm from '../../components/PluginConfigForm';
import LogViewer from '../../components/LogViewer';
import type { PluginField } from '../../components/PluginConfigForm';
import api from '../../utils/api';

const FIELDS: PluginField[] = [
  { key: 'log_dir', label: '日志目录', type: 'string', default: '/app/data/logs', required: true, help: '容器内日志文件目录' },
  { key: 'max_age_days', label: '保留天数', type: 'number', default: 30, help: '超过此天数的 .log 文件将被删除' },
  { key: 'max_size_kb', label: '单文件上限(KB)', type: 'number', default: 102400, help: '超过此大小截断，保留末尾行' },
  { key: 'tail_lines', label: '截断保留行数', type: 'number', default: 2000 },
];

export default function LogCleanup() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'log_cleanup');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`, null, { timeout: 60000 });
      setRunResult(res.data?.result);
    } catch { /* ignore */ }
    finally { setRunning(false); }
  };

  return (
    <>
      <PluginConfigForm
        slug="log_cleanup"
        title="日志清理"
        description="定期清理过期和过大的日志文件，保持磁盘空间可控。"
        fields={FIELDS}
        onRun={handleRun}
        running={running}
        runResult={runResult}
        resultRenderer={(r: any) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
      />
      <div style={{ marginTop: 16 }}>
        <LogViewer source="log_cleanup" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
