import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';

const FIELDS: PluginField[] = [
  { key: 'backup_dir', label: 'Backup Directory', type: 'string', default: '/app/data/docker_backup', required: true, help: '备份文件存放目录' },
  { key: 'containers', label: 'Containers', type: 'array', placeholder: 'naspilot, qbittorrent', help: '逗号分隔的容器名。留空则备份所有。' },
  { key: 'volumes', label: 'Volumes', type: 'array', placeholder: 'volume1, volume2', help: '额外需要备份的卷路径' },
  { key: 'compress', label: 'Compress', type: 'boolean', default: true, help: '使用 .tgz 压缩备份' },
  { key: 'keep_days', label: 'Keep Days', type: 'number', default: 7, help: '保留最近 N 天的备份，自动清理旧文件' },
];

export default function DockerBackup() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'docker_backup');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`);
      setRunResult(res.data?.result);
    } catch {}
    finally { setRunning(false); }
  };

  return (
    <PluginConfigForm
      slug="docker_backup"
      title="Docker Backup"
      description="Backup container configs + volumes with auto-cleanup. Web UI for backup_docker_all_core.sh."
      fields={FIELDS}
      onRun={handleRun}
      running={running}
      runResult={runResult}
      resultRenderer={(r) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
    />
  );
}
