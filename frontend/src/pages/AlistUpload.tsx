import { useState } from 'react';
import PluginConfigForm from '../components/PluginConfigForm';
import LogViewer from '../components/LogViewer';
import type { PluginField } from '../components/PluginConfigForm';
import api from '../utils/api';

const FIELDS: PluginField[] = [
  { key: 'alist_url', label: 'AList 地址', type: 'string', placeholder: 'https://alist.example.com', required: true, help: 'AList 服务地址' },
  { key: 'username', label: '用户名', type: 'string', default: 'admin' },
  { key: 'password', label: '密码', type: 'password' },
  { key: 'scan_dirs', label: '扫描目录', type: 'array', placeholder: '/volume1/upload, /volume1/media', help: '逗号分隔的本地扫描目录' },
  { key: 'remote_root', label: '远程根路径', type: 'string', default: '/', help: 'AList 上的目标根目录' },
  { key: 'extensions', label: '文件扩展名', type: 'array', placeholder: 'mkv, mp4, iso, zip', help: '逗号分隔，只扫描这些扩展名（空=全部）' },
  { key: 'max_retries', label: '最大重试次数', type: 'number', default: 3 },
  { key: 'delete_after_upload', label: '上传后删除', type: 'boolean', default: false, help: '上传成功后删除本地文件' },
  { key: 'max_file_size_gb', label: '文件大小上限(GB)', type: 'number', default: 0, help: '超过此大小的文件跳过，0=不限' },
  { key: 'min_free_space_gb', label: '最小剩余空间(GB)', type: 'number', default: 0, help: '远程剩余空间低于此值时跳过，0=不检查' },
  { key: 'connect_timeout', label: '连接超时(秒)', type: 'number', default: 10 },
  { key: 'read_timeout', label: '读取超时(秒)', type: 'number', default: 120 },
  { key: 'verify_max_workers', label: '并发上传数', type: 'number', default: 4, help: '同时上传的文件数，默认4' },
];

export default function AlistUpload() {
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === 'alist_upload');
      if (!p) return;
      const res = await api.post(`/plugins/${p.id}/run`, null, { timeout: 300000 });
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
        <LogViewer source="alist_upload" maxHeight={300} placeholder="等待运行..." collapsible defaultOpen={false} label="运行日志" />
      </div>
    </>
  );
}
