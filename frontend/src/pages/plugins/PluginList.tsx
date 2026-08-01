import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Tag, Button, Space, Typography, Row, Col, Spin, Statistic, Tooltip } from 'antd';
import { ReloadOutlined, PlayCircleOutlined, SettingOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import api from '../../utils/api';

const { Title, Text } = Typography;

interface Plugin {
  id: number;
  slug: string;
  name: string;
  description: string;
  version: string;
  author: string;
  category: string;
  enabled: boolean;
  instance_count: number;
}

interface ToolStatus {
  slug: string;
  name: string;
  enabled: boolean;
  status: string;
  lastRun: string | null;
  successCount: number;
  failedCount: number;
  summary: string;
}

const TOOL_PAGE_MAP: Record<string, string> = {
  pt_rss: '/applications/pt-rss',
  alist_upload: '/applications/alist-upload',
  cloudflare_ddns: '/applications/cloudflare-ddns',
  cloudflare_pages: '/applications/cloudflare-pages',
  docker_backup: '/applications/docker-backup',
  log_cleanup: '/applications/log-cleanup',
  btrfs_cleanup: '/applications/btrfs-cleanup',
  rclone_mount: '/applications/rclone-mount',
};

const ICONS: Record<string, string> = {
  pt_rss: '📥', alist_upload: '📤', cloudflare_ddns: '🌐',
  docker_backup: '💾', log_cleanup: '🧹', btrfs_cleanup: '🗑️',
  rclone_mount: '📁', cloudflare_pages: '🏠',
};

function statusColor(s: string) {
  if (s === 'ok' || s === 'success') return 'green';
  if (s === 'running') return 'blue';
  if (s === 'failed' || s === 'error') return 'red';
  if (s === 'warning' || s === 'timeout') return 'orange';
  if (s === 'disabled') return 'default';
  if (s === 'idle') return 'cyan';
  return 'default';
}

function parseSummary(raw: unknown): Record<string, any> {
  if (!raw) return {};
  if (typeof raw === 'object') return raw as Record<string, any>;
  if (typeof raw === 'string') {
    try { const p = JSON.parse(raw); return typeof p === 'object' ? p : {}; } catch { return {}; }
  }
  return {};
}

export default function IntegrationToolsList() {
  const navigate = useNavigate();
  const [toolStatuses, setToolStatuses] = useState<ToolStatus[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const pluginsRes = await api.get('/plugins');
      const plugins: Plugin[] = pluginsRes.data || [];
      const rows: ToolStatus[] = await Promise.all(
        plugins.map(async (p) => {
          try {
            const instRes = await api.get(`/plugins/${p.id}/instances`);
            const inst = (instRes.data || [])[0];
            const history = Array.isArray(inst?.config?.state?.run_history)
              ? inst.config.state.run_history : [];
            const latest = history[0] || null;
            const summaryObj = parseSummary(latest?.summary);
            const successCount = history.filter((x: any) =>
              ['ok', 'success'].includes(String(x?.status || '').toLowerCase())).length;
            const failedCount = history.filter((x: any) =>
              ['failed', 'error', 'timeout'].includes(String(x?.status || '').toLowerCase())).length;
            let summary = '-';
            if (latest) {
              const parts: string[] = [];
              const s = summaryObj;
              if (s.added > 0) parts.push(`+${s.added}`);
              if (s.uploaded > 0) parts.push(`↑${s.uploaded}`);
              if (s.deleted > 0) parts.push(`-${s.deleted}`);
              if (s.failed > 0) parts.push(`✗${s.failed}`);
              if (s.skipped > 0) parts.push(`⏭${s.skipped}`);
              summary = parts.length > 0 ? parts.join(' ') : s.status || 'ok';
            }
            return {
              slug: p.slug,
              name: p.name,
              enabled: inst ? inst.enabled : false,
              status: latest?.status || (inst?.enabled ? 'idle' : 'disabled'),
              lastRun: latest?.time || null,
              successCount,
              failedCount,
              summary,
            };
          } catch {
            return { slug: p.slug, name: p.name, enabled: p.enabled, status: 'disabled', lastRun: null, successCount: 0, failedCount: 0, summary: '-' };
          }
        })
      );
      setToolStatuses(rows);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleRun = async (slug: string) => {
    try {
      const pluginsRes = await api.get('/plugins');
      const p = (pluginsRes.data as any[]).find((x: any) => x.slug === slug);
      if (!p) return;
      await api.post(`/plugins/${p.id}/run`, null, { timeout: 300000 });
      await fetchAll();
    } catch { /* ignore */ }
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  const runningCount = toolStatuses.filter((t) => t.enabled).length;
  const okCount = toolStatuses.filter((t) => ['ok', 'success', 'idle'].includes(t.status)).length;
  const failCount = toolStatuses.filter((t) => ['failed', 'error'].includes(t.status)).length;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <Title level={4} style={{ margin: 0 }}>📦 应用控制中心</Title>
        <Button icon={<ReloadOutlined />} onClick={fetchAll}>刷新</Button>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        <Col xs={8} sm={4}><Card size="small"><Statistic title="总计" value={toolStatuses.length} valueStyle={{ fontSize: 22 }} /></Card></Col>
        <Col xs={8} sm={4}><Card size="small"><Statistic title="已启用" value={runningCount} valueStyle={{ fontSize: 22, color: '#52c41a' }} /></Card></Col>
        <Col xs={8} sm={4}><Card size="small"><Statistic title="正常" value={okCount} valueStyle={{ fontSize: 22, color: '#1677ff' }} /></Card></Col>
        <Col xs={8} sm={4}><Card size="small"><Statistic title="异常" value={failCount} valueStyle={{ fontSize: 22, color: failCount > 0 ? '#ff4d4f' : '#999' }} /></Card></Col>
      </Row>

      <Row gutter={[16, 16]}>
        {toolStatuses.map((t) => (
          <Col xs={24} sm={12} lg={6} key={t.slug}>
            <Card
              hoverable
              size="small"
              onClick={() => { const route = TOOL_PAGE_MAP[t.slug]; if (route) navigate(route); }}
              title={
                <Space>
                  <span style={{ fontSize: 18 }}>{ICONS[t.slug] || '🔌'}</span>
                  <span style={{ fontSize: 14 }}>{t.name}</span>
                </Space>
              }
              extra={<Tag color={statusColor(t.status)}>{t.status}</Tag>}
              style={{ height: '100%' }}
            >
              <Space direction="vertical" style={{ width: '100%' }} size={6}>
                <div>
                  <Text type="secondary" style={{ fontSize: 11 }}>最后执行</Text><br />
                  <Text style={{ fontSize: 12 }}>
                    {t.lastRun ? new Date(t.lastRun).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '从未'}
                  </Text>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 11 }}>最近结果</Text><br />
                  <Text ellipsis style={{ fontSize: 12, fontFamily: 'monospace' }}>{t.summary}</Text>
                </div>
                <div style={{ display: 'flex', gap: 12 }}>
                  <span><CheckCircleOutlined style={{ color: '#52c41a', fontSize: 11 }} /> <Text style={{ fontSize: 12 }}>{t.successCount}</Text></span>
                  <span><CloseCircleOutlined style={{ color: '#ff4d4f', fontSize: 11 }} /> <Text style={{ fontSize: 12 }}>{t.failedCount}</Text></span>
                </div>
                <Space size={4} wrap>
                  <Tooltip title="配置"><Button size="small" icon={<SettingOutlined />} onClick={(e) => { e.stopPropagation(); const route = TOOL_PAGE_MAP[t.slug]; if (route) navigate(route); }} /></Tooltip>
                  <Tooltip title="立即运行"><Button size="small" type="primary" icon={<PlayCircleOutlined />} disabled={!t.enabled} onClick={(e) => { e.stopPropagation(); handleRun(t.slug); }} /></Tooltip>
                </Space>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
}
