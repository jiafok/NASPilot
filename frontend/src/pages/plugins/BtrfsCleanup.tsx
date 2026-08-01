import { useState } from 'react';
import PluginConfigForm from '../../components/PluginConfigForm';
import LogViewer from '../../components/LogViewer';
import type { PluginField } from '../../components/PluginConfigForm';
import api from '../../utils/api';

const FIELDS: PluginField[] = [
  { key: 'subvol_path', label: 'Btrfs 子卷路径', type: 'string', default: '/volume1/@docker/btrfs/subvolumes', required: true, help: 'Docker btrfs 子卷目录路径' },
  { key: 'min_age_days', label: '最小老化天数', type: 'number', default: 7, help: '仅清理超过此天数的孤儿子卷' },
  { key: 'dry_run', label: '仅预览 (Dry Run)', type: 'boolean', default: true, help: '开启时仅列出将被清理的子卷，不实际删除' },
];

export default function BtrfsCleanup() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'btrfs_cleanup');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`, null, { timeout: 120000 });
      setRunResult(res.data?.result);
    } catch { /* ignore */ }
    finally { setRunning(false); }
  };

  return (
    <>
      <PluginConfigForm
        slug="btrfs_cleanup"
        title="Btrfs 子卷清理"
        description="扫描并清理 Docker 孤儿 Btrfs 子卷，释放磁盘空间。对应 clean_btrfs.sh 行为。"
        fields={FIELDS}
        onRun={handleRun}
        running={running}
        runResult={runResult}
        resultRenderer={(r: any) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
      />
      <div style={{ marginTop: 16 }}>
        <LogViewer source="btrfs_cleanup" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
