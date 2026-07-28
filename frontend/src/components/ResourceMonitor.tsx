import { useState, useEffect, useRef, useCallback } from 'react';
import { Card, Col, Row, Table, Typography, Progress, Statistic, Space, Segmented, Spin } from 'antd';
import { ThunderboltOutlined, CloudServerOutlined, GlobalOutlined, HddOutlined, PauseCircleOutlined, CaretRightOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, TitleComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import api from '../utils/api';

echarts.use([LineChart, GridComponent, TooltipComponent, TitleComponent, LegendComponent, CanvasRenderer]);

const { Text } = Typography;

interface MetricPoint {
  ts: number;
  cpu_percent: number;
  mem_percent: number;
  mem_used_mb: number;
  net_recv_kbps: number;
  net_sent_kbps: number;
  disk_read_kbps: number;
  disk_write_kbps: number;
}

interface PartInfo {
  device: string;
  mount: string;
  size_gb: number;
  used_gb: number;
  avail_gb: number;
  percent: number;
}

interface CurrentMetrics {
  cpu_percent: number;
  mem_percent: number;
  mem_used_mb: number;
  net_recv_kbps: number;
  net_sent_kbps: number;
  disk_read_kbps: number;
  disk_write_kbps: number;
  partitions: PartInfo[];
}

const CHART_HEIGHT = 200;
const COLORS = {
  cpu: '#667eea',
  memory: '#34d399',
  netIn: '#3b82f6',
  netOut: '#f59e0b',
  diskR: '#a78bfa',
  diskW: '#f97316',
};

function timeTicks(tsArr: number[]) {
  if (!tsArr.length) return [];
  const step = Math.max(1, Math.floor(tsArr.length / 8));
  const out: string[] = [];
  for (let i = 0; i < tsArr.length; i++) {
    if (i % step === 0) {
      out.push(new Date(tsArr[i] * 1000).toLocaleTimeString('zh-CN', { hour12: false }));
    } else {
      out.push('');
    }
  }
  return out;
}

function makeLineOpt(_title: string, seriesData: { name: string; color: string; data: number[] }[], timestamps: number[]) {
  return {
    grid: { top: 10, right: 15, bottom: 25, left: 45 },
    tooltip: { trigger: 'axis' as const, textStyle: { fontSize: 11 } },
    legend: { show: true, bottom: 0, textStyle: { fontSize: 10 } },
    xAxis: { type: 'category' as const, data: timeTicks(timestamps), axisLabel: { fontSize: 9, rotate: 0 }, boundaryGap: false },
    yAxis: { type: 'value' as const, axisLabel: { fontSize: 9 }, splitLine: { lineStyle: { color: '#e8e8e8' } } },
    series: seriesData.map(s => ({
      name: s.name, type: 'line', data: s.data,
      smooth: true, symbol: 'none',
      lineStyle: { color: s.color, width: 1.5 },
    })),
  };
}

export default function ResourceMonitor() {
  const { t } = useTranslation();
  const [history, setHistory] = useState<MetricPoint[]>([]);
  const [current, setCurrent] = useState<CurrentMetrics | null>(null);
  const [paused, setPaused] = useState(false);
  const [range, setRange] = useState<number>(120);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pausedRef = useRef(false);

  const fetchData = useCallback(async (count: number, isInitial: boolean) => {
    if (pausedRef.current && !isInitial) return;
    try {
      const [hRes, cRes] = await Promise.all([
        api.get('/system/metrics/history', { params: { count } }),
        api.get('/system/metrics/current'),
      ]);
      setHistory(hRes.data || []);
      setCurrent(cRes.data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    fetchData(range, true);
    timerRef.current = setInterval(() => fetchData(range, false), 3000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [range, fetchData]);

  const timestamps = history.map(h => h.ts);
  const cpuData = history.map(h => h.cpu_percent);
  const memData = history.map(h => h.mem_percent);
  const netRxData = history.map(h => h.net_recv_kbps);
  const netTxData = history.map(h => h.net_sent_kbps);
  const diskRData = history.map(h => h.disk_read_kbps);
  const diskWData = history.map(h => h.disk_write_kbps);

  const formatKBps = (kbps: number) => {
    if (kbps < 1024) return `${kbps.toFixed(1)} KB/s`;
    return `${(kbps / 1024).toFixed(1)} MB/s`;
  };

  const cpuChart = history.length > 1 ? (
    <ReactEChartsCore echarts={echarts} option={makeLineOpt('CPU', [
      { name: 'CPU %', color: COLORS.cpu, data: cpuData },
    ], timestamps)} style={{ height: CHART_HEIGHT }} notMerge />
  ) : <Spin />;

  const memChart = history.length > 1 ? (
    <ReactEChartsCore echarts={echarts} option={makeLineOpt('Memory', [
      { name: 'Mem %', color: COLORS.memory, data: memData },
    ], timestamps)} style={{ height: CHART_HEIGHT }} notMerge />
  ) : <Spin />;

  const netChart = history.length > 1 ? (
    <ReactEChartsCore echarts={echarts} option={makeLineOpt('Network', [
      { name: 'Download', color: COLORS.netIn, data: netRxData },
      { name: 'Upload', color: COLORS.netOut, data: netTxData },
    ], timestamps)} style={{ height: CHART_HEIGHT }} notMerge />
  ) : <Spin />;

  const diskChart = history.length > 1 ? (
    <ReactEChartsCore echarts={echarts} option={makeLineOpt('Disk IO', [
      { name: 'Read', color: COLORS.diskR, data: diskRData },
      { name: 'Write', color: COLORS.diskW, data: diskWData },
    ], timestamps)} style={{ height: CHART_HEIGHT }} notMerge />
  ) : <Spin />;

  const partCols = [
    { title: t('system.mount'), dataIndex: 'mount', ellipsis: true },
    { title: t('system.device'), dataIndex: 'device', width: 60, ellipsis: true },
    { title: t('system.size'), dataIndex: 'size_gb', width: 70, render: (v: number) => `${v} GB` },
    { title: t('system.used'), dataIndex: 'used_gb', width: 70, render: (v: number) => `${v} GB` },
    { title: t('system.available'), dataIndex: 'avail_gb', width: 70, render: (v: number) => `${v} GB` },
    {
      title: t('system.usage'), dataIndex: 'percent', width: 100,
      render: (v: number) => <Progress percent={Math.round(v)} size="small" strokeColor={v > 90 ? '#ff4d4f' : v > 70 ? '#faad14' : '#52c41a'} />,
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <Space>
          <Text strong style={{ fontSize: 16 }}>📊 {t('system.resourceMonitor')}</Text>
          <Segmented
            size="small"
            value={range}
            onChange={v => setRange(v as number)}
            options={[
              { label: '2m', value: 120 },
              { label: '5m', value: 300 },
              { label: '10m', value: 600 },
            ]}
          />
          {paused
            ? <CaretRightOutlined onClick={() => setPaused(false)} style={{ cursor: 'pointer', color: '#52c41a' }} />
            : <PauseCircleOutlined onClick={() => setPaused(true)} style={{ cursor: 'pointer', color: '#faad14' }} />}
        </Space>
        <Space size={16}>
          <Statistic title="CPU" value={current?.cpu_percent ?? 0} suffix="%" valueStyle={{ fontSize: 18, color: COLORS.cpu }} prefix={<CloudServerOutlined />} />
          <Statistic title="Mem" value={current?.mem_percent ?? 0} suffix="%" valueStyle={{ fontSize: 18, color: COLORS.memory }} prefix={<ThunderboltOutlined />} />
          <Statistic title="Net ↓" value={formatKBps(current?.net_recv_kbps ?? 0)} valueStyle={{ fontSize: 14, color: COLORS.netIn }} />
          <Statistic title="Net ↑" value={formatKBps(current?.net_sent_kbps ?? 0)} valueStyle={{ fontSize: 14, color: COLORS.netOut }} />
          <Statistic title="Disk R" value={formatKBps(current?.disk_read_kbps ?? 0)} valueStyle={{ fontSize: 14, color: COLORS.diskR }} />
          <Statistic title="Disk W" value={formatKBps(current?.disk_write_kbps ?? 0)} valueStyle={{ fontSize: 14, color: COLORS.diskW }} />
        </Space>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card size="small" title={<><CloudServerOutlined /> CPU</>}>
            {cpuChart}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title={<><ThunderboltOutlined /> {t('dashboard.memory')}</>}>
            {memChart}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title={<><GlobalOutlined /> Network</>}>
            {netChart}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title={<><HddOutlined /> Disk IO</>}>
            {diskChart}
          </Card>
        </Col>
      </Row>

      {/* Disk Partitions */}
      <Card size="small" title={<><HddOutlined /> {t('system.diskPartitions')}</>} style={{ marginTop: 16 }}>
        <div style={{ overflowX: 'auto' }}>
          <Table
            dataSource={current?.partitions || []}
            columns={partCols}
            rowKey="mount"
            size="small"
            pagination={false}
            scroll={{ x: 900 }}
            locale={{ emptyText: t('common.noData') }}
          />
        </div>
      </Card>
    </div>
  );
}
