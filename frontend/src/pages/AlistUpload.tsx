import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import LogViewer from '../components/LogViewer';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';

const FIELDS: PluginField[] = [
  { key: 'alist_url', label: 'AList URL', type: 'string', placeholder: 'https://alist.example.com', required: true, help: 'AList 服务地址' },
  { key: 'username', label: 'Username', type: 'string', default: 'admin' },
  { key: 'password', label: 'Password', type: 'password' },
  { key: 'scan_dirs', label: 'Scan Directories', type: 'array', placeholder: '/volume1/upload, /volume1/media', help: '逗号分隔的本地扫描目录' },
  { key: 'remote_root', label: 'Remote Root', type: 'string', default: '/', help: 'AList 上的目标根目录' },
  { key: 'extensions', label: 'File Extensions', type: 'array', placeholder: 'mkv, mp4, iso, zip', help: '逗号分隔，只扫描这些扩展名' },
  { key: 'max_retries', label: 'Max Retries', type: 'number', default: 3 },
  { key: 'delete_after_upload', label: 'Delete After Upload', type: 'boolean', default: false, help: '上传成功后删除本地文件' },
  { key: 'connect_timeout', label: 'Connect Timeout (s)', type: 'number', default: 10 },
  { key: 'read_timeout', label: 'Read Timeout (s)', type: 'number', default: 120 },
];

export default function AlistUpload() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'alist_upload');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`);
      setRunResult(res.data?.result);
    } catch {}
    finally { setRunning(false); }
  };

  return (
    <>
      <PluginConfigForm
        slug="alist_upload"
        title="AList Auto Upload"
        description="Scan local directories and auto-upload files to AList with verification and retry. Web UI for alist_upload.py."
        fields={FIELDS}
        onRun={handleRun}
        running={running}
        runResult={runResult}
        resultRenderer={(r) => <pre style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</pre>}
      />
      <div style={{ marginTop: 16 }}>
        <LogViewer source="plugin:alist_upload" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
