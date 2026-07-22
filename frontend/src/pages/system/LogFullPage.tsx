import { useSearchParams } from 'react-router-dom';
import { Typography } from 'antd';
import LogViewer from '../../components/LogViewer';

const { Title } = Typography;

export default function LogFullPage() {
  const [params] = useSearchParams();
  const source = params.get('source') || undefined;

  return (
    <div style={{ padding: '0 8px', height: '100vh', display: 'flex', flexDirection: 'column', background: '#f5f5f5' }}>
      <Title level={5} style={{ margin: '8px 0' }}>
        📡 实时日志 {source ? `· ${source}` : '· 全部'}
      </Title>
      <div style={{ flex: 1, minHeight: 0 }}>
        <LogViewer
          source={source}
          maxHeight={window.innerHeight - 70}
          maxLines={20000}
          placeholder="连接中... 等待日志产生"
          showFilters
        />
      </div>
    </div>
  );
}
