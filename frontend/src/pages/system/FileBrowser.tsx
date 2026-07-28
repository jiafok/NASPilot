import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Table, Button, Breadcrumb, Typography, Spin, Modal, Space, message } from 'antd';
import { FolderOutlined, FileOutlined, HomeOutlined, ReloadOutlined, DownloadOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import api from '../../utils/api';
import { getToken } from '../../utils/auth';

const { Title, Text } = Typography;

interface DirEntry {
  name: string;
  is_dir: boolean;
  size: number;
  mtime: number;
  is_text: boolean;
  real_path: string;
  is_root?: boolean;
}

export default function FileBrowser() {
  const { t } = useTranslation();
  const [path, setPath] = useState('/');
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // File viewer
  const [viewFile, setViewFile] = useState<string | null>(null);
  const [viewFilePath, setViewFilePath] = useState<string>('');
  const [viewContent, setViewContent] = useState('');
  const [viewLoading, setViewLoading] = useState(false);

  const fetchDir = useCallback(async (dirPath: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get('/files/list', { params: { path: dirPath } });
      setPath(res.data.path || dirPath);
      setEntries(res.data.entries || []);
      if (res.data.error) setError(res.data.error);
    } catch {
      setError('Failed to load directory');
    }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchDir(path); }, [path, fetchDir]);

  const navigateTo = (entry: DirEntry) => {
    setPath(entry.real_path);
  };

  const goUp = () => {
    if (path === '/') return;
    const parts = path.split('/').filter(Boolean);
    if (parts.length <= 1) {
      setPath('/');
    } else {
      setPath('/' + parts.slice(0, -1).join('/'));
    }
  };

  // Build breadcrumbs from the real path
  const breadcrumbParts = path === '/' ? [] : path.split('/').filter(Boolean);

  const formatSize = (size: number) => {
    if (size === 0) return '-';
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  const openFile = async (entry: DirEntry) => {
    if (!entry.is_text) {
      // Download non-text files
      const token = getToken();
      window.open(`/api/v1/files/download?path=${encodeURIComponent(entry.real_path)}&token=${token}`, '_blank');
      return;
    }
    setViewFile(entry.name);
    setViewFilePath(entry.real_path);
    setViewLoading(true);
    try {
      const res = await api.get('/files/read', { params: { path: entry.real_path, limit: 50000 } });
      setViewContent(res.data);
    } catch {
      message.error('Cannot read file');
    }
    finally { setViewLoading(false); }
  };

  const columns = [
    {
      title: '名称', dataIndex: 'name', ellipsis: true,
      render: (name: string, record: DirEntry) => (
        record.is_dir
          ? <Button type="link" icon={<FolderOutlined />} onClick={() => navigateTo(record)} style={{ padding: 0, color: record.is_root ? '#1890ff' : '#faad14' }}>{name}</Button>
          : <Button type="link" icon={<FileOutlined />} onClick={() => openFile(record)} style={{ padding: 0 }}>{name}</Button>
      ),
    },
    {
      title: '大小', dataIndex: 'size', width: 100, responsive: ['md' as const],
      render: (size: number) => <Text type="secondary" style={{ fontSize: 12 }}>{formatSize(size)}</Text>,
    },
    {
      title: '修改时间', dataIndex: 'mtime', width: 160, responsive: ['md' as const],
      render: (mtime: number) => mtime ? new Date(mtime * 1000).toLocaleString('zh-CN', { hour12: false }) : '-',
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Title level={4} style={{ margin: 0 }}>{t('system.fileBrowser')}</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => fetchDir(path)}>{t('common.refresh')}</Button>
        </Space>
      </div>

      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Button size="small" icon={<HomeOutlined />} onClick={() => setPath('/')}>/</Button>
        <Button size="small" icon={<ArrowLeftOutlined />} onClick={goUp} disabled={path === '/'}>{t('system.parentDir')}</Button>
        <Breadcrumb
          style={{ fontSize: 13 }}
          items={[
            { title: <Button type="link" size="small" onClick={() => setPath('/')} style={{ padding: 0 }}>/</Button> },
            ...breadcrumbParts.map((part, idx) => ({
              title: (
                <Button type="link" size="small"
                  onClick={() => setPath('/' + breadcrumbParts.slice(0, idx + 1).join('/'))}
                  style={{ padding: 0 }}>
                  {part}
                </Button>
              ),
            })),
          ]}
        />
      </div>

      {error && <Text type="danger" style={{ display: 'block', marginBottom: 8 }}>{error}</Text>}

      <div style={{ overflowX: 'auto' }}>
        <Table
          dataSource={entries}
          columns={columns}
          rowKey="name"
          size="small"
          loading={loading}
          scroll={{ x: 900 }}
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], showTotal: (n: number) => t('common.items', { count: n }) }}
          locale={{ emptyText: t('system.emptyDir') }}
          onRow={(record) => ({
            onDoubleClick: () => record.is_dir ? navigateTo(record) : openFile(record),
            style: { cursor: 'pointer' },
          })}
        />
      </div>

      <Modal
        title={viewFile}
        open={!!viewFile}
        onCancel={() => { setViewFile(null); setViewContent(''); }}
        footer={[
          <Button key="download" icon={<DownloadOutlined />}
            onClick={() => {
              const token = getToken();
              window.open(`/api/v1/files/download?path=${encodeURIComponent(viewFilePath)}&token=${token}`, '_blank');
            }}>{t('common.download')}</Button>,
          <Button key="close" onClick={() => { setViewFile(null); setViewContent(''); }}>{t('common.close')}</Button>,
        ]}
        width="90%"
        style={{ top: 20 }}
      >
        {viewLoading ? <Spin /> : (
          <pre style={{
            maxHeight: '70vh', overflow: 'auto', background: '#1e1e1e', color: '#d4d4d4',
            padding: 12, borderRadius: 6, fontSize: 12, fontFamily: 'monospace',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
          }}>
            {viewContent}
          </pre>
        )}
      </Modal>
    </div>
  );
}
