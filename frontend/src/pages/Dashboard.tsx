import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Col, List, Progress, Row, Space, Spin, Statistic, Table, Tag, Timeline, Typography } from 'antd';
import { CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined, DatabaseOutlined, DeleteOutlined, ExclamationCircleOutlined, FolderOpenOutlined, InfoCircleOutlined, ReloadOutlined, ThunderboltOutlined, ToolOutlined, WarningOutlined } from '@ant-design/icons';
import api from '../utils/api';

const { Title, Text } = Typography;

interface SystemStats {
  cpu_percent: number;
  memory_percent: number;
  memory_used: number;
  memory_total: number;
  disk_percent: number;
  disk_used: number;
  disk_total: number;
}

interface ObservabilityOverview {
  task: { success_24h: number; failed_24h: number; timeout_24h: number; running_now: number; pending_count: number };
  container: { running: number; stopped: number; error: number; abnormal_containers: string[] };
  file: { storage_usage_percent: number; uploaded_success_24h: number; uploaded_failed_24h: number; deleted_24h: number };
  application: { ok_24h: number; failed_24h: number; skipped_24h: number };
}

interface UnifiedEvent {
  execution_id: string;
  domain: string;
  source_name: string;
  status: string;
  event_type: string | null;
  started_at: string;
  duration_ms?: number | null;
  failure_reasons?: string[];
  counters?: { added: number; deleted: number; uploaded: number; skipped: number; failed: number; unchanged: number; pending: number };
}

interface TimelineEvent {
  id: string;
  timestamp: string;
  event_type: string;
  domain: string;
  source: string;
  summary: string;
  counters?: { added: number; deleted: number; uploaded: number; skipped: number; failed: number };
}

interface UnifiedFeed { items: UnifiedEvent[] }

function statusTag(status: string) {
  if (status === 'ok' || status === 'success') return <Tag color="green">正常</Tag>;
  if (status === 'running') return <Tag color="blue">运行中</Tag>;
  if (status === 'warning' || status === 'timeout') return <Tag color="orange">告警</Tag>;
  if (status === 'failed' || status === 'error') return <Tag color="red">失败</Tag>;
  return <Tag>{status}</Tag>;
}

function eventIcon(et: string | null) {
  if (!et) return <InfoCircleOutlined />;
  if (et.includes('failed') || et.includes('abnormal')) return <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
  if (et.includes('added') || et.includes('uploaded') || et.includes('succeeded')) return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
  if (et.includes('deleted')) return <DeleteOutlined style={{ color: '#faad14' }} />;
  if (et.includes('skipped')) return <WarningOutlined style={{ color: '#faad14' }} />;
  return <InfoCircleOutlined />;
}

function eventColor(et: string | null) {
  if (!et) return 'gray';
  if (et.includes('failed') || et.includes('abnormal')) return 'red';
  if (et.includes('succeeded') || et.includes('added') || et.includes('uploaded')) return 'green';
  if (et.includes('deleted')) return 'orange';
  if (et.includes('skipped')) return 'gold';
  return 'gray';
}

