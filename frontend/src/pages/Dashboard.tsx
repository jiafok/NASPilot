import { useState, useEffect } from 'react';
import { Row, Col, Card, Statistic, Progress, Table, Tag, Typography, Spin } from 'antd';
import {
  CloudServerOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import api from '../utils/api';

const { Title } = Typography;

interface SystemStats {
  cpu_percent: number;
  memory_percent: number;
  memory_used_gb: number;
  memory_total_gb: number;
  disk_percent: number;
  disk_used_gb: number;
  disk_total_gb: number;
  uptime_hours: number;
}

interface RecentExecution {
  id: number;
  task_name: string;
  status: string;
  start_time: string;
  duration_ms: number | null;
}

export default function Dashboard() {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [recent, setRecent] = useState<RecentExecution[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get('/system/stats'),
      api.get('/tasks/executions?limit=10'),
    ])
      .then(([statsRes, execRes]) => {
        setStats(statsRes.data);
        setRecent(execRes.data || []);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  const statusColor = (s: string) => {
    switch (s) {
      case 'success': return 'green';
      case 'failed': return 'red';
      case 'running': return 'blue';
      default: return 'default';
    }
  };

  const statusIcon = (s: string) => {
    switch (s) {
      case 'success': return <CheckCircleOutlined />;
      case 'failed': return <CloseCircleOutlined />;
      case 'running': return <ClockCircleOutlined spin />;
      default: return <ClockCircleOutlined />;
    }
  };

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>系统概览</Title>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="CPU 使用率"
              value={stats?.cpu_percent ?? 0}
              suffix="%"
              prefix={<CloudServerOutlined />}
            />
            <Progress percent={Math.round(stats?.cpu_percent ?? 0)} size="small" showInfo={false} style={{ marginTop: 8 }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="内存使用率"
              value={stats?.memory_percent ?? 0}
              suffix="%"
              prefix={<ThunderboltOutlined />}
              precision={1}
            />
            <Progress percent={Math.round(stats?.memory_percent ?? 0)} size="small" showInfo={false} style={{ marginTop: 8 }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="磁盘使用率"
              value={stats?.disk_percent ?? 0}
              suffix="%"
              precision={1}
            />
            <Progress percent={Math.round(stats?.disk_percent ?? 0)} size="small" showInfo={false} style={{ marginTop: 8 }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="运行时间"
              value={stats?.uptime_hours ?? 0}
              suffix="小时"
              precision={1}
            />
          </Card>
        </Col>
      </Row>

      <Title level={5} style={{ marginTop: 32, marginBottom: 16 }}>最近执行记录</Title>
      <Table
        dataSource={recent}
        rowKey="id"
        size="small"
        pagination={false}
        columns={[
          { title: '任务', dataIndex: 'task_name', key: 'task_name', ellipsis: true },
          {
            title: '状态', dataIndex: 'status', key: 'status', width: 100,
            render: (s: string) => (
              <Tag color={statusColor(s)} icon={statusIcon(s)}>{s}</Tag>
            ),
          },
          {
            title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 100,
            render: (ms: number | null) => ms ? `${(ms / 1000).toFixed(1)}s` : '-',
          },
          {
            title: '时间', dataIndex: 'start_time', key: 'start_time', width: 180,
            render: (t: string) => t ? new Date(t).toLocaleString() : '-',
          },
        ]}
      />
    </div>
  );
}
