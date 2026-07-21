import { useState, useEffect } from 'react';
import { Row, Col, Card, Statistic, Progress, Table, Tag, Typography, Spin } from 'antd';
import { CloudServerOutlined, ThunderboltOutlined, CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import api from '../utils/api';

const { Title } = Typography;

interface SystemStats { cpu_percent: number; memory_percent: number; memory_used: number; memory_total: number; disk_percent: number; disk_used: number; disk_total: number; uptime_hours: number; }
interface RecentExecution { id: number; task_name: string; status: string; start_time: string; duration_ms: number | null; }

function fmtBytes(b: number) { if (!b) return '0 GB'; const gb = b / 1024 / 1024 / 1024; return `${gb.toFixed(1)} GB`; }

export default function Dashboard() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [recent, setRecent] = useState<RecentExecution[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.get('/system/stats'), api.get('/tasks/executions?limit=10')])
      .then(([s, e]) => { setStats(s.data); setRecent(e.data || []); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  const sc = (s: string) => ({ success: 'green', failed: 'red', running: 'blue' }[s] || 'default') as string;
  const si = (s: string) => ({ success: <CheckCircleOutlined />, failed: <CloseCircleOutlined />, running: <ClockCircleOutlined spin /> }[s] || <ClockCircleOutlined />);

  const cards = [
    { title: t('dashboard.cpu'), value: stats?.cpu_percent ?? 0, icon: <CloudServerOutlined />, color: '#667eea' },
    { title: t('dashboard.memory'), value: stats?.memory_percent ?? 0, icon: <ThunderboltOutlined />, color: '#34d399', detail: `${fmtBytes(stats?.memory_used || 0)} / ${fmtBytes(stats?.memory_total || 0)}` },
    { title: t('dashboard.disk'), value: stats?.disk_percent ?? 0, icon: null, color: '#f59e0b', detail: `${fmtBytes(stats?.disk_used || 0)} / ${fmtBytes(stats?.disk_total || 0)}` },
  ];

  return (
    <div>
      <Title level={4} style={{ marginBottom: 20 }}>{t('dashboard.title')}</Title>
      <Row gutter={[16, 16]}>
        {cards.map((c, i) => (
          <Col xs={24} sm={12} lg={8} key={i}>
            <Card hoverable>
              <Statistic title={c.title} value={c.value} suffix="%" prefix={c.icon} valueStyle={{ color: c.color }} />
              <Progress percent={Math.round(c.value)} strokeColor={c.color} size="small" showInfo={false} style={{ marginTop: 8 }} />
              {c.detail && <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>{c.detail}</div>}
            </Card>
          </Col>
        ))}
      </Row>
      <Card title={t('dashboard.recentTasks')} style={{ marginTop: 20 }}>
        <Table dataSource={recent} rowKey="id" size="small" pagination={false}
          columns={[
            { title: t('tasks.name'), dataIndex: 'task_name', ellipsis: true },
            { title: t('common.status'), dataIndex: 'status', width: 100, render: (s: string) => <Tag color={sc(s)} icon={si(s)}>{s}</Tag> },
            { title: t('tasks.duration'), dataIndex: 'duration_ms', width: 90, render: (ms: number|null) => ms ? `${(ms/1000).toFixed(1)}s` : '-' },
            { title: t('common.time'), dataIndex: 'start_time', width: 170, render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
          ]} />
      </Card>
    </div>
  );
}
