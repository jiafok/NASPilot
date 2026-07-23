import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import LogViewer from '../components/LogViewer';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';

const FIELDS: PluginField[] = [
  { key: 'api_token', label: 'Cloudflare API Token', type: 'password', required: true, help: '需要 DNS:Edit 权限的 API Token' },
  { key: 'iface', label: 'Network Interface', type: 'string', placeholder: 'eth0', help: '获取 IPv6 地址的网卡接口。留空则使用公网检测。' },
  { key: 'zones', label: 'Zones', type: 'array', placeholder: '{"zone_id":"xxx","records":["home.example.com"],"ip_type":"both","proxied":false}', help: 'JSON 数组，每项包含 zone_id、records、ip_type（ipv4/ipv6/both）、proxied' },
];

export default function CloudflareDDNS() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'cloudflare_ddns');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`, null, { timeout: 300000 });
      setRunResult(res.data?.result);
    } catch {}
    finally { setRunning(false); }
  };

  return (
    <>
      <PluginConfigForm
        slug="cloudflare_ddns"
        title="Cloudflare DDNS"
        description="Auto-update IPv4/IPv6 DNS records on Cloudflare. Web UI for update_cloudflare.sh."
        fields={FIELDS}
        onRun={handleRun}
        running={running}
        runResult={runResult}
        resultRenderer={(r) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
      />
      <div style={{ marginTop: 16 }}>
        <LogViewer source="plugin:cloudflare_ddns" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
