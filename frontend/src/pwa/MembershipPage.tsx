import { useEffect, useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import api from '../utils/api';
import { ErrorState, PageHeader, SkeletonList } from './components';
import { CheckCircleOutlined, ClockCircleOutlined, LogoutOutlined } from '@ant-design/icons';

interface Catalog {
  trial_days: number;
  contact_text: string;
  contact_url: string;
  plans: Array<{ id: string; name: string; price: string; enabled: boolean }>;
}

const LABELS: Record<string, string> = { admin: '管理员', legacy: '原有账号', trial: '3 天试用', monthly: '包月', quarterly: '包季', yearly: '包年', lifetime: '永久' };

export default function MembershipPage() {
  const { logout } = useAuth();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [status, setStatus] = useState<{ can_learn: boolean; plan: string; expires_at: string | null } | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    setError('');
    Promise.all([api.get<Catalog>('/membership/catalog'), api.get('/membership/status')])
      .then(([plans, membership]) => { setCatalog(plans.data); setStatus(membership.data); })
      .catch(() => setError('会员信息加载失败'));
  }, [refresh]);

  return (
    <div className="ef-app"><div className="ef-page">
      <PageHeader title="学习权益" eyebrow="KEEP YOUR LEARNING GOING" symbol={<ClockCircleOutlined />} />
      {error ? <ErrorState message={error} onRetry={() => setRefresh((value) => value + 1)} /> : !catalog || !status ? <SkeletonList rows={3} /> : <>
        <div className="ef-membership-state">
          <strong>{status.can_learn ? LABELS[status.plan] || status.plan : '学习权限已到期'}</strong>
          <p>{status.expires_at ? `有效期至 ${new Date(status.expires_at).toLocaleString()}` : '长期有效'}</p>
        </div>
        <div className="ef-section-head"><h2 className="ef-section-title">选择学习套餐</h2><span className="ef-result-count">人民币 / 元</span></div>
        <div className="ef-membership-plans">
          {catalog.plans.filter((plan) => plan.enabled).map((plan) => <article key={plan.id} className="ef-plan-option"><strong>{plan.name}</strong><span>¥ {Number(plan.price).toFixed(2)}</span><small>{plan.id === 'lifetime' ? '开通后长期有效' : '未到期续费顺延有效期'}</small></article>)}
        </div>
        {!catalog.plans.some((plan) => plan.enabled) && <p className="ef-notice">套餐价格尚未公布，请联系管理员。</p>}
        <section className="ef-section" style={{ marginTop: 28 }}>
          <h2 className="ef-section-title">联系管理员开通</h2>
          <p className="ef-contact-text">{catalog.contact_text || '管理员尚未填写联系方式，请联系站点提供者。'}</p>
          <p className="ef-context-status">当前为人工收款，管理员确认后开通；本页面不会自动扣费。</p>
          {catalog.contact_url && <a className="ef-btn" href={catalog.contact_url} target="_blank" rel="noopener noreferrer">联系管理员</a>}
        </section>
        <div className="ef-btn-row">
          <button type="button" className="ef-btn" onClick={async () => { try { const { data } = await api.get('/membership/status'); setStatus(data); if (data.can_learn) window.location.href = '/m'; } catch { setError('开通状态检查失败，请重试'); } }}><CheckCircleOutlined /> 检查开通状态</button>
          {status.can_learn && <button type="button" className="ef-btn" data-kind="primary" onClick={() => { window.location.href = '/m'; }}>返回学习</button>}
        </div>
      </>}
      <button type="button" className="ef-context-link" onClick={logout} style={{ marginTop: 24 }}><LogoutOutlined /> 退出登录</button>
    </div></div>
  );
}