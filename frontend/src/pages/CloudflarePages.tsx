import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import LogViewer from '../components/LogViewer';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';

const DEFAULT_SERVICES = JSON.stringify([
  { group: 'Synology 管理', name: 'DSM(HTTP)', port: 5000, ssl: false, path: '', enabled: true },
  { group: '媒体服务', name: 'Emby', port: 8098, ssl: false, path: '', enabled: true },
  { group: '下载影音', name: 'qBittorrent', port: 8080, ssl: false, path: '', enabled: true },
  { group: '下载影音', name: 'MoviePilot', port: 3002, ssl: false, path: '', enabled: true },
  { group: '文件与网盘', name: 'Alist', port: 5266, ssl: false, path: '', enabled: true },
  { group: '工具应用', name: 'CloudSaver', port: 8008, ssl: false, path: '', enabled: true },
], null, 2);

const FIELDS: PluginField[] = [
  { key: 'cloudflare_api_token', label: 'Cloudflare API Token', type: 'password', required: true, help: '需要 Pages:Edit 权限' },
  { key: 'cloudflare_account_id', label: 'Cloudflare Account ID', type: 'string', required: true },
  { key: 'project_name', label: 'CF Pages 项目名', type: 'string', default: 'nas', required: true },
  { key: 'iface', label: 'IPv6 网卡名', type: 'string', placeholder: 'eth0', help: '留空自动检测全局 IPv6' },
  { key: 'basic_auth_enabled', label: '启用 Basic Auth', type: 'boolean', default: true },
  { key: 'basic_auth_user', label: 'Basic Auth 用户名', type: 'string' },
  { key: 'basic_auth_pass', label: 'Basic Auth 密码', type: 'password' },
  { key: 'timeout_check', label: '环境检查超时(秒)', type: 'number', default: 120 },
  { key: 'timeout_deploy', label: '部署超时(秒)', type: 'number', default: 600 },
  { key: 'services_json', label: '服务列表 JSON', type: 'textarea', default: DEFAULT_SERVICES, help: '数组格式，字段支持 group/name/port/ssl/path/enabled 或直接 url' },
];

export default function CloudflarePages() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'cloudflare_pages');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`, null, { timeout: 600000 });
      setRunResult(res.data?.result);
    } catch {
      // message already handled by global interceptor and PluginConfigForm logs
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <PluginConfigForm
        slug="cloudflare_pages"
        title="Cloudflare Pages 发布"
        description="按 update_cloudflare.sh 逻辑：检测 IPv6 变化后生成控制面板并部署到 Cloudflare Pages。"
        fields={FIELDS}
        onRun={handleRun}
        running={running}
        runResult={runResult}
        resultRenderer={(r) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
      />
      <div style={{ marginTop: 16 }}>
        <LogViewer source="cloudflare_pages" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
