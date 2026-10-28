import { useEffect, useState } from 'react';
import { Form, Input, Button, message, Segmented, Typography } from 'antd';
import { UserOutlined, LockOutlined, ReadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../hooks/useAuth';
import { useNavigate } from 'react-router-dom';
import api from '../utils/api';
import { getUser } from '../utils/auth';

const { Title } = Typography;

export default function LoginPage({ registerMode = false }: { registerMode?: boolean }) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [smsReady, setSmsReady] = useState(false);
  const [inviteEnabled, setInviteEnabled] = useState(false);
  const [registrationMethod, setRegistrationMethod] = useState<'invite' | 'sms'>('invite');
  const [sending, setSending] = useState(false);
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    if (registerMode) api.get('/membership/catalog').then(({ data }) => {
      setSmsReady(data.sms_ready); setInviteEnabled(data.invite_enabled);
      setRegistrationMethod(data.invite_enabled ? 'invite' : 'sms');
    }).catch(() => { setSmsReady(false); setInviteEnabled(false); });
  }, [registerMode]);

  useEffect(() => {
    if (!countdown) return;
    const timer = window.setTimeout(() => setCountdown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown]);

  const sendCode = async () => {
    try { await form.validateFields(['phone']); } catch { return; }
    setSending(true);
    try {
      const { data } = await api.post('/auth/sms-code', { phone: form.getFieldValue('phone') }, { timeout: 30000 });
      setCountdown(data.retry_after || 60); message.success('验证码已发送');
    } catch (error: unknown) {
      const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '短信发送失败，请稍后重试');
    } finally { setSending(false); }
  };

  const onFinish = async (values: { username: string; password: string; phone?: string; verification_code?: string; invitation_code?: string }) => {
    setLoading(true);
    try {
      if (registerMode) await api.post('/auth/register', {
        username: values.username, password: values.password,
        ...(registrationMethod === 'invite' ? { invitation_code: values.invitation_code?.trim() } : { phone: values.phone, verification_code: values.verification_code }),
      });
      await login(values.username, values.password);
      message.success(t('auth.loginSuccess'));
      navigate(getUser()?.is_admin ? '/' : '/m', { replace: true });
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (typeof detail === 'string') message.error(detail);
      else if (Array.isArray(detail)) message.error(detail.map((d: any) => d.msg).join('; '));
      else message.error(t('auth.loginFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="ef-auth">
      <div className="ef-auth-art"><div className="ef-auth-brand"><ReadOutlined /><span>NASPILOT · LEARNING</span><h1>EnglishFlow</h1></div></div>
      <section className="ef-auth-form">
        <span className="ef-eyebrow">{registerMode ? 'YOUR NEXT CHAPTER' : 'WELCOME BACK'}</span>
        <Title level={3}>{registerMode ? '开始你的学习旅程' : '欢迎回来'}</Title>
        {registerMode && <p className="ef-context-status" style={{ marginBottom: 20 }}>{registrationMethod === 'invite' ? '填写管理员发放的邀请码，开启 3 天试用。' : '验证手机号，开启 3 天试用。每个手机号仅限一次。'}</p>}
        {registerMode && inviteEnabled && smsReady && <Segmented style={{ marginBottom: 20 }} block value={registrationMethod} onChange={(value) => setRegistrationMethod(value as 'invite' | 'sms')} options={[{ value: 'invite', label: '邀请码注册' }, { value: 'sms', label: '手机验证码' }]} />}
        {registerMode && !smsReady && !inviteEnabled && <p className="ef-notice">注册暂未开放，请联系管理员。</p>}
        <Form key={registerMode ? 'register' : 'login'} form={form} name="login" onFinish={onFinish} size="large">
          {registerMode && registrationMethod === 'invite' && <Form.Item name="invitation_code" rules={[{ required: true, message: '请输入邀请码' }]}>
            <Input aria-label="邀请码" autoComplete="off" placeholder="管理员发放的邀请码" />
          </Form.Item>}
          {registerMode && registrationMethod === 'sms' && <>
            <Form.Item name="phone" rules={[{ required: true, message: '请输入手机号' }, { pattern: /^(?:\+86)?1[3-9]\d{9}$/, message: '请输入有效的中国大陆手机号' }]}>
              <Input aria-label="手机号" autoComplete="tel" inputMode="tel" placeholder="手机号（+86）" />
            </Form.Item>
            <div className="ef-sms-row"><Form.Item name="verification_code" rules={[{ required: true, message: '请输入验证码' }, { pattern: /^\d{6}$/, message: '验证码为 6 位数字' }]}>
              <Input aria-label="短信验证码" autoComplete="one-time-code" inputMode="numeric" maxLength={6} placeholder="短信验证码" />
            </Form.Item><Button disabled={!smsReady || countdown > 0} loading={sending} onClick={() => void sendCode()}>{countdown ? `${countdown}s` : '获取验证码'}</Button></div>
          </>}
          <Form.Item name="username" rules={[
            { required: true, message: t('common.pleaseInput') },
            ...(registerMode ? [{ pattern: /^[A-Za-z0-9_][A-Za-z0-9_.-]{2,63}$/, message: '用户名为 3–64 位字母、数字、下划线、点或短横线' }] : []),
          ]}>
            <Input autoComplete="username" prefix={<UserOutlined />} placeholder={t('auth.username')} />
          </Form.Item>
          <Form.Item name="password" rules={[
            { required: true, message: t('common.pleaseInput') },
            ...(registerMode ? [{ min: 8, max: 64, message: '密码为 8–64 位' }] : []),
          ]}>
            <Input.Password autoComplete={registerMode ? 'new-password' : 'current-password'} prefix={<LockOutlined />} placeholder={t('auth.password')} />
          </Form.Item>
          {registerMode && (
            <Form.Item name="confirmPassword" dependencies={['password']} rules={[
              { required: true, message: '请再次输入密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  return value === getFieldValue('password') ? Promise.resolve() : Promise.reject(new Error('两次密码不一致'));
                },
              }),
            ]}>
              <Input.Password autoComplete="new-password" prefix={<LockOutlined />} placeholder="确认密码" />
            </Form.Item>
          )}
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} disabled={registerMode && !(registrationMethod === 'invite' ? inviteEnabled : smsReady)} block>{registerMode ? '注册并开始学习' : t('auth.login')}</Button>
          </Form.Item>
        </Form>
        <Button type="link" block onClick={() => navigate(registerMode ? '/login' : '/register')}>
          {registerMode ? '已有账号，登录' : '创建学习账号'}
        </Button>
      </section>
    </div>
  );
}
