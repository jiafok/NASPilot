import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Col, Input, InputNumber, Row, Space, Spin, Switch, Tag, Typography, App } from 'antd';
import { SaveOutlined } from '@ant-design/icons';
import api from '../../utils/api';
import InvitationManager from './InvitationManager';

const PLAN_NAMES: Record<string, string> = { monthly: '包月', quarterly: '包季', yearly: '包年', lifetime: '永久' };

interface Settings {
  invite_enabled: boolean;
  sms_enabled: boolean;
  sms_access_key_id: string;
  sms_sign_name: string;
  sms_template_code: string;
  sms_secret_configured: boolean;
  contact_text: string;
  contact_url: string;
  plans: Record<string, { enabled: boolean; price: string }>;
}

export default function AccessSettings() {
  const { message } = App.useApp();
  const [config, setConfig] = useState<Settings | null>(null);
  const [secret, setSecret] = useState('');
  const [clearSecret, setClearSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.get<Settings>('/membership/admin/settings').then(({ data }) => setConfig(data)).catch(() => setError('配置加载失败，请刷新页面重试'));
  }, []);

  const save = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const { sms_secret_configured: _configured, ...values } = config;
      const { data } = await api.put<Settings>('/membership/admin/settings', {
        ...values, sms_access_key_secret: secret || null, clear_sms_secret: clearSecret,
      });
      setConfig(data); setSecret(''); setClearSecret(false);
      message.success('注册与套餐配置已保存');
    } catch (failure: unknown) {
      const detail = (failure as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '配置无效，请检查价格、联系方式和短信配置');
    } finally { setSaving(false); }
  };

  if (error) return <Alert type="error" message={error} />;
  if (!config) return <Spin />;

  const field = (label: string, value: string, key: 'sms_access_key_id' | 'sms_sign_name' | 'sms_template_code' | 'contact_url') => (
    <Col xs={24} md={12}><label style={{ display: 'block', marginBottom: 8 }}>{label}</label><Input aria-label={label} value={value} onChange={(event) => setConfig({ ...config, [key]: event.target.value })} style={{ marginBottom: 16 }} /></Col>
  );

  return (
    <div>
      <Typography.Title level={5}>邀请码注册（无需短信服务）</Typography.Title>
      <Space style={{ marginBottom: 16 }}><span>启用邀请码注册</span><Switch aria-label="启用邀请码注册" checked={config.invite_enabled} onChange={(value) => setConfig({ ...config, invite_enabled: value })} /></Space>
      <InvitationManager />
      <Alert type="info" message="每码只能注册一次；无需手机号。邀请码注册不能保证一人一号，请仅向可信用户发放。" style={{ marginBottom: 24 }} />
      <Typography.Title level={5}>手机号注册（可选）</Typography.Title>
      <Alert type="info" showIcon message="选择手机验证码注册时，需验证中国大陆手机号（+86），每个号码只能注册一次；删除账号不会释放号码重新领取试用。邀请码注册无需手机号。现有账号保留原访问权限。" style={{ marginBottom: 20 }} />
      <Space style={{ marginBottom: 20 }}><span>启用阿里云短信</span><Switch checked={config.sms_enabled} onChange={(value) => setConfig({ ...config, sms_enabled: value })} /><Tag>验证码 5 分钟有效</Tag></Space>
      <Row gutter={[16, 0]}>
        {field('AccessKey ID', config.sms_access_key_id, 'sms_access_key_id')}
        <Col xs={24} md={12}>
          <label style={{ display: 'block', marginBottom: 8 }}>AccessKey Secret</label>
          <Input.Password aria-label="AccessKey Secret" value={secret} autoComplete="new-password" placeholder={config.sms_secret_configured ? '已配置，留空保留现有密钥' : '未配置'} onChange={(event) => setSecret(event.target.value)} />
          <Checkbox checked={clearSecret} onChange={(event) => setClearSecret(event.target.checked)} style={{ margin: '8px 0 16px' }}>清除已保存的短信密钥</Checkbox>
        </Col>
        {field('短信签名', config.sms_sign_name, 'sms_sign_name')}
        {field('验证码模板 CODE（模板变量为 code）', config.sms_template_code, 'sms_template_code')}
      </Row>
      <Typography.Title level={5}>试用与套餐价格</Typography.Title>
      <Space style={{ marginBottom: 16 }}><span>新用户试用</span><InputNumber value={3} disabled style={{ width: 70 }} /><span>天</span><Tag color="green">管理员不受期限限制</Tag></Space>
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {Object.entries(config.plans).map(([key, plan]) => (
          <Col key={key} xs={24} sm={12} lg={6}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space><strong>{PLAN_NAMES[key]}</strong><Switch aria-label={`启用${PLAN_NAMES[key]}`} checked={plan.enabled} onChange={(enabled) => setConfig({ ...config, plans: { ...config.plans, [key]: { ...plan, enabled } } })} /></Space>
              <InputNumber aria-label={`${PLAN_NAMES[key]}价格`} stringMode precision={2} min="0" max="999999" prefix="¥" value={plan.price} onChange={(value) => setConfig({ ...config, plans: { ...config.plans, [key]: { ...plan, price: value || '0.00' } } })} style={{ width: '100%' }} />
            </Space>
          </Col>
        ))}
      </Row>
      <Alert type="warning" showIcon message="当前为人工收款、管理员确认开通；不是在线支付。请确认收款后在用户管理中授予套餐，价格变更不影响已开通记录。" style={{ marginBottom: 20 }} />
      <Typography.Title level={5}>到期联系管理员</Typography.Title>
      <Input.TextArea aria-label="管理员联系方式" placeholder="例如微信号、联系电话或付款说明" rows={3} value={config.contact_text} onChange={(event) => setConfig({ ...config, contact_text: event.target.value })} style={{ marginBottom: 16 }} />
      <Row gutter={16}>{field('联系链接（可选，http/https）', config.contact_url, 'contact_url')}</Row>
      <Button type="primary" loading={saving} icon={<SaveOutlined />} onClick={() => void save()}>保存注册与套餐配置</Button>
    </div>
  );
}