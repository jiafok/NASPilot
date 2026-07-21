import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';

const FIELDS: PluginField[] = [
  { key: 'docker_root', label: 'Docker App Directory', type: 'string', default: '/volume1/docker', required: true, help: 'NAS 上 Docker 应用目录，包含每个应用的 config/data/conf/db 子目录' },
  { key: 'backup_dir', label: 'Backup Destination', type: 'string', default: '/volumeUSB1/usbshare/docker_backup', required: true, help: '备份文件输出目录（建议 USB 外接存储）' },
  { key: 'containers', label: 'App Filter', type: 'array', placeholder: 'v2raya, qbittorrent', help: '逗号分隔的应用名。留空则备份所有含 config/data/conf/db 的应用。' },
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
      title="Docker App Backup"
      description="备份 /volume1/docker 下每个应用的配置和数据目录（排除 media/downloads/cache/logs）。v2raya 采用白名单备份。完全对应 backup_docker_all_core.sh 的行为。"
      fields={FIELDS}
      onRun={handleRun}
      running={running}
      runResult={runResult}
      resultRenderer={(r) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
    />
  );
}
