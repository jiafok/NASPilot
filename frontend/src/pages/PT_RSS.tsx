import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';

const FIELDS: PluginField[] = [
  { key: 'rss_urls', label: 'RSS URLs', type: 'textarea', placeholder: 'https://example.com/rss.xml', required: true, help: '一行一个 RSS 订阅地址' },
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

  const handleRun = async () => {
    setRunning(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const pt = (pluginsRes.data as any[]).find((x: any) => x.slug === 'pt_rss');
      if (!pt) return;
      const res = await api.post(`/plugins/${pt.id}/run`);
      setRunResult(res.data?.result);
    } catch {}
    finally { setRunning(false); }
  };

  return (
    <PluginConfigForm
      slug="pt_rss"
      title="PT RSS Auto Download"
      description="Monitor RSS feeds, auto-add torrents to qBittorrent, manage disk space and seeding. This is the Web UI for your pt_rss_auto.py script."
      fields={FIELDS}
      onRun={handleRun}
      running={running}
      runResult={runResult}
      resultRenderer={(r) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
    />
  );
}
