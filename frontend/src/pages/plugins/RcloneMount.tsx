import { useState } from 'react';
import PluginConfigForm from '../../components/PluginConfigForm';
import LogViewer from '../../components/LogViewer';
import type { PluginField } from '../../components/PluginConfigForm';
import api from '../../utils/api';

const FIELDS: PluginField[] = [
  { key: 'home', label: 'Home 目录', type: 'string', default: '/root', help: 'rclone 配置目录的基础路径' },
  { key: 'config_file', label: 'Rclone 配置文件', type: 'string', placeholder: '/root/.config/rclone/rclone.conf', help: '留空则使用 home/.config/rclone/rclone.conf' },
  { key: 'remote', label: '远程名称', type: 'string', default: 'alist:/', required: true, help: 'rclone remote 名称，例如 alist:/' },
  { key: 'mount_point', label: '挂载点', type: 'string', default: '/volume1/docker/Alist/media', required: true, help: '本地 FUSE 挂载目录' },
  { key: 'cache_size', label: '缓存大小', type: 'string', default: '10G', help: 'VFS 缓存最大大小' },
  { key: 'cache_age_m', label: '缓存有效期(分钟)', type: 'number', default: 15, help: 'VFS 缓存最大保留时间' },
];

export default function RcloneMount() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'rclone_mount');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`, null, { timeout: 120000 });
      setRunResult(res.data?.result);
    } catch { /* ignore */ }
    finally { setRunning(false); }
  };

  return (
    <>
      <PluginConfigForm
        slug="rclone_mount"
        title="Rclone 挂载"
        description="通过 rclone FUSE 挂载 Alist 远程存储，支持 VFS 缓存。对应 rclone_mount_simple.sh 行为。"
        fields={FIELDS}
        onRun={handleRun}
        running={running}
        runResult={runResult}
        resultRenderer={(r: any) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
      />
      <div style={{ marginTop: 16 }}>
        <LogViewer source="rclone_mount" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
