import { useState, useEffect, Component, type ReactNode } from 'react';
import { Row, Col, Card, Statistic, Progress, Table, Tag, Typography, Spin, Space, Divider } from 'antd';
import { CloudServerOutlined, ThunderboltOutlined, CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined, ReloadOutlined, BarChartOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import api from '../utils/api';
import ResourceMonitor from '../components/ResourceMonitor';

const { Title, Text } = Typography;

// Error boundary to prevent ResourceMonitor from blanking the whole page
class MonitorErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };
  static getDerivedStateFromError() { return { hasError: true }; }
  render() {
    if (this.state.hasError) return null;
    return this.props.children;
  }
}

interface SystemStats { cpu_percent: number; memory_percent: number; memory_used: number; memory_total: number; disk_percent: number; disk_used: number; disk_total: number; uptime_hours: number; }
interface RecentExecution { id: number; task_name: string; status: string; start_time: string; duration_ms: number | null; }
interface TaskSummary { task_name: string; total: number; success: number; failed: number; last_run: string | null; }
interface ToolStatus { slug: string; name: string; enabled: boolean; status: string; last_run: string | null; metrics: string; }

const TOOL_SLUGS = ['pt_rss', 'alist_upload', 'docker_backup', 'cloudflare_pages', 'cloudflare_ddns', 'log_cleanup'];

function parseSummary(raw: unknown): Record<string, any> {
  if (raw && typeof raw === 'object') return raw as Record<string, any>;
  if (typeof raw === 'string') {
    try { return JSON.parse(raw); } catch { return {}; }
  }
  return {};
}

function toolMetrics(slug: string, config: Record<string, any>, summary: Record<string, any>): string {
  if (slug === 'pt_rss') {
    const daily = (config?.state?.daily?.stats || {}) as Record<string, any>;
    const added = Number(daily.added ?? summary.added ?? 0);
    const deleted = Object.keys(daily)
      .filter((k) => k.startsWith('deleted'))
      .reduce((s, k) => s + Number(daily[k] || 0), 0);
    return `新增 ${added} / 删除 ${deleted}`;
  }
  if (slug === 'alist_upload') {
    return `上传 ${Number(summary.uploaded || 0)} / 删除 ${Number(summary.deleted || 0)} / 失败 ${Number(summary.failed || 0)}`;
  }
  if (slug === 'docker_backup') {
    return `应用 ${Number(summary.apps_count || 0)} / 文件 ${Number(summary.total_files || 0)}`;
  }
  if (slug === 'cloudflare_ddns') {
    return `更新 ${Number(summary.updated || 0)} / 未变更 ${Number(summary.unchanged || 0)}`;
  }
  if (slug === 'cloudflare_pages') {
    return `部署 ${summary.deployed ? '成功' : '失败'} / IPv6 ${summary.ipv6 || '-'}`;
  }
  if (slug === 'log_cleanup') {
    return `删除 ${Number(summary.deleted || 0)} / 截断 ${Number(summary.truncated || 0)}`;
  }
  return Object.keys(summary).length ? JSON.stringify(summary).slice(0, 80) : '-';
}

function fmtBytes(b: number) { if (!b) return '0 GB'; const gb = b / 1024 / 1024 / 1024; return `${gb.toFixed(1)} GB`; }
function fmtUptime(h: number) {
  if (h < 1) return '< 1h';
  if (h < 24) return `${h.toFixed(0)}h`;
  const d = Math.floor(h / 24);
  return `${d}d ${(h % 24).toFixed(0)}h`;
}

