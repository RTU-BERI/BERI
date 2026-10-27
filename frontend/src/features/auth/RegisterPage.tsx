import {useState} from 'react';
import type {FormEvent} from 'react';
import {Link, Navigate, useNavigate} from 'react-router-dom';
import {ApiError} from '../../shared/api/client';
import {authApi} from './api';
import {useAuth} from './AuthProvider';
import {FormFeedback} from './FormFeedback';

export function RegisterPage() {
  const auth = useAuth(); const navigate = useNavigate();
  const [phone, setPhone] = useState(''); const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState<ApiError | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(null);
    if (password !== confirm) {setError(new ApiError('validation_error', 'Пароли не совпадают.')); return;}
    setBusy(true);
    try {await authApi.register({phone, password, password_confirm: confirm}); setPassword(''); setConfirm(''); navigate('/login', {replace: true, state: {registered: true}});}
    catch (e) {setError(e instanceof ApiError ? e : new ApiError('unknown', 'Не удалось зарегистрироваться.'));}
    finally {setBusy(false);}
  }
  if (auth.authenticated) return <Navigate to="/login" replace/>;
  if (auth.status === 'loading') return <section className="panel"><p role="status">Проверяем сессию…</p>{auth.loadError && <><p role="alert">{auth.loadError}</p><button onClick={() => void auth.refresh()}>Повторить проверку</button></>}</section>;
  return <section className="panel"><span className="eyebrow">НАЧНЁМ ЗНАКОМСТВО</span><h1>Создать аккаунт</h1><p className="intro">Один номер — один аккаунт.</p><div className="feedback small">Локальная демонстрация: SMS не отправляется, телефон не считается подтверждённым.</div>
    <FormFeedback error={error}/><form onSubmit={submit}>
      <label>Телефон<input type="tel" autoComplete="username" required value={phone} onChange={e => setPhone(e.target.value)} placeholder="+7 (999) 123-45-67"/></label>
      <label>Пароль<input type="password" autoComplete="new-password" required value={password} onChange={e => setPassword(e.target.value)}/></label>
      <label>Повторите пароль<input type="password" autoComplete="new-password" required value={confirm} onChange={e => setConfirm(e.target.value)}/></label>
      <button disabled={busy}>{busy ? 'Создаём аккаунт…' : 'Зарегистрироваться'}</button>
    </form><p className="switch">Уже с нами? <Link to="/login">Войти</Link></p></section>;
}