function fmtBytes(bytes: number) {
  if (!bytes || Number.isNaN(bytes)) return '0 GB';
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function fmtRelTime(ts: string) {
  const ms = Date.now() - new Date(ts).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return `${m}分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}小时前`;
  const d = Math.floor(h / 24);
  return `${d}天前`;
}

function renderCounterSuffix(c: { added?: number; deleted?: number; uploaded?: number; skipped?: number; failed?: number } | undefined) {
  if (!c) return null;
  const parts: string[] = [];
  if ((c.added ?? 0) > 0) parts.push(`+${c.added}`);
  if ((c.deleted ?? 0) > 0) parts.push(`-${c.deleted}`);
  if ((c.uploaded ?? 0) > 0) parts.push(`↑${c.uploaded}`);
  if ((c.skipped ?? 0) > 0) parts.push(`⏭${c.skipped}`);
  if ((c.failed ?? 0) > 0) parts.push(`✗${c.failed}`);
  return parts.length > 0 ? parts.join(' ') : null;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [overview, setOverview] = useState<ObservabilityOverview | null>(null);
  const [feed, setFeed] = useState<UnifiedFeed>({ items: [] });
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [dockerUnavailable, setDockerUnavailable] = useState(false);

  const load = async () => {
    setLoading(true);
    const [statsRes, overviewRes, feedRes, timelineRes] = await Promise.allSettled([
      api.get('/system/stats'),
      api.get('/observability/overview?hours=24'),
      api.get('/observability/executions/unified?hours=24&limit=100'),
      api.get('/observability/timeline?hours=24&limit=50'),
    ]);

    if (statsRes.status === 'fulfilled') setStats(statsRes.value.data);
    if (overviewRes.status === 'fulfilled') setOverview(overviewRes.value.data);
    if (feedRes.status === 'fulfilled') setFeed(feedRes.value.data || { items: [] });
    if (timelineRes.status === 'fulfilled') setTimeline((timelineRes.value.data as any)?.events || []);

    // Check Docker availability from overview
    const hasContainerData = overviewRes.status === 'fulfilled' && overviewRes.value.data?.container !== undefined;
    setDockerUnavailable(!hasContainerData);

    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  // ── Risk Queue: aggregate failures + warnings from timeline ──
  const riskQueue = useMemo(() => {
    return timeline
      .filter((e) =>
        e.event_type === 'task_failed' ||
        e.event_type === 'plugin_failed' ||
        e.event_type === 'container_abnormal' ||
        e.event_type === 'execution_failed')
      .slice(0, 10);
  }, [timeline]);

  // ── Recent failures from unified feed ──
  const recentFailures = useMemo(() => {
    return feed.items
      .filter((x) => x.status === 'failed' || x.status === 'error' || x.status === 'timeout')
      .slice(0, 8);
  }, [feed]);

  // ── Timeline display items (pre-computed to avoid JSX parse issues) ──
  const timelineItems = useMemo(() => {
    return timeline.slice(0, 15).map((e) => ({
      color: eventColor(e.event_type),
      dot: eventIcon(e.event_type),
      children: (
        <div>
          <Text strong style={{ marginRight: 8 }}>{e.source}</Text>
          <Tag color={eventColor(e.event_type)} style={{ marginRight: 8 }}>{e.event_type}</Tag>
          <Text type="secondary">{fmtRelTime(e.timestamp)}</Text>
          <br />
          <Text>{e.summary}</Text>
          {e.counters && (
            <Text type="secondary" style={{ marginLeft: 8, fontSize: 11 }}>
              {renderCounterSuffix(e.counters)}
            </Text>
          )}
        </div>
      ),
    }));
  }, [timeline]);

  const systemWarn = (stats?.cpu_percent || 0) >= 90 || (stats?.memory_percent || 0) >= 90 || (stats?.disk_percent || 0) >= 90;
  const taskWarn = (overview?.task.failed_24h || 0) > 0 || (overview?.task.timeout_24h || 0) > 0;
  const containerWarn = dockerUnavailable || (overview?.container.error || 0) > 0;
  const storageWarn = (overview?.file.storage_usage_percent || stats?.disk_percent || 0) >= 85;

  const systemLevel = systemWarn ? 'warning' : 'success';
  const taskLevel = taskWarn ? 'warning' : 'success';
  const containerLevel = (dockerUnavailable || containerWarn) ? 'warning' : 'success';
  const storageLevel = storageWarn ? 'warning' : 'success';

  const riskHints: string[] = [];
  if (riskQueue.length > 0) riskHints.push(`⚠️ 风险队列有 ${riskQueue.length} 项待处理：${riskQueue.slice(0, 3).map((r) => r.source).join('、')}`);
  if (dockerUnavailable) riskHints.push('Docker API 不可用，容器监控已中断');
  if (storageWarn) riskHints.push(`磁盘使用率 ${Math.round(overview?.file.storage_usage_percent || stats?.disk_percent || 0)}%，建议释放空间`);
  if (riskHints.length === 0) riskHints.push('✅ 当前无高风险项，系统运行正常');

  const totalRisks = riskQueue.length + (dockerUnavailable ? 1 : 0) + (storageWarn ? 1 : 0);

  if (loading) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  }

  return (
    <div>
      <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>📊 Operations Center</Title>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>

      {/* ── 1. Risk Queue ── */}
      <Alert
        type={totalRisks > 0 ? 'warning' : 'success'}
        showIcon
        message={`Risk Queue · ${totalRisks} 项`}
        description={
          <Space direction="vertical" size={2}>
            {riskHints.map((item, idx) => <Text key={idx}>{item}</Text>)}
          </Space>
        }
        style={{ marginBottom: 16 }}
      />

      {/* ── 2-5. Health Cards: System / Container / Task / File ── */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card title="🖥 System Health" extra={statusTag(systemLevel)} size="small">
            <Statistic title="CPU" value={stats?.cpu_percent || 0} suffix="%" valueStyle={{ fontSize: 20 }} />
            <Progress percent={Math.round(stats?.cpu_percent || 0)} size="small" status={systemWarn ? 'exception' : 'normal'} />
            <Text type="secondary">Memory: {fmtBytes(stats?.memory_used || 0)} / {fmtBytes(stats?.memory_total || 0)}</Text>
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card title="🐳 Container Health" extra={statusTag(containerLevel)} size="small">
            <Statistic title="运行中 / 停止 / 异常" value={`${overview?.container.running || 0} / ${overview?.container.stopped || 0} / ${overview?.container.error || 0}`} valueStyle={{ fontSize: 18 }} />
            {dockerUnavailable && <Text type="danger">Docker 不可用</Text>}
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card title="⚡ Task Health" extra={statusTag(taskLevel)} size="small">
            <Statistic title="24h 成功 / 失败" value={`${overview?.task.success_24h || 0} / ${overview?.task.failed_24h || 0}`} valueStyle={{ fontSize: 20 }} />
            <Text type="secondary">待执行: {overview?.task.pending_count || 0} | 超时: {overview?.task.timeout_24h || 0}</Text>
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card title="📁 File Health" extra={statusTag(storageLevel)} size="small">
            <Statistic title="磁盘占用" value={overview?.file.storage_usage_percent || stats?.disk_percent || 0} suffix="%" valueStyle={{ fontSize: 20 }} />
            <Progress percent={Math.round(overview?.file.storage_usage_percent || stats?.disk_percent || 0)} size="small" status={storageWarn ? 'exception' : 'normal'} />
            <Text type="secondary">24h 上传: {overview?.file.uploaded_success_24h || 0} | 删除: {overview?.file.deleted_24h || 0}</Text>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        {/* ── 6. Activity Timeline ── */}
        <Col xs={24} xl={14}>
          <Card title="📋 Recent Activity Timeline" extra={<Tag color="blue">Phase 3</Tag>} style={{ marginBottom: 16 }}>
            <Timeline
              items={timelineItems as any}
            />
            {timeline.length === 0 && <Text type="secondary">暂无活动事件</Text>}
          </Card>
        </Col>

        {/* ── 7. Risk Queue + Recent Failures ── */}
        <Col xs={24} xl={10}>
          <Card title="⚠️ Risk Queue" size="small" style={{ marginBottom: 16 }}>
            {riskQueue.length > 0 ? (
              <List
                size="small"
                dataSource={riskQueue}
                renderItem={(e) => (
                  <List.Item>
                    <Space>
                      {eventIcon(e.event_type)}
                      <div>
                        <Text>{e.source}</Text>
                        <br />
                        <Text type="secondary" style={{ fontSize: 11 }}>{e.summary} · {fmtRelTime(e.timestamp)}</Text>
                      </div>
                    </Space>
                  </List.Item>
                )}
              />
            ) : (
              <Text type="secondary">✅ 风险队列为空</Text>
            )}
          </Card>

          <Card title="❌ Recent Failures" size="small" style={{ marginBottom: 16 }}>
            <Table
              rowKey="execution_id"
              size="small"
              pagination={false}
              dataSource={recentFailures}
              locale={{ emptyText: '无近期失败记录' }}
              columns={[
                { title: '来源', dataIndex: 'source_name', ellipsis: true, width: 120 },
                { title: '域', dataIndex: 'domain', width: 80 },
                { title: '状态', dataIndex: 'status', width: 70, render: (v: string) => statusTag(v) },
                {
                  title: '原因', key: 'reason', ellipsis: true,
                  render: (_: unknown, r: UnifiedEvent) => r.failure_reasons?.[0] || '-',
                },
              ]}
            />
          </Card>

          {/* ── 8. Next Actions ── */}
          <Card title="🎯 Next Actions" size="small">
            <Space direction="vertical" style={{ width: '100%' }}>
              {riskQueue.length > 0 && (
                <Button block icon={<ExclamationCircleOutlined />} type="primary" danger onClick={() => navigate('/logs')}>
                  查看风险日志
                </Button>
              )}
              <Button block icon={<ThunderboltOutlined />} onClick={() => navigate('/automation')}>进入任务中心</Button>
              <Button block icon={<DatabaseOutlined />} onClick={() => navigate('/containers')}>查看容器状态</Button>
              <Button block icon={<ToolOutlined />} onClick={() => navigate('/applications/log-cleanup')}>执行日志清理</Button>
              <Button block icon={<FolderOpenOutlined />} onClick={() => navigate('/files')}>浏览文件系统</Button>
              <Button block icon={<ClockCircleOutlined />} onClick={load}>立即刷新健康状态</Button>
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