export default function Dashboard() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [recent, setRecent] = useState<RecentExecution[]>([]);
  const [summary, setSummary] = useState<TaskSummary[]>([]);
  const [toolStatus, setToolStatus] = useState<ToolStatus[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = () => {
    setLoading(true);
    Promise.all([
      api.get('/system/stats'),
      api.get('/tasks/executions?limit=50'),
      api.get('/plugins'),
    ])
      .then(async ([s, e, p]) => {
        setStats(s.data);
        const execs: RecentExecution[] = e.data || [];
        setRecent(execs.slice(0, 10));
        const grouped: Record<string, TaskSummary> = {};
        for (const ex of execs) {
          const name = ex.task_name || 'Unknown';
          if (!grouped[name]) grouped[name] = { task_name: name, total: 0, success: 0, failed: 0, last_run: null };
          grouped[name].total++;
          if (ex.status === 'success') grouped[name].success++;
          else if (ex.status === 'failed' || ex.status === 'timeout') grouped[name].failed++;
          if (!grouped[name].last_run) grouped[name].last_run = ex.start_time;
        }
        setSummary(Object.values(grouped));

        const plugins = ((p.data || []) as any[]).filter((x) => TOOL_SLUGS.includes(x.slug));
        const statusRows = await Promise.all(plugins.map(async (pl: any) => {
          try {
            const instRes = await api.get(`/plugins/${pl.id}/instances`);
            const inst = (instRes.data || [])[0];
            const run = inst?.config?.state?.run_history?.[0] || null;
            const summaryObj = parseSummary(run?.summary || {});
            return {
              slug: pl.slug,
              name: pl.name,
              enabled: !!inst?.enabled,
              status: run?.status || summaryObj.status || (inst?.enabled ? 'idle' : 'disabled'),
              last_run: run?.time || null,
              metrics: toolMetrics(pl.slug, inst?.config || {}, summaryObj),
            } as ToolStatus;
          } catch {
            return {
              slug: pl.slug,
              name: pl.name,
              enabled: false,
              status: 'unknown',
              last_run: null,
              metrics: '-',
            } as ToolStatus;
          }
        }));
        setToolStatus(statusRows);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchData(); }, []);

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  const sc = (s: string) => ({ success: 'green', failed: 'red', running: 'blue' }[s] || 'default') as string;
  const si = (s: string) => ({ success: <CheckCircleOutlined />, failed: <CloseCircleOutlined />, running: <ClockCircleOutlined spin /> }[s] || <ClockCircleOutlined />);

  const cards = [
    { title: t('dashboard.cpu'), value: stats?.cpu_percent ?? 0, icon: <CloudServerOutlined />, color: '#667eea' },
    { title: t('dashboard.memory'), value: stats?.memory_percent ?? 0, icon: <ThunderboltOutlined />, color: '#34d399', detail: `${fmtBytes(stats?.memory_used || 0)} / ${fmtBytes(stats?.memory_total || 0)}` },
    { title: t('dashboard.disk'), value: stats?.disk_percent ?? 0, icon: null, color: '#f59e0b', detail: `${fmtBytes(stats?.disk_used || 0)} / ${fmtBytes(stats?.disk_total || 0)}` },
    { title: t('dashboard.uptime'), value: fmtUptime(stats?.uptime_hours ?? 0), icon: <ClockCircleOutlined />, color: '#8b5cf6', detail: '', valueStyle: { fontSize: 22 } },
  ];

  const totalExecs = summary.reduce((s, t) => s + t.total, 0);
  const totalSuccess = summary.reduce((s, t) => s + t.success, 0);
  const totalFailed = summary.reduce((s, t) => s + t.failed, 0);

  return (
    <div>
      <Title level={4} style={{ marginBottom: 20 }}>{t('dashboard.title')}</Title>
      <Row gutter={[16, 16]}>
        {cards.map((c, i) => (
          <Col xs={24} sm={12} lg={6} key={i}>
            <Card hoverable>
              <Statistic title={c.title} value={c.value} suffix={c.valueStyle ? '' : '%'} prefix={c.icon} valueStyle={c.valueStyle || { color: c.color }} />
              {!c.valueStyle && <Progress percent={Math.round(typeof c.value === 'number' ? c.value : 0)} strokeColor={c.color} size="small" showInfo={false} style={{ marginTop: 8 }} />}
              {c.detail && <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>{c.detail}</div>}
            </Card>
          </Col>
        ))}
      </Row>

      {/* ── Resource Monitor ── */}
      <Divider />
      <MonitorErrorBoundary>
        <ResourceMonitor />
      </MonitorErrorBoundary>

      {/* ── Task Summary + Recent ── */}
      <Row gutter={[16, 16]} style={{ marginTop: 20 }}>
        <Col xs={24}>
          <Card title="工具任务状态">
            <Table
              dataSource={toolStatus}
              rowKey="slug"
              size="small"
              pagination={false}
              scroll={{ x: 700 }}
              columns={[
                { title: '工具', dataIndex: 'name', width: 180 },
                { title: t('common.status'), dataIndex: 'status', width: 120, render: (s: string, r: ToolStatus) => {
                  const color = s === 'ok' || s === 'success' ? 'green' : s === 'failed' || s === 'error' ? 'red' : 'default';
                  return <Tag color={color}>{r.enabled ? s : 'disabled'}</Tag>;
                } },
                { title: '指标', dataIndex: 'metrics', ellipsis: true },
                { title: t('tasks.lastRun'), dataIndex: 'last_run', width: 180, render: (v: string | null) => v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '-' },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card title={<Space><BarChartOutlined /> {t('dashboard.taskStats')}</Space>}
            extra={<Space size="small"><Text type="secondary">{t('dashboard.totalExecutions', { count: totalExecs })}</Text><Text style={{ color: '#52c41a' }}>{totalSuccess} {t('common.success')}</Text><Text style={{ color: '#ff4d4f' }}>{totalFailed} {t('common.failed')}</Text></Space>}>
            <Table dataSource={summary} rowKey="task_name" size="small" pagination={false} scroll={{ x: 700 }}
              columns={[
                { title: t('tasks.title'), dataIndex: 'task_name', ellipsis: true },
                { title: t('dashboard.totalRuns'), dataIndex: 'total', width: 70, align: 'center' as const },
                { title: t('common.success'), dataIndex: 'success', width: 60, align: 'center' as const, render: (v: number) => <Text style={{ color: '#52c41a' }}>{v}</Text> },
                { title: t('common.failed'), dataIndex: 'failed', width: 60, align: 'center' as const, render: (v: number) => v > 0 ? <Text style={{ color: '#ff4d4f' }}>{v}</Text> : <Text type="secondary">0</Text> },
                { title: t('dashboard.successRate'), key: 'rate', width: 80, align: 'center' as const, render: (_:any, r: TaskSummary) => {
                  const rate = r.total > 0 ? Math.round((r.success / r.total) * 100) : 0;
                  return <Text style={{ color: rate >= 80 ? '#52c41a' : rate >= 50 ? '#faad14' : '#ff4d4f' }}>{rate}%</Text>;
                }},
                { title: t('tasks.lastRun'), dataIndex: 'last_run', width: 160,
                  render: (v: string) => v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '-' },
              ]} />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title={t('dashboard.recentTasks')} extra={<ReloadOutlined onClick={fetchData} style={{ cursor: 'pointer' }} />}>
            <Table dataSource={recent} rowKey="id" size="small" pagination={false} scroll={{ x: 340 }}
              columns={[
                { title: t('tasks.name'), dataIndex: 'task_name', ellipsis: true, width: 120 },
                { title: t('common.status'), dataIndex: 'status', width: 80, render: (s: string) => <Tag color={sc(s)} icon={si(s)} style={{ margin: 0, fontSize: 11 }}>{s}</Tag> },
                { title: t('common.duration'), dataIndex: 'duration_ms', width: 60, render: (ms: number|null) => ms ? `${(ms/1000).toFixed(1)}s` : '-' },
              ]} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
